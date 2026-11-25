"""Top-up wallet: money a member deposits to pay for product orders.

Kept separate from the commission wallet (apps.wallet): top-up balance can only
be spent on orders, never withdrawn, and deposits never earn commission.
"""
from django.conf import settings
from django.db import models


class TopupTransaction(models.Model):
    """Immutable ledger. Balance = credits - debits."""

    CREDIT, DEBIT = "credit", "debit"
    TYPES = [(CREDIT, "Credit"), (DEBIT, "Debit")]

    member = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="topup_transactions")
    transaction_type = models.CharField(max_length=10, choices=TYPES)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    reference = models.CharField(max_length=100, unique=True)  # idempotency key, e.g. "deposit:7"
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.CheckConstraint(condition=models.Q(amount__gt=0), name="topup_amount_positive")]

    def __str__(self):
        return f"{self.transaction_type} {self.amount} ({self.reference})"


class DepositRequest(models.Model):
    PENDING, APPROVED, REJECTED = "pending", "approved", "rejected"
    STATUS = [(PENDING, "Pending"), (APPROVED, "Approved"), (REJECTED, "Rejected")]
    UPI, USDT, RAZORPAY = "upi", "usdt", "razorpay"
    METHODS = [(RAZORPAY, "Razorpay"), (UPI, "UPI (manual)"), (USDT, "USDT (BEP-20)")]

    member = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="deposit_requests")
    method = models.CharField(max_length=10, choices=METHODS, default=UPI)
    amount = models.DecimalField(max_digits=12, decimal_places=2)  # INR credited to the top-up wallet
    crypto_amount = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)  # USDT received
    utr = models.CharField("UTR / TxID", max_length=100, unique=True)
    status = models.CharField(max_length=20, choices=STATUS, default=PENDING)
    admin_note = models.CharField(max_length=255, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.CheckConstraint(condition=models.Q(amount__gt=0), name="deposit_amount_positive")]

    @property
    def deposit_no(self):
        return f"DEP{self.pk:05d}"

    def __str__(self):
        return f"{self.deposit_no} {self.amount} ({self.status})"


class CryptoPaymentRequest(models.Model):
    """A USDT payment QR shown to a member. Each open request has a unique USDT amount,
    so an on-chain transfer can only be claimed by the member it was issued to."""

    member = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="crypto_payment_requests")
    inr_amount = models.DecimalField(max_digits=12, decimal_places=2)
    usdt_amount = models.DecimalField(max_digits=18, decimal_places=6)
    rate = models.DecimalField(max_digits=10, decimal_places=2)
    to_address = models.CharField(max_length=42)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    deposit = models.OneToOneField(DepositRequest, null=True, blank=True, on_delete=models.SET_NULL, related_name="crypto_request")

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["usdt_amount", "expires_at"])]

    def __str__(self):
        return f"{self.member} {self.usdt_amount} USDT"


class RazorpayDepositOrder(models.Model):
    """A Razorpay order created for a top-up deposit. Completed once by callback or webhook."""

    member = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="razorpay_deposit_orders")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    gateway_order_id = models.CharField(max_length=60, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    deposit = models.OneToOneField(DepositRequest, null=True, blank=True, on_delete=models.SET_NULL, related_name="razorpay_order")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.gateway_order_id} {self.amount}"
