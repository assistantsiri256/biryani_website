from decimal import Decimal

from django.conf import settings
from django.db import models


class BinaryNode(models.Model):
    LEFT = "left"
    RIGHT = "right"
    POSITION_CHOICES = [(LEFT, "Left"), (RIGHT, "Right")]

    member = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="binary_node")
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="children")
    position = models.CharField(max_length=10, choices=POSITION_CHOICES, null=True, blank=True)
    # Materialized path of ancestor node ids, e.g. "/1/4/9/" (includes self).
    # Gives single-query ancestor lookups and fast "is in my downline" checks.
    path = models.TextField(db_index=True, editable=False, default="")
    depth = models.PositiveIntegerField(default=0, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["parent", "position"], name="unique_parent_position"),
            # NULLs are never equal in a unique index, so enforce one root via depth=0.
            models.UniqueConstraint(
                fields=["depth"], condition=models.Q(parent__isnull=True), name="single_root_node"
            ),
            models.CheckConstraint(
                condition=models.Q(parent__isnull=True, position__isnull=True)
                | models.Q(parent__isnull=False, position__isnull=False),
                name="position_required_with_parent",
            ),
        ]

    def ancestor_ids(self):
        """Ids from root down to (excluding) self."""
        return [int(x) for x in self.path.strip("/").split("/")[:-1] if x]

    def is_descendant_of(self, other: "BinaryNode") -> bool:
        return self.path.startswith(other.path)

    def child(self, position):
        return self.children.filter(position=position).first()

    def __str__(self):
        return f"{self.member.member_id} ({self.position or 'root'})"


class BinaryVolume(models.Model):
    """Per-member running totals for each leg.

    `*_carry` is the unmatched volume still available for matching.
    `total_*` is lifetime volume for reporting.
    """

    member = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="binary_volume")
    left_carry = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    right_carry = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    total_left = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    total_right = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    total_matched = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    left_count = models.PositiveIntegerField(default=0)
    right_count = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Volume {self.member.member_id}: L {self.left_carry} / R {self.right_carry}"


class BinaryVolumeEntry(models.Model):
    """Immutable log of volume added to a member's leg by one order.

    Unique (order, member) guarantees an order can never credit the same
    ancestor twice, even if processing is retried.
    """

    member = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="volume_entries")
    order = models.ForeignKey("orders.Order", on_delete=models.PROTECT, related_name="volume_entries")
    leg = models.CharField(max_length=10, choices=BinaryNode.POSITION_CHOICES)
    volume = models.DecimalField(max_digits=14, decimal_places=2)
    reversed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["order", "member"], name="unique_volume_per_order_member")]
