import time

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from apps.core.permissions import admin_required
from apps.core.utils import audit
from apps.orders import upi
from apps.orders.models import Order

from . import services
from .models import CryptoPaymentRequest, DepositRequest


QR_VALID_SECONDS = 60 * 60
_SALT = "deposits.qr"
_INVALID = "Invalid deposit request. Please generate a new QR code."
_EXPIRED = "This QR code has expired. Please generate a new one."


def _issue_qr_token(user, amount):
    return signing.dumps({"u": user.pk, "a": str(amount), "t": int(time.time())}, salt=_SALT)


def _read_qr_token(token, user, amount) -> int:
    """Validate the token and return the seconds left before the QR expires."""
    try:
        data = signing.loads(token or "", salt=_SALT, max_age=QR_VALID_SECONDS)
    except signing.SignatureExpired:
        raise services.DepositError(_EXPIRED)
    except signing.BadSignature:
        raise services.DepositError(_INVALID)
    if data.get("u") != user.pk or data.get("a") != str(amount):
        raise services.DepositError(_INVALID)
    return max(0, QR_VALID_SECONDS - (int(time.time()) - int(data.get("t", 0))))


def _deposit_url(**params):
    from urllib.parse import urlencode

    return reverse("deposits:home") + ("?" + urlencode(params) if params else "")


@login_required
def deposit(request):
    """Deposit page: Razorpay (instant), UPI QR (admin verifies UTR) or USDT BEP-20 (auto-verified TxID)."""
    default = "razorpay" if services.razorpay_enabled() else ("upi" if upi.is_configured() else ("usdt" if services.usdt_enabled() else "upi"))
    method = request.GET.get("method") or request.POST.get("method") or default
    if method not in ("razorpay", "upi", "usdt"):
        method = default
    action = request.POST.get("action", "") if request.method == "POST" else ""

    # ---- USDT ----
    if action == "usdt_generate":
        try:
            req = services.create_usdt_request(request.user, request.POST.get("amount"))
            return redirect(_deposit_url(method="usdt", req=req.pk))
        except services.DepositError as e:
            messages.error(request, str(e))
            return redirect(_deposit_url(method="usdt"))
    if action == "usdt_verify":
        rid = request.POST.get("req", "")
        try:
            dep = services.claim_usdt_deposit(request.user, rid, request.POST.get("txid"))
            messages.success(request, f"Verified! ₹{dep.amount} has been added to your top-up wallet.")
            return redirect(_deposit_url(method="usdt"))
        except services.DepositError as e:
            messages.error(request, str(e))
            return redirect(_deposit_url(method="usdt", req=rid))

    amount, qr, uri, error, token, expires_in = None, "", "", "", "", QR_VALID_SECONDS
    usdt_req = None
    if method == "usdt":
        rid = request.GET.get("req")
        if rid and services.usdt_enabled():
            usdt_req = CryptoPaymentRequest.objects.filter(pk=rid if rid.isdigit() else 0, member=request.user).first()
            if usdt_req:
                expires_in = max(0, int((usdt_req.expires_at - timezone.now()).total_seconds()))
                qr = upi.svg_for(usdt_req.to_address)
    else:
        # ---- UPI ----
        raw = request.GET.get("amount")
        if request.method == "POST":
            token = request.POST.get("token", "")
            try:
                amount = services.parse_amount(request.POST.get("amount"))
                expires_in = _read_qr_token(token, request.user, amount)
            except services.DepositError as e:
                messages.error(request, str(e))
                return redirect("deposits:home")
            try:
                services.submit_deposit(request.user, amount, request.POST.get("utr", ""))
                messages.success(request, "Deposit submitted. It will be added to your top-up wallet after verification.")
                return redirect("deposits:home")
            except services.DepositError as e:
                # Wrong UTR etc.: keep showing the same QR with the remaining time.
                messages.error(request, str(e))
                raw = str(amount)
        if raw and upi.is_configured():
            try:
                amount = services.parse_amount(raw)
                uri = upi.uri_for(amount, f"Deposit {request.user.member_id}")
                qr = upi.svg_for(uri)
                if not token:
                    token = _issue_qr_token(request.user, amount)
            except services.DepositError as e:
                error, amount = str(e), None

    return render(request, "deposits/deposit.html", {
        "method": method,
        "upi_enabled": upi.is_configured(),
        "usdt_enabled": services.usdt_enabled(),
        "razorpay_enabled": services.razorpay_enabled(),
        "upi_id": settings.UPI_ID,
        "upi_payee": settings.UPI_PAYEE_NAME,
        "usdt_address": settings.USDT_BEP20_ADDRESS,
        "usdt_rate": settings.USDT_INR_RATE,
        "usdt_req": usdt_req,
        "amount": amount, "qr": qr, "uri": uri, "error": error,
        "token": token, "expires_in": expires_in,
        "min": settings.DEPOSIT_MIN, "max": settings.DEPOSIT_MAX,
        "quick_amounts": [500, 1000, 2000, 5000],
        "balance": services.topup_balance(request.user),
        "deposits": request.user.deposit_requests.all()[:20],
        "ledger": request.user.topup_transactions.all()[:20],
    })


@login_required
@require_POST
def pay_order(request, order_number):
    order = get_object_or_404(Order, order_number=order_number, customer=request.user)
    try:
        services.pay_order_from_topup(order, request.user)
        messages.success(request, "Paid from your top-up wallet. Thank you for your order!")
    except services.DepositError as e:
        messages.error(request, str(e))
    return redirect(order)


@admin_required
def admin_deposits(request):
    status = request.GET.get("status", DepositRequest.PENDING)
    qs = DepositRequest.objects.select_related("member", "reviewed_by")
    if status in dict(DepositRequest.STATUS):
        qs = qs.filter(status=status)
    return render(request, "deposits/admin_deposits.html", {
        "page": Paginator(qs, 25).get_page(request.GET.get("page")),
        "status": status, "statuses": DepositRequest.STATUS,
        "pending_count": DepositRequest.objects.filter(status=DepositRequest.PENDING).count(),
    })


@admin_required
@require_POST
def admin_action(request, pk):
    dep = get_object_or_404(DepositRequest, pk=pk)
    action, note = request.POST.get("action"), request.POST.get("note", "").strip()[:255]
    try:
        if action == "approve":
            services.approve_deposit(dep, request.user, note)
            messages.success(request, f"{dep.deposit_no} approved: ₹{dep.amount} added to {dep.member.member_id}'s top-up wallet.")
        elif action == "reject":
            services.reject_deposit(dep, request.user, note)
            messages.info(request, f"{dep.deposit_no} rejected; the member has been notified.")
    except services.DepositError as e:
        messages.error(request, str(e))
    return redirect("deposits:admin")


@login_required
@require_POST
def razorpay_start(request):
    """Create a Razorpay order for the deposit and return Checkout options as JSON."""
    try:
        rdo = services.start_razorpay_deposit(request.user, request.POST.get("amount"))
    except services.DepositError as e:
        return JsonResponse({"error": str(e)}, status=400)
    from apps.orders import razorpay

    return JsonResponse({
        "key": settings.RAZORPAY_KEY_ID,
        "order_id": rdo.gateway_order_id,
        "amount": razorpay.to_paise(rdo.amount),
        "currency": "INR",
        "name": "BinaryPro",
        "description": "Top-up wallet deposit",
        "prefill": {"name": request.user.get_full_name(), "email": request.user.email, "contact": request.user.mobile or ""},
    })


@login_required
@require_POST
def razorpay_callback(request):
    """Browser callback after Checkout. Nothing is credited unless the signature verifies."""
    from apps.orders import razorpay

    from .models import RazorpayDepositOrder

    gw_order_id = request.POST.get("razorpay_order_id", "")
    payment_id = request.POST.get("razorpay_payment_id", "")
    signature = request.POST.get("razorpay_signature", "")
    owned = RazorpayDepositOrder.objects.filter(gateway_order_id=gw_order_id, member=request.user).exists()
    if not owned or not razorpay.verify_payment_signature(gw_order_id, payment_id, signature):
        audit(request.user, "deposit.razorpay_signature_invalid", None, order=gw_order_id, payment_id=payment_id)
        messages.error(request, "Payment could not be verified. If money was debited, it will be credited automatically or refunded by Razorpay.")
        return redirect("deposits:home")
    try:
        dep = services.complete_razorpay_deposit(gw_order_id, payment_id, raw={"razorpay_payment_id": payment_id})
        messages.success(request, f"Payment successful! ₹{dep.amount} has been added to your top-up wallet.")
    except services.DepositError as e:
        messages.error(request, str(e))
    return redirect("deposits:home")
