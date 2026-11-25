from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q, Sum
from django.urls import reverse
from django.utils import timezone

from apps.core.utils import audit
from apps.notifications.models import notify
from apps.orders import upi
from apps.orders.models import Order, PaymentTransaction

from .models import DepositRequest, TopupTransaction

MAX_PENDING_DEPOSITS = 3


class DepositError(Exception):
    pass


def topup_balance(user) -> Decimal:
    agg = TopupTransaction.objects.filter(member=user).aggregate(
        credit=Sum("amount", filter=Q(transaction_type=TopupTransaction.CREDIT)),
        debit=Sum("amount", filter=Q(transaction_type=TopupTransaction.DEBIT)),
    )
    return (agg["credit"] or Decimal("0")) - (agg["debit"] or Decimal("0"))


def _lock_user(user):
    # Serialises all top-up wallet writes for one member.
    return get_user_model().objects.select_for_update().get(pk=user.pk)


def _admins():
    return get_user_model().objects.filter(Q(is_superuser=True) | Q(role="admin"), is_active=True)


@transaction.atomic
def credit(user, amount, reference, description=""):
    _lock_user(user)
    tx, _ = TopupTransaction.objects.get_or_create(
        reference=reference,
        defaults={"member": user, "transaction_type": TopupTransaction.CREDIT, "amount": amount, "description": description},
    )
    return tx


@transaction.atomic
def debit(user, amount, reference, description=""):
    _lock_user(user)
    existing = TopupTransaction.objects.filter(reference=reference).first()
    if existing:
        return existing
    if topup_balance(user) < amount:
        raise DepositError("Insufficient top-up wallet balance.")
    return TopupTransaction.objects.create(
        member=user, transaction_type=TopupTransaction.DEBIT, amount=amount, reference=reference, description=description
    )


def parse_amount(value) -> Decimal:
    try:
        amount = Decimal(str(value).strip()).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        raise DepositError("Enter a valid amount.")
    if not amount.is_finite() or amount < settings.DEPOSIT_MIN or amount > settings.DEPOSIT_MAX:
        raise DepositError(f"Deposit amount must be between ₹{settings.DEPOSIT_MIN} and ₹{settings.DEPOSIT_MAX}.")
    return amount


@transaction.atomic
def submit_deposit(user, amount, utr) -> DepositRequest:
    """Member paid via the UPI QR; record it for an admin to verify."""
    _lock_user(user)
    amount = parse_amount(amount)
    utr = upi.clean_utr(utr)
    if not upi.UTR_RE.match(utr):
        raise DepositError("Enter the 12-digit UTR / UPI reference number shown in your UPI app.")
    if DepositRequest.objects.filter(member=user, status=DepositRequest.PENDING).count() >= MAX_PENDING_DEPOSITS:
        raise DepositError(f"You already have {MAX_PENDING_DEPOSITS} deposits waiting for approval. Please wait.")
    if DepositRequest.objects.filter(utr=utr).exists() or PaymentTransaction.objects.filter(gateway_reference=utr).exists():
        raise DepositError("This UTR has already been submitted.")
    dep = DepositRequest.objects.create(member=user, amount=amount, utr=utr)
    audit(user, "deposit.submitted", dep, amount=amount, utr=utr)
    link = reverse("deposits:admin")
    for admin in _admins():
        notify(admin, "Deposit to approve", f"{user.member_id}: ₹{amount}, UTR {utr}.", link)
    return dep


@transaction.atomic
def approve_deposit(dep: DepositRequest, actor, note="") -> DepositRequest:
    dep = DepositRequest.objects.select_for_update().get(pk=dep.pk)
    if dep.status != DepositRequest.PENDING:
        raise DepositError("This deposit has already been processed.")
    credit(dep.member, dep.amount, f"deposit:{dep.pk}", f"Deposit {dep.deposit_no} (UTR {dep.utr})")
    dep.status, dep.admin_note, dep.reviewed_by, dep.reviewed_at = DepositRequest.APPROVED, note, actor, timezone.now()
    dep.save()
    audit(actor, "deposit.approved", dep, amount=dep.amount, utr=dep.utr)
    notify(dep.member, "Deposit approved", f"₹{dep.amount} has been added to your top-up wallet.", reverse("deposits:home"))
    return dep


@transaction.atomic
def reject_deposit(dep: DepositRequest, actor, note="") -> DepositRequest:
    dep = DepositRequest.objects.select_for_update().get(pk=dep.pk)
    if dep.status != DepositRequest.PENDING:
        raise DepositError("This deposit has already been processed.")
    dep.status, dep.admin_note, dep.reviewed_by, dep.reviewed_at = DepositRequest.REJECTED, note, actor, timezone.now()
    dep.save()
    audit(actor, "deposit.rejected", dep, amount=dep.amount, utr=dep.utr, note=note)
    notify(
        dep.member, "Deposit not approved",
        f"We could not verify your ₹{dep.amount} deposit (UTR {dep.utr}). {note}".strip(), reverse("deposits:home"),
    )
    return dep


@transaction.atomic
def pay_order_from_topup(order: Order, user) -> Order:
    """Pay a pending order with top-up balance, then run the normal paid-order flow."""
    from apps.orders import services as order_services

    order = Order.objects.select_for_update().get(pk=order.pk)
    if order.customer_id != user.pk or order.status != Order.PENDING:
        raise DepositError("This order is not awaiting payment.")
    if order.payments.filter(gateway="upi", status="initiated").exists():
        raise DepositError("A UPI payment for this order is already being verified.")
    debit(user, order.total_amount, f"order:{order.pk}", f"Payment for order {order.order_number}")
    return order_services.mark_paid(order, gateway="topup", gateway_reference=f"topup_{order.order_number}", actor=user)


def refund_order_to_topup(order: Order):
    """Return the money to the top-up wallet when an order paid from it is refunded."""
    if order.payments.filter(gateway="topup").exists():
        credit(order.customer, order.total_amount, f"refund:order:{order.pk}", f"Refund for order {order.order_number}")


# --- USDT (BEP-20) -----------------------------------------------------------

USDT_QR_MINUTES = 60
USDT_CLAIM_HOURS = 24        # a paid request can be verified up to this long after it was created
MAX_OPEN_USDT_REQUESTS = 5


def usdt_enabled() -> bool:
    from . import bsc

    return bool(bsc.ADDRESS_RE.match(settings.USDT_BEP20_ADDRESS or ""))


def create_usdt_request(user, inr_amount):
    """Create (or reuse) a USDT payment request with a unique amount for this member."""
    import secrets
    from datetime import timedelta
    from decimal import ROUND_UP

    from .models import CryptoPaymentRequest

    if not usdt_enabled():
        raise DepositError("USDT deposits are not available.")
    inr = parse_amount(inr_amount)
    now = timezone.now()
    open_qs = CryptoPaymentRequest.objects.filter(member=user, deposit__isnull=True, expires_at__gt=now)
    existing = open_qs.filter(inr_amount=inr, to_address=settings.USDT_BEP20_ADDRESS).first()
    if existing:
        return existing
    if open_qs.count() >= MAX_OPEN_USDT_REQUESTS:
        raise DepositError("Too many open USDT requests. Use one of your existing QR codes or wait for them to expire.")
    rate = settings.USDT_INR_RATE
    base = (inr / rate).quantize(Decimal("0.01"), rounding=ROUND_UP)
    recent = now - timedelta(hours=USDT_CLAIM_HOURS)
    for _ in range(50):
        usdt = base + Decimal(secrets.randbelow(9999) + 1) / Decimal(1_000_000)
        if not CryptoPaymentRequest.objects.filter(usdt_amount=usdt, created_at__gte=recent).exists():
            return CryptoPaymentRequest.objects.create(
                member=user, inr_amount=inr, usdt_amount=usdt, rate=rate, to_address=settings.USDT_BEP20_ADDRESS,
                expires_at=now + timedelta(minutes=USDT_QR_MINUTES),
            )
    raise DepositError("Could not create a payment request. Please try again.")


def claim_usdt_deposit(user, request_id, txid) -> DepositRequest:
    """Verify the TxID on BSC and credit the top-up wallet instantly."""
    from datetime import timedelta

    from django.db import IntegrityError

    from . import bsc
    from .models import CryptoPaymentRequest

    txid = (txid or "").strip().lower()
    req = CryptoPaymentRequest.objects.filter(pk=request_id, member=user).first()
    if req is None:
        raise DepositError("Payment request not found. Please generate a new QR code.")
    if req.deposit_id:
        raise DepositError("This payment has already been verified.")
    if timezone.now() > req.created_at + timedelta(hours=USDT_CLAIM_HOURS):
        raise DepositError("This payment request is too old. Contact support with your TxID.")
    if DepositRequest.objects.filter(utr__iexact=txid).exists():
        raise DepositError("This TxID has already been used.")

    # Network call outside the DB transaction so no row locks are held while waiting.
    try:
        info = bsc.verify_usdt_transfer(txid, req.to_address, req.usdt_amount, int(req.created_at.timestamp()))
    except bsc.BscUnavailable:
        raise DepositError("Could not reach the blockchain right now. Please try again in a minute.")
    except (bsc.BscError, bsc.BscPending) as e:
        raise DepositError(str(e))
    if info["timestamp"] > int((req.expires_at + timedelta(minutes=5)).timestamp()):
        raise DepositError("This payment was sent after the QR expired. Contact support with your TxID.")

    try:
        with transaction.atomic():
            req = CryptoPaymentRequest.objects.select_for_update().get(pk=req.pk)
            if req.deposit_id:
                raise DepositError("This payment has already been verified.")
            dep = DepositRequest.objects.create(
                member=user, method=DepositRequest.USDT, amount=req.inr_amount, crypto_amount=req.usdt_amount,
                utr=txid, status=DepositRequest.APPROVED, reviewed_at=timezone.now(),
                admin_note=f"Auto-verified on BSC (block {info['block']}, from {info['from']})",
            )
            req.deposit = dep
            req.save(update_fields=["deposit"])
            credit(user, dep.amount, f"deposit:{dep.pk}", f"USDT deposit {dep.deposit_no} ({req.usdt_amount} USDT)")
            audit(user, "deposit.usdt_verified", dep, usdt=req.usdt_amount, inr=req.inr_amount, txid=txid, sender=info["from"])
    except IntegrityError:
        raise DepositError("This TxID has already been used.")
    notify(user, "Deposit successful", f"₹{dep.amount} ({req.usdt_amount} USDT) added to your top-up wallet.", reverse("deposits:home"))
    return dep


# --- Razorpay (instant) ------------------------------------------------------

def razorpay_enabled() -> bool:
    from apps.orders import razorpay

    return razorpay.is_configured()


def start_razorpay_deposit(user, amount):
    """Create a Razorpay order for a deposit. Returns our RazorpayDepositOrder."""
    from apps.orders import razorpay

    from .models import RazorpayDepositOrder

    if not razorpay_enabled():
        raise DepositError("Online payment is not available.")
    amount = parse_amount(amount)
    try:
        data = razorpay.create_order(amount, f"dep-{user.member_id}-{int(timezone.now().timestamp())}",
                                     {"purpose": "topup_deposit", "member_id": user.member_id})
    except razorpay.RazorpayError:
        raise DepositError("Could not start the payment. Please try again.")
    return RazorpayDepositOrder.objects.create(member=user, amount=amount, gateway_order_id=data["id"])


def complete_razorpay_deposit(gateway_order_id, payment_id, raw=None, paid_paise=None):
    """Credit the top-up wallet for a verified Razorpay payment. Idempotent: safe to call
    from both the browser callback and the webhook. Returns the DepositRequest or None."""
    from django.db import IntegrityError

    from apps.orders import razorpay

    from .models import RazorpayDepositOrder

    with transaction.atomic():
        rdo = RazorpayDepositOrder.objects.select_for_update().filter(gateway_order_id=gateway_order_id).first()
        if rdo is None:
            return None
        if rdo.deposit_id:
            return rdo.deposit
        if paid_paise is not None and int(paid_paise) != razorpay.to_paise(rdo.amount):
            audit(None, "deposit.razorpay_amount_mismatch", rdo.member, order=gateway_order_id, paid=paid_paise)
            raise DepositError("Payment amount does not match.")
        try:
            with transaction.atomic():
                dep = DepositRequest.objects.create(
                    member=rdo.member, method=DepositRequest.RAZORPAY, amount=rdo.amount, utr=payment_id,
                    status=DepositRequest.APPROVED, reviewed_at=timezone.now(), admin_note=f"Razorpay order {gateway_order_id}",
                )
        except IntegrityError:
            return DepositRequest.objects.filter(utr=payment_id).first()
        rdo.deposit = dep
        rdo.save(update_fields=["deposit"])
        credit(rdo.member, dep.amount, f"deposit:{dep.pk}", f"Online deposit {dep.deposit_no} (Razorpay {payment_id})")
        audit(rdo.member, "deposit.razorpay_paid", dep, amount=dep.amount, payment_id=payment_id, order=gateway_order_id)
    notify(rdo.member, "Deposit successful", f"₹{dep.amount} has been added to your top-up wallet.", reverse("deposits:home"))
    return dep
