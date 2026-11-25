import json
from datetime import timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Prefetch, Q, Sum
from django.db.models.functions import TruncDate
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.models import User
from apps.binary_network.models import BinaryNode
from apps.binary_network.services import PlacementError, ensure_volume, place_member
from apps.notifications.models import notify
from apps.commissions.engine import release_commissions, reverse_order
from apps.commissions.models import Commission
from apps.core.models import AuditLog
from apps.core.permissions import admin_required
from apps.orders import services as order_services
from apps.orders.models import Order, PaymentTransaction
from apps.wallet.views import wallet_summary
from apps.deposits.services import topup_balance
from apps.withdrawals.models import WithdrawalRequest
from apps.withdrawals.services import WithdrawalError, process_withdrawal


def _daily_series(qs, date_field, value_field, days=14):
    start = timezone.localdate() - timedelta(days=days - 1)
    rows = (
        qs.filter(**{f"{date_field}__date__gte": start})
        .annotate(day=TruncDate(date_field))
        .values("day")
        .annotate(total=Sum(value_field))
    )
    by_day = {r["day"]: float(r["total"] or 0) for r in rows}
    labels = [(start + timedelta(days=i)) for i in range(days)]
    return json.dumps({"labels": [d.strftime("%d %b") for d in labels], "values": [by_day.get(d, 0) for d in labels]})


@login_required
def dashboard(request):
    user = request.user
    if user.is_company_admin and not hasattr(user, "binary_node"):
        return redirect("reports:admin_dashboard")
    vol = ensure_volume(user) if hasattr(user, "binary_node") else None
    ctx = {
        "vol": vol,
        "summary": wallet_summary(user),
        "topup_balance": topup_balance(user),
        "direct_count": user.sponsored_members.count(),
        "recent_txs": user.wallet_transactions.all()[:6],
        "recent_orders": user.orders.all()[:5],
        "referral_left": request.build_absolute_uri(f"/accounts/register/?ref={user.member_id}&leg=left"),
        "referral_right": request.build_absolute_uri(f"/accounts/register/?ref={user.member_id}&leg=right"),
        "earnings_chart": _daily_series(
            Commission.objects.filter(member=user).exclude(status=Commission.REVERSED), "created_at", "amount"
        ),
    }
    return render(request, "dashboard/member.html", ctx)


# ---------------------------------------------------------------- admin panel


@admin_required
def admin_dashboard(request):
    paid = Order.objects.filter(status=Order.PAID)
    ctx = {
        "total_members": User.objects.exclude(role=User.ROLE_ADMIN).filter(is_superuser=False).count(),
        "active_distributors": User.objects.filter(role=User.ROLE_DISTRIBUTOR, is_active=True).count(),
        "total_sales": paid.aggregate(s=Sum("total_amount"))["s"] or Decimal("0"),
        "orders_today": paid.filter(paid_at__date=timezone.localdate()).count(),
        "commission_liability": Commission.objects.filter(status=Commission.PENDING).aggregate(s=Sum("amount"))["s"] or Decimal("0"),
        "commission_paid": Commission.objects.filter(status=Commission.RELEASED).aggregate(s=Sum("amount"))["s"] or Decimal("0"),
        "pending_withdrawals": WithdrawalRequest.objects.filter(status__in=["pending", "approved"]).aggregate(n=Count("id"), s=Sum("amount")),
        "recent_orders": Order.objects.select_related("customer")[:8],
        "new_members": User.objects.order_by("-created_at")[:6],
        "sales_chart": _daily_series(paid, "paid_at", "total_amount"),
        "joins_chart": json.dumps(_joins_series()),
    }
    return render(request, "admin_panel/dashboard.html", ctx)


def _joins_series(days=14):
    start = timezone.localdate() - timedelta(days=days - 1)
    rows = User.objects.filter(created_at__date__gte=start).annotate(day=TruncDate("created_at")).values("day").annotate(n=Count("id"))
    by_day = {r["day"]: r["n"] for r in rows}
    return [by_day.get(start + timedelta(days=i), 0) for i in range(days)]


@admin_required
def admin_members(request):
    q = request.GET.get("q", "").strip()
    role = request.GET.get("role", "")
    users = User.objects.select_related("binary_volume", "referral_record__sponsor", "binary_node").order_by("-created_at")
    if q:
        users = users.filter(Q(member_id__icontains=q) | Q(username__icontains=q) | Q(first_name__icontains=q) | Q(mobile__icontains=q))
    if role:
        users = users.filter(role=role)
    page = Paginator(users, 25).get_page(request.GET.get("page"))
    return render(request, "admin_panel/members.html", {"page": page, "q": q, "role": role, "roles": User.ROLE_CHOICES})


@admin_required
@require_POST
def admin_member_toggle(request, member_id):
    user = get_object_or_404(User, member_id=member_id, is_superuser=False)
    user.is_active = not user.is_active
    user.save(update_fields=["is_active"])
    AuditLog.objects.create(actor=request.user, action="member.toggle_active", object_type="User", object_id=str(user.pk), details={"is_active": user.is_active})
    messages.success(request, f"{user.member_id} {'activated' if user.is_active else 'blocked'}.")
    return redirect(request.POST.get("next") or "reports:admin_members")


@admin_required
def admin_orders(request):
    status = request.GET.get("status", "")
    upi_pending_qs = PaymentTransaction.objects.filter(gateway="upi", status="initiated")
    orders = Order.objects.select_related("customer").prefetch_related(
        Prefetch("payments", queryset=upi_pending_qs, to_attr="upi_pending")
    )
    if status == "upi":
        orders = orders.filter(status=Order.PENDING, payments__in=upi_pending_qs).distinct()
    elif status:
        orders = orders.filter(status=status)
    page = Paginator(orders, 25).get_page(request.GET.get("page"))
    return render(request, "admin_panel/orders.html", {
        "page": page, "status": status, "statuses": Order.STATUS,
        "upi_waiting": Order.objects.filter(status=Order.PENDING, payments__in=upi_pending_qs).distinct().count(),
    })


@admin_required
@require_POST
def admin_order_action(request, order_number):
    order = get_object_or_404(Order, order_number=order_number)
    action = request.POST.get("action")
    try:
        if action == "mark_paid":
            order_services.mark_paid(order, gateway="manual", actor=request.user)
            messages.success(request, f"{order.order_number} marked as paid; commissions calculated.")
        elif action == "confirm_upi":
            order_services.confirm_upi_payment(order, actor=request.user)
            messages.success(request, f"{order.order_number}: UPI payment confirmed; commissions calculated.")
        elif action == "reject_upi":
            order_services.reject_upi_payment(order, actor=request.user)
            messages.info(request, f"{order.order_number}: UPI payment rejected; the customer has been notified.")
        elif action == "refund":
            reverse_order(order, actor=request.user)
            messages.success(request, f"{order.order_number} refunded; commissions reversed.")
        elif action == "cancel":
            order_services.cancel_order(order, actor=request.user)
            messages.success(request, f"{order.order_number} cancelled.")
    except (ValueError, order_services.CheckoutError) as e:
        messages.error(request, str(e))
    return redirect(request.POST.get("next") or "reports:admin_orders")


@admin_required
def admin_commissions(request):
    status = request.GET.get("status", "pending")
    qs = Commission.objects.select_related("member", "order")
    if status:
        qs = qs.filter(status=status)
    page = Paginator(qs, 30).get_page(request.GET.get("page"))
    totals = Commission.objects.values("status").annotate(s=Sum("amount"), n=Count("id"))
    return render(
        request, "admin_panel/commissions.html", {"page": page, "status": status, "totals": {t["status"]: t for t in totals}}
    )


@admin_required
@require_POST
def admin_release_commissions(request):
    ids = request.POST.getlist("ids")
    qs = Commission.objects.filter(status=Commission.PENDING)
    if ids:
        qs = qs.filter(pk__in=ids)
    n = release_commissions(qs, actor=request.user)
    messages.success(request, f"{n} commission(s) released to member wallets.")
    return redirect("reports:admin_commissions")


@admin_required
def admin_withdrawals(request):
    status = request.GET.get("status", "pending")
    qs = WithdrawalRequest.objects.select_related("member")
    if status:
        qs = qs.filter(status=status)
    page = Paginator(qs, 25).get_page(request.GET.get("page"))
    return render(request, "admin_panel/withdrawals.html", {"page": page, "status": status, "statuses": WithdrawalRequest.STATUS})


@admin_required
@require_POST
def admin_withdrawal_action(request, pk):
    wr = get_object_or_404(WithdrawalRequest, pk=pk)
    try:
        process_withdrawal(
            wr, request.POST.get("action"), request.user,
            note=request.POST.get("note", ""), payment_reference=request.POST.get("payment_reference", ""),
        )
        messages.success(request, f"Withdrawal #{wr.pk} updated.")
    except WithdrawalError as e:
        messages.error(request, str(e))
    return redirect(request.POST.get("next") or "reports:admin_withdrawals")


@admin_required
def admin_placements(request):
    """Distributors waiting for a binary position (placement mode = admin)."""
    if request.method == "POST":
        member = get_object_or_404(User, member_id=request.POST.get("member_id", ""), role=User.ROLE_DISTRIBUTOR)
        parent = BinaryNode.objects.filter(member__member_id=request.POST.get("parent_id", "").strip().upper()).first()
        sponsor = getattr(getattr(member, "referral_record", None), "sponsor", None)
        try:
            if parent is None:
                raise PlacementError("Parent member ID not found in the tree.")
            node = place_member(member, sponsor=sponsor, leg=request.POST.get("leg", ""), under=parent)
        except PlacementError as e:
            messages.error(request, f"{member.member_id}: {e}")
        else:
            AuditLog.objects.create(actor=request.user, action="member.admin_place", object_type="User", object_id=str(member.pk),
                                    details={"parent": parent.member.member_id, "position": node.position})
            notify(member, "You have been placed", f"Your position: {node.get_position_display()} of {parent.member.member_id}.", "/network/tree/")
            messages.success(request, f"{member.member_id} placed on the {node.position} of {parent.member.member_id}.")
        return redirect("reports:admin_placements")

    waiting = (
        User.objects.filter(role=User.ROLE_DISTRIBUTOR, binary_node__isnull=True)
        .select_related("referral_record__sponsor")
        .order_by("created_at")
    )
    return render(request, "admin_panel/placements.html", {"waiting": waiting})


@admin_required
def admin_reports(request):
    paid = Order.objects.filter(status=Order.PAID)
    top_earners = (
        User.objects.annotate(earned=Sum("commissions__amount", filter=~Q(commissions__status=Commission.REVERSED)))
        .filter(earned__gt=0)
        .order_by("-earned")[:10]
    )
    top_products = (
        paid.values("items__product_name")
        .annotate(qty=Sum("items__quantity"))
        .order_by("-qty")[:10]
    )
    ctx = {
        "top_earners": top_earners,
        "top_products": top_products,
        "by_type": Commission.objects.exclude(status=Commission.REVERSED).values("commission_type").annotate(s=Sum("amount"), n=Count("id")),
        "audit": AuditLog.objects.select_related("actor")[:30],
        "sales_chart": _daily_series(paid, "paid_at", "total_amount", days=30),
    }
    return render(request, "admin_panel/reports.html", ctx)
