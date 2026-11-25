from decimal import Decimal

from django.conf import settings
from django.db import models


class CommissionRule(models.Model):
    """Admin-configurable plan parameters. The active rule with the latest id applies.

    Rates are placeholders – set them to the figures in the client's
    approved compensation plan.
    """

    PLACEMENT_MANUAL, PLACEMENT_AUTO, PLACEMENT_ADMIN = "manual", "auto", "admin"
    PLACEMENT_MODES = [
        (PLACEMENT_MANUAL, "Manual – member chooses left or right (spillover to bottom of that leg)"),
        (PLACEMENT_AUTO, "Automatic – system fills the next available position"),
        (PLACEMENT_ADMIN, "Admin – company admin places every new distributor"),
    ]

    name = models.CharField(max_length=100, default="Default plan")
    placement_mode = models.CharField(max_length=10, choices=PLACEMENT_MODES, default=PLACEMENT_MANUAL)
    binary_match_rate = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("5.00"),
        help_text="% of matched volume paid as binary commission. Set 0 for a direct-sales-only plan.",
    )
    direct_sale_rate = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("2.00"),
        help_text="% of order volume paid to the buyer's sponsor. Set 0 for a binary-only plan.",
    )
    daily_binary_cap = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0"), help_text="Max binary commission per member per day (0 = no cap)"
    )
    min_withdrawal = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("500"))
    require_active_purchase = models.BooleanField(
        default=False, help_text="Only members with at least one paid order of their own earn commissions"
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Plan settings"
        verbose_name_plural = "Plan settings"

    @classmethod
    def current(cls):
        return cls.objects.filter(is_active=True).order_by("-id").first() or cls(name="Default plan")

    def __str__(self):
        return self.name


class Commission(models.Model):
    BINARY, DIRECT = "binary", "direct"
    TYPES = [(BINARY, "Binary matching"), (DIRECT, "Direct sale")]
    PENDING, RELEASED, REVERSED = "pending", "released", "reversed"
    STATUS = [(PENDING, "Pending"), (RELEASED, "Released"), (REVERSED, "Reversed")]

    member = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="commissions")
    order = models.ForeignKey("orders.Order", on_delete=models.PROTECT, related_name="commissions")
    commission_type = models.CharField(max_length=20, choices=TYPES)
    volume = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    rate = models.DecimalField(max_digits=5, decimal_places=2)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    capped = models.BooleanField(default=False)
    status = models.CharField(max_length=20, choices=STATUS, default=PENDING)
    idempotency_key = models.CharField(max_length=100, unique=True)
    released_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_commission_type_display()} ₹{self.amount} → {self.member.member_id}"
