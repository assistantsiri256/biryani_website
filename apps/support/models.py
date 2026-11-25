from django.conf import settings
from django.db import models


class SupportTicket(models.Model):
    OPEN, ANSWERED, CLOSED = "open", "answered", "closed"
    STATUS = [(OPEN, "Open"), (ANSWERED, "Answered"), (CLOSED, "Closed")]
    CATEGORIES = [
        ("order", "Order / Payment"),
        ("income", "Income / Commission"),
        ("withdrawal", "Withdrawal"),
        ("account", "Account / KYC"),
        ("other", "Other"),
    ]

    member = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="support_tickets")
    category = models.CharField(max_length=20, choices=CATEGORIES, default="other")
    subject = models.CharField(max_length=150)
    message = models.TextField(max_length=3000)
    status = models.CharField(max_length=20, choices=STATUS, default=OPEN)
    admin_reply = models.TextField(blank=True)
    replied_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    replied_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    @property
    def ticket_no(self):
        return f"TKT{self.pk:05d}"

    def __str__(self):
        return f"{self.ticket_no} {self.subject}"
