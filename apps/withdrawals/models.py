import json

from django.conf import settings
from django.db import models

from apps.core.fields import EncryptedCharField


class WithdrawalRequest(models.Model):
    PENDING, APPROVED, PAID, REJECTED = "pending", "approved", "paid", "rejected"
    STATUS = [(PENDING, "Pending"), (APPROVED, "Approved"), (PAID, "Paid"), (REJECTED, "Rejected")]

    member = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="withdrawals")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=20, choices=STATUS, default=PENDING)
    # Encrypted JSON snapshot of bank/UPI details at request time.
    payout_snapshot = EncryptedCharField(blank=True, default="")
    payment_reference = models.CharField(max_length=100, blank=True)
    admin_note = models.TextField(blank=True)
    processed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    processed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    @property
    def payout_details(self) -> dict:
        return json.loads(self.payout_snapshot) if self.payout_snapshot else {}

    @payout_details.setter
    def payout_details(self, value: dict):
        self.payout_snapshot = json.dumps(value or {})

    def __str__(self):
        return f"Withdrawal #{self.pk} ₹{self.amount} ({self.status})"
