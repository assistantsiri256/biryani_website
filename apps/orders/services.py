import uuid
from decimal import Decimal

from django.db import transaction
from django.db.models import F, Q
from django.urls import reverse
from django.utils import timezone

from apps.core.utils import audit
from apps.notifications.models import notify
from apps.products.models import Product

from .models import Order, OrderItem, PaymentTransaction


class CheckoutError(Exception):
    pass


def _order_number():
    return f"ORD{timezone.now():%y%m%d}{uuid.uuid4().hex[:6].upper()}"


@transaction.atomic
def create_order(user, cart: dict, shipping_address="") -> Order:
    """cart = {product_id(str): quantity(int)}"""
    if not cart:
        raise CheckoutError("Your cart is empty.")
    products = {str(p.pk): p for p in Product.objects.select_for_update().filter(pk__in=cart.keys(), is_active=True)}
    order = Order.objects.create(customer=user, order_number=_order_number(), shipping_address=shipping_address)
    total, volume = Decimal("0"), Decimal("0")
    for pid, qty in cart.items():
        p = products.get(pid)
        if p is None:
            raise CheckoutError("A product in your cart is no longer available.")
        if qty < 1:
            raise CheckoutError("Invalid quantity.")
        if p.product_type == Product.PHYSICAL and qty > p.stock:
            raise CheckoutError(f"Only {p.stock} unit(s) of {p.name} in stock.")
        OrderItem.objects.create(
            order=order, product=p, product_name=p.name, unit_price=p.price, unit_volume=p.sales_volume, quantity=qty
        )
        total += p.price * qty
        volume += p.sales_volume * qty
    order.total_amount, order.total_volume = total, volume
    order.save(update_fields=["total_amount", "total_volume"])
    return order


@transaction.atomic
def mark_paid(order: Order, gateway="demo", gateway_reference=None, actor=None, raw=None, payment=None):
    """Confirm payment, then run the commission engine.

    ``payment``: an existing "initiated" PaymentTransaction (e.g. a submitted UPI UTR)
    to mark successful instead of creating a new one.

    In production call this from the verified Razorpay/Cashfree webhook
    (after signature verification) – ideally via a Celery task.
    """
    from apps.commissions.tasks import dispatch_process_order

    order = Order.objects.select_for_update().get(pk=order.pk)
    # Idempotent: duplicate callbacks/webhooks for an already-paid order do nothing.
    if order.status != Order.PENDING:
        return order
    if payment is None and gateway_reference and PaymentTransaction.objects.filter(gateway_reference=gateway_reference).exists():
        return order
    for item in order.items.select_related("product"):
        if item.product.product_type == Product.DIGITAL:
            continue
        updated = Product.objects.filter(pk=item.product_id, stock__gte=item.quantity).update(stock=F("stock") - item.quantity)
        if not updated:
            # Money has already been captured, so never fail here: flag the oversell for the admin.
            Product.objects.filter(pk=item.product_id).update(stock=0)
            audit(None, "stock.oversold", item.product, order=order.order_number, quantity=item.quantity)
    if payment is not None:
        payment.status = "success"
        payment.save(update_fields=["status"])
    else:
        PaymentTransaction.objects.create(
            order=order, gateway=gateway, gateway_reference=gateway_reference or f"{gateway}_{uuid.uuid4().hex[:12]}",
            amount=order.total_amount, status="success", raw_response=raw or {},
        )
    order.status = Order.PAID
    order.paid_at = timezone.now()
    order.save(update_fields=["status", "paid_at"])
    audit(actor or order.customer, "order.paid", order, gateway=gateway, amount=order.total_amount)
    notify(order.customer, "Payment received", f"Order {order.order_number} is confirmed.", order.get_absolute_url())
    dispatch_process_order(order.pk)
    return order


def record_failed_payment(order: Order, gateway: str, gateway_reference: str, raw=None):
    PaymentTransaction.objects.get_or_create(
        gateway_reference=gateway_reference,
        defaults={"order": order, "gateway": gateway, "amount": order.total_amount, "status": "failed", "raw_response": raw or {}},
    )


@transaction.atomic
def cancel_order(order: Order, actor=None):
    order = Order.objects.select_for_update().get(pk=order.pk)
    if order.status != Order.PENDING:
        raise CheckoutError("Only pending orders can be cancelled.")
    if order.payments.filter(gateway="upi", status="initiated").exists():
        raise CheckoutError("A UPI payment for this order is being verified. Confirm or reject it first.")
    order.status = Order.CANCELLED
    order.save(update_fields=["status"])
    audit(actor, "order.cancel", order)


# --- UPI QR (manual confirmation) -------------------------------------------

def pending_upi_payment(order: Order):
    return order.payments.filter(gateway="upi", status="initiated").first()


@transaction.atomic
def submit_upi_payment(order: Order, utr: str, actor=None) -> PaymentTransaction:
    """Customer says they paid via the QR; record the UTR for an admin to confirm."""
    from django.contrib.auth import get_user_model

    from . import upi

    order = Order.objects.select_for_update().get(pk=order.pk)
    if order.status != Order.PENDING:
        raise CheckoutError("This order is not awaiting payment.")
    utr = upi.clean_utr(utr)
    if not upi.UTR_RE.match(utr):
        raise CheckoutError("Enter the 12-digit UTR / UPI reference number shown in your UPI app.")
    if pending_upi_payment(order):
        raise CheckoutError("A payment for this order is already waiting for confirmation.")
    if PaymentTransaction.objects.filter(gateway_reference=utr).exists():
        raise CheckoutError("This UTR has already been submitted.")
    txn = PaymentTransaction.objects.create(
        order=order, gateway="upi", gateway_reference=utr, amount=order.total_amount, status="initiated",
        raw_response={"utr": utr, "submitted_by": getattr(actor, "pk", None)},
    )
    audit(actor or order.customer, "payment.upi_submitted", order, utr=utr, amount=order.total_amount)
    admins = get_user_model().objects.filter(Q(is_superuser=True) | Q(role="admin"), is_active=True)
    link = reverse("reports:admin_orders") + "?status=upi"
    for admin in admins:
        notify(admin, "UPI payment to confirm", f"Order {order.order_number}: ₹{order.total_amount}, UTR {utr}.", link)
    return txn


@transaction.atomic
def confirm_upi_payment(order: Order, actor) -> Order:
    """Admin verified the UTR in the bank/UPI app: mark paid and run commissions."""
    order = Order.objects.select_for_update().get(pk=order.pk)
    txn = pending_upi_payment(order)
    if txn is None:
        raise CheckoutError("No UPI payment is waiting for confirmation on this order.")
    audit(actor, "payment.upi_confirmed", order, utr=txn.gateway_reference)
    return mark_paid(order, gateway="upi", gateway_reference=txn.gateway_reference, actor=actor, payment=txn)


@transaction.atomic
def reject_upi_payment(order: Order, actor) -> None:
    """Admin could not find the payment; the customer can submit a correct UTR."""
    order = Order.objects.select_for_update().get(pk=order.pk)
    txn = pending_upi_payment(order)
    if txn is None:
        raise CheckoutError("No UPI payment is waiting for confirmation on this order.")
    txn.status = "failed"
    txn.save(update_fields=["status"])
    audit(actor, "payment.upi_rejected", order, utr=txn.gateway_reference)
    notify(order.customer, "UPI payment not found",
           f"We could not verify UTR {txn.gateway_reference} for order {order.order_number}. "
           "Please check the number and submit it again.", order.get_absolute_url())
