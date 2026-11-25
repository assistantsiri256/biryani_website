from django.conf import settings
from django.db import models


class WalletTransaction(models.Model):
    """Immutable ledger. Balance = sum(credits) - sum(debits); never stored alone."""

    CREDIT, DEBIT = "credit", "debit"
    TYPES = [(CREDIT, "Credit"), (DEBIT, "Debit")]

    member = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="wallet_transactions")
    transaction_type = models.CharField(max_length=10, choices=TYPES)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    # Unique business reference (e.g. "commission:42") = idempotency key.
    reference = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.CheckConstraint(condition=models.Q(amount__gt=0), name="wallet_amount_positive")]

    def __str__(self):
        return f"{self.transaction_type} {self.amount} ({self.reference})"
