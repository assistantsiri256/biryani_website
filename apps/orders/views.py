import json
import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.core.utils import audit
from apps.products.models import Product

from apps.deposits.services import topup_balance

from . import razorpay, services, upi
from .models import Order

logger = logging.getLogger("apps.orders")

CART_KEY = "cart"


def _cart(request) -> dict:
    return request.session.setdefault(CART_KEY, {})


@login_required
@require_POST
def cart_add(request, product_id):
    product = get_object_or_404(Product, pk=product_id, is_active=True)
    cart = _cart(request)
    qty = max(1, int(request.POST.get("quantity", 1) or 1))
    cart[str(product.pk)] = min(cart.get(str(product.pk), 0) + qty, product.max_quantity)
    request.session.modified = True
    messages.success(request, f"{product.name} added to cart.")
    return redirect(request.POST.get("next") or "orders:cart")


@login_required
@require_POST
def cart_update(request):
    cart = _cart(request)
    for key, value in request.POST.items():
        if key.startswith("qty_"):
            pid = key[4:]
            qty = int(value or 0)
            if qty <= 0:
                cart.pop(pid, None)
            else:
                cart[pid] = qty
    request.session.modified = True
    return redirect("orders:cart")


@login_required
def cart(request):
    cart = _cart(request)
    products = Product.objects.filter(pk__in=cart.keys())
    lines, total, volume = [], 0, 0
    for p in products:
        qty = cart[str(p.pk)]
        lines.append({"product": p, "qty": qty, "total": p.price * qty, "volume": p.sales_volume * qty})
        total += p.price * qty
        volume += p.sales_volume * qty
    return render(request, "orders/cart.html", {"lines": lines, "total": total, "volume": volume})


@login_required
@require_POST
def checkout(request):
    address = request.POST.get("shipping_address", "").strip()
    if not address:
        messages.error(request, "Please enter a shipping address.")
        return redirect("orders:cart")
    try:
        order = services.create_order(request.user, _cart(request), address)
    except services.CheckoutError as e:
        messages.error(request, str(e))
        return redirect("orders:cart")
    request.session[CART_KEY] = {}
    return redirect(order)


@login_required
def order_list(request):
    return render(request, "orders/list.html", {"orders": request.user.orders.all()})


@login_required
def order_detail(request, order_number):
    qs = Order.objects.all() if request.user.is_company_admin else request.user.orders.all()
    order = get_object_or_404(qs.prefetch_related("items", "payments"), order_number=order_number)
    upi_enabled = upi.is_configured() and order.status == Order.PENDING
    return render(request, "orders/detail.html", {
        "order": order,
        "upi_enabled": upi_enabled,
        "upi_id": settings.UPI_ID,
        "upi_payee": settings.UPI_PAYEE_NAME,
        "upi_qr": upi.qr_svg(order) if upi_enabled else "",
        "upi_uri": upi.payment_uri(order) if upi_enabled else "",
        "upi_pending": services.pending_upi_payment(order) if upi_enabled else None,
        "topup_balance": topup_balance(request.user) if order.status == Order.PENDING else None,
        "demo_payments": settings.PAYMENT_DEMO_MODE,
        "razorpay_enabled": razorpay.is_configured(),
        "razorpay_key_id": settings.RAZORPAY_KEY_ID,
    })


@login_required
@require_POST
def razorpay_start(request, order_number):
    """Create the Razorpay order and return Checkout options as JSON."""
    order = get_object_or_404(Order, order_number=order_number, customer=request.user, status=Order.PENDING)
    if not razorpay.is_configured():
        return JsonResponse({"error": "Payment gateway not configured."}, status=503)
    try:
        gw_id = razorpay.create_gateway_order(order)
    except razorpay.RazorpayError:
        logger.exception("Razorpay order creation failed for %s", order.order_number)
        return JsonResponse({"error": "Could not start payment. Please try again."}, status=502)
    return JsonResponse({
        "key": settings.RAZORPAY_KEY_ID,
        "order_id": gw_id,
        "amount": razorpay.to_paise(order.total_amount),
        "currency": "INR",
        "name": "BinaryPro",
        "description": f"Order {order.order_number}",
        "prefill": {"name": request.user.get_full_name(), "email": request.user.email, "contact": request.user.mobile or ""},
    })


@login_required
@require_POST
def razorpay_callback(request, order_number):
    """Browser callback after Checkout. Trust nothing until the signature verifies."""
    order = get_object_or_404(Order, order_number=order_number, customer=request.user)
    payment_id = request.POST.get("razorpay_payment_id", "")
    gw_order_id = request.POST.get("razorpay_order_id", "")
    signature = request.POST.get("razorpay_signature", "")
    if gw_order_id != order.gateway_order_id or not razorpay.verify_payment_signature(gw_order_id, payment_id, signature):
        audit(request.user, "payment.signature_invalid", order, payment_id=payment_id)
        messages.error(request, "Payment could not be verified. If money was debited, it will be confirmed automatically or refunded.")
        return redirect(order)
    services.mark_paid(order, gateway="razorpay", gateway_reference=payment_id, actor=request.user,
                       raw={"razorpay_order_id": gw_order_id, "razorpay_payment_id": payment_id})
    messages.success(request, "Payment successful. Thank you for your order!")
    return redirect(order)


@csrf_exempt
@require_POST
def razorpay_webhook(request):
    """Server-to-server confirmation (source of truth). Configure in Razorpay Dashboard → Webhooks."""
    signature = request.headers.get("X-Razorpay-Signature", "")
    if not razorpay.verify_webhook_signature(request.body, signature):
        logger.warning("Rejected Razorpay webhook with invalid signature")
        return HttpResponseBadRequest("invalid signature")
    try:
        event = json.loads(request.body)
        entity = event["payload"]["payment"]["entity"]
    except (ValueError, KeyError):
        return HttpResponse(status=200)  # events we don't handle

    order = Order.objects.filter(gateway_order_id=entity.get("order_id")).first()
    if order is None:
        # Not a product order: maybe a top-up wallet deposit.
        if event.get("event") in ("payment.captured", "order.paid") and entity.get("currency") == "INR":
            from apps.deposits import services as deposit_services
            try:
                deposit_services.complete_razorpay_deposit(entity.get("order_id"), entity["id"], raw=entity, paid_paise=entity.get("amount"))
            except deposit_services.DepositError:
                pass  # amount mismatch is audited inside
        return HttpResponse(status=200)
    if event.get("event") in ("payment.captured", "order.paid"):
        if int(entity.get("amount", 0)) != razorpay.to_paise(order.total_amount) or entity.get("currency") != "INR":
            audit(None, "payment.amount_mismatch", order, payment_id=entity.get("id"), amount=entity.get("amount"))
            return HttpResponse(status=200)
        services.mark_paid(order, gateway="razorpay", gateway_reference=entity["id"], raw=entity)
    elif event.get("event") == "payment.failed":
        services.record_failed_payment(order, "razorpay", entity["id"], raw=entity)
    return HttpResponse(status=200)


@login_required
@require_POST
def pay(request, order_number):
    """Demo payment (development only – disabled automatically when DEBUG=False)."""
    if not settings.PAYMENT_DEMO_MODE:
        messages.error(request, "Demo payments are disabled.")
        return redirect("orders:detail", order_number=order_number)
    order = get_object_or_404(Order, order_number=order_number, customer=request.user)
    try:
        services.mark_paid(order, gateway="demo", actor=request.user)
        messages.success(request, "Payment successful. Thank you for your order!")
    except services.CheckoutError as e:
        messages.error(request, str(e))
    return redirect(order)


@login_required
@require_POST
def upi_submit(request, order_number):
    """Customer paid via the UPI QR and submits the UTR for admin confirmation."""
    if not upi.is_configured():
        messages.error(request, "UPI payments are not available.")
        return redirect("orders:detail", order_number=order_number)
    order = get_object_or_404(Order, order_number=order_number, customer=request.user)
    try:
        services.submit_upi_payment(order, request.POST.get("utr", ""), actor=request.user)
        messages.success(request, "Thanks! Your payment is being verified. The order will be confirmed shortly.")
    except services.CheckoutError as e:
        messages.error(request, str(e))
    return redirect(order)


@login_required
@require_POST
def cancel(request, order_number):
    order = get_object_or_404(Order, order_number=order_number, customer=request.user)
    try:
        services.cancel_order(order, actor=request.user)
        messages.info(request, "Order cancelled.")
    except services.CheckoutError as e:
        messages.error(request, str(e))
    return redirect(order)
