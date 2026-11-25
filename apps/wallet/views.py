from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Sum
from django.shortcuts import render

from apps.commissions.models import Commission
from apps.withdrawals.models import WithdrawalRequest

from .models import WalletTransaction
from .services import get_balance


def wallet_summary(user):
    comm = Commission.objects.filter(member=user)
    return {
        "balance": get_balance(user),
        "pending": comm.filter(status=Commission.PENDING).aggregate(s=Sum("amount"))["s"] or Decimal("0"),
        "total_earned": comm.filter(status=Commission.RELEASED).aggregate(s=Sum("amount"))["s"] or Decimal("0"),
        "total_withdrawn": WithdrawalRequest.objects.filter(member=user, status=WithdrawalRequest.PAID).aggregate(s=Sum("amount"))["s"]
        or Decimal("0"),
    }


@login_required
def wallet(request):
    txs = Paginator(request.user.wallet_transactions.all(), 20).get_page(request.GET.get("page"))
    commissions = Commission.objects.filter(member=request.user).select_related("order")[:20]
    return render(request, "wallet/wallet.html", {"summary": wallet_summary(request.user), "txs": txs, "commissions": commissions})


@login_required
def income_history(request):
    """All commissions; ?type=binary|direct and ?status=pending|released|reversed filter."""
    qs = Commission.objects.filter(member=request.user).select_related("order")
    ctype, status = request.GET.get("type", ""), request.GET.get("status", "")
    if ctype in dict(Commission.TYPES):
        qs = qs.filter(commission_type=ctype)
    if status in dict(Commission.STATUS):
        qs = qs.filter(status=status)
    totals = {
        t["commission_type"]: t["s"]
        for t in Commission.objects.filter(member=request.user).exclude(status=Commission.REVERSED)
        .values("commission_type").annotate(s=Sum("amount"))
    }
    return render(request, "wallet/income.html", {
        "page": Paginator(qs, 25).get_page(request.GET.get("page")),
        "ctype": ctype, "status": status,
        "types": Commission.TYPES, "statuses": Commission.STATUS,
        "binary_total": totals.get(Commission.BINARY, Decimal("0")),
        "direct_total": totals.get(Commission.DIRECT, Decimal("0")),
        "filter_total": qs.exclude(status=Commission.REVERSED).aggregate(s=Sum("amount"))["s"] or Decimal("0"),
    })


@login_required
def transactions(request):
    qs = request.user.wallet_transactions.all()
    kind = request.GET.get("kind", "")
    if kind in dict(WalletTransaction.TYPES):
        qs = qs.filter(transaction_type=kind)
    sums = {t["transaction_type"]: t["s"] for t in request.user.wallet_transactions.values("transaction_type").annotate(s=Sum("amount"))}
    return render(request, "wallet/transactions.html", {
        "page": Paginator(qs, 25).get_page(request.GET.get("page")),
        "kind": kind,
        "credits": sums.get(WalletTransaction.CREDIT, Decimal("0")),
        "debits": sums.get(WalletTransaction.DEBIT, Decimal("0")),
        "balance": get_balance(request.user),
    })
