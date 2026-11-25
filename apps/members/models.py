from django.conf import settings
from django.db import models

from apps.core.fields import EncryptedCharField, mask


class Referral(models.Model):
    """Who introduced the member. Independent of binary placement."""

    sponsor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="sponsored_members"
    )
    member = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="referral_record"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.sponsor.member_id} → {self.member.member_id}"


class MemberProfile(models.Model):
    KYC_CHOICES = [("pending", "Pending"), ("submitted", "Submitted"), ("approved", "Approved"), ("rejected", "Rejected")]

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    address = models.TextField(blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=100, blank=True)
    pincode = models.CharField(max_length=10, blank=True)
    # Sensitive fields are encrypted at rest (see apps/core/fields.py).
    pan_number = EncryptedCharField(blank=True, max_plain_length=10)
    bank_account_name = models.CharField(max_length=120, blank=True)
    bank_account_number = EncryptedCharField(blank=True, max_plain_length=18)
    bank_ifsc = models.CharField(max_length=11, blank=True)
    upi_id = EncryptedCharField(blank=True, max_plain_length=100)
    kyc_status = models.CharField(max_length=20, choices=KYC_CHOICES, default="pending")

    def has_payout_details(self):
        return bool(self.upi_id or (self.bank_account_number and self.bank_ifsc))

    @property
    def masked_account(self):
        return mask(self.bank_account_number)

    @property
    def masked_pan(self):
        return mask(self.pan_number)

    def __str__(self):
        return f"Profile of {self.user.member_id}"
