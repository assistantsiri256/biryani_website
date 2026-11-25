"""Daily financial reconciliation. Exit code 1 if any discrepancy is found (use in cron/monitoring).

Checks:
  1. Every released commission has exactly one wallet credit of the same amount.
  2. Every reversed-after-release commission has a matching reversal debit.
  3. Every withdrawal has a hold debit; every rejected one has a refund credit.
  4. No wallet entry references a missing commission/withdrawal.
  5. Paid orders have had commissions processed.
  6. Members with a negative wallet balance (from clawbacks) are listed.
"""

from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db.models import Q, Sum

from apps.accounts.models import User
from apps.commissions.models import Commission
from apps.core.utils import audit
from apps.orders.models import Order
from apps.wallet.models import WalletTransaction
from apps.withdrawals.models import WithdrawalRequest


class Command(BaseCommand):
    help = "Reconcile commissions, withdrawals and wallet ledger entries."

    def handle(self, *args, **opts):
        issues: list[str] = []
        ledger = {t.reference: t for t in WalletTransaction.objects.all()}

        def expect(ref, tx_type, amount, label):
            t = ledger.get(ref)
            if t is None:
                issues.append(f"{label}: missing wallet entry '{ref}'")
            elif t.transaction_type != tx_type or t.amount != amount:
                issues.append(f"{label}: wallet entry '{ref}' is {t.transaction_type} ₹{t.amount}, expected {tx_type} ₹{amount}")

        for c in Commission.objects.exclude(status=Commission.PENDING).filter(Q(status=Commission.RELEASED) | Q(released_at__isnull=False)):
            expect(f"commission:{c.pk}", "credit", c.amount, f"Commission #{c.pk}")
            if c.status == Commission.REVERSED:
                expect(f"commission-reversal:{c.pk}", "debit", c.amount, f"Commission #{c.pk} reversal")

        for w in WithdrawalRequest.objects.all():
            expect(f"withdrawal:{w.pk}", "debit", w.amount, f"Withdrawal #{w.pk}")
            if w.status == WithdrawalRequest.REJECTED:
                expect(f"withdrawal-refund:{w.pk}", "credit", w.amount, f"Withdrawal #{w.pk} refund")
            elif f"withdrawal-refund:{w.pk}" in ledger:
                issues.append(f"Withdrawal #{w.pk} is {w.status} but has a refund entry")

        known_prefixes = ("commission:", "commission-reversal:", "withdrawal:", "withdrawal-refund:")
        commission_ids = set(Commission.objects.values_list("pk", flat=True))
        withdrawal_ids = set(WithdrawalRequest.objects.values_list("pk", flat=True))
        for ref in ledger:
            prefix, _, ident = ref.partition(":")
            if not ref.startswith(known_prefixes):
                continue
            ids = commission_ids if prefix.startswith("commission") else withdrawal_ids
            if not ident.isdigit() or int(ident) not in ids:
                issues.append(f"Orphan wallet entry '{ref}'")

        unprocessed = Order.objects.filter(status=Order.PAID, commission_processed=False).count()
        if unprocessed:
            issues.append(f"{unprocessed} paid order(s) without commission processing (check Celery worker)")

        balances = (
            User.objects.annotate(
                cr=Sum("wallet_transactions__amount", filter=Q(wallet_transactions__transaction_type="credit")),
                dr=Sum("wallet_transactions__amount", filter=Q(wallet_transactions__transaction_type="debit")),
            )
            .filter(wallet_transactions__isnull=False)
            .distinct()
        )
        negatives = [(u.member_id, (u.cr or Decimal(0)) - (u.dr or Decimal(0))) for u in balances if (u.cr or 0) - (u.dr or 0) < 0]

        totals = WalletTransaction.objects.aggregate(
            cr=Sum("amount", filter=Q(transaction_type="credit")), dr=Sum("amount", filter=Q(transaction_type="debit"))
        )
        self.stdout.write(f"Ledger: credits ₹{totals['cr'] or 0}, debits ₹{totals['dr'] or 0}, entries {len(ledger)}")
        for mid, bal in negatives:
            self.stdout.write(self.style.WARNING(f"Negative balance (clawback): {mid} ₹{bal}"))

        audit(None, "reconcile.run", issues=len(issues), negatives=len(negatives))
        if issues:
            for i in issues:
                self.stdout.write(self.style.ERROR(i))
            self.stdout.write(self.style.ERROR(f"{len(issues)} discrepancy(ies) found."))
            raise SystemExit(1)
        self.stdout.write(self.style.SUCCESS("Reconciliation OK – no discrepancies."))
