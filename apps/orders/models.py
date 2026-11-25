from decimal import Decimal

from django.conf import settings
from django.db import models
from django.urls import reverse


class Order(models.Model):
    PENDING, PAID, CANCELLED, REFUNDED = "pending", "paid", "cancelled", "refunded"
    STATUS = [(PENDING, "Pending"), (PAID, "Paid"), (CANCELLED, "Cancelled"), (REFUNDED, "Refunded")]

    customer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="orders")
    order_number = models.CharField(max_length=30, unique=True)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    total_volume = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    status = models.CharField(max_length=20, choices=STATUS, default=PENDING)
    shipping_address = models.TextField(blank=True)
    gateway_order_id = models.CharField(max_length=60, unique=True, null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    commission_processed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def get_absolute_url(self):
        return reverse("orders:detail", args=[self.order_number])

    def __str__(self):
        return self.order_number


class OrderItem(models.Model):
    """Snapshot of product name/price/volume at purchase time."""

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey("products.Product", on_delete=models.PROTECT)
    product_name = models.CharField(max_length=200)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    unit_volume = models.DecimalField(max_digits=10, decimal_places=2)
    quantity = models.PositiveIntegerField()

    @property
    def line_total(self):
        return self.unit_price * self.quantity

    @property
    def line_volume(self):
        return self.unit_volume * self.quantity


class PaymentTransaction(models.Model):
    STATUS = [("initiated", "Initiated"), ("success", "Success"), ("failed", "Failed"), ("refunded", "Refunded")]

    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="payments")
    gateway = models.CharField(max_length=30, default="demo")  # razorpay / cashfree / demo
    gateway_reference = models.CharField(max_length=100, unique=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=20, choices=STATUS, default="initiated")
    raw_response = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
