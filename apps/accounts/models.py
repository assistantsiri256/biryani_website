from django.contrib.auth.models import AbstractUser, UserManager
from django.db import models, transaction


class User(AbstractUser):
    ROLE_ADMIN = "admin"
    ROLE_DISTRIBUTOR = "distributor"
    ROLE_CUSTOMER = "customer"
    ROLE_CHOICES = [
        (ROLE_ADMIN, "Company Admin"),
        (ROLE_DISTRIBUTOR, "Distributor"),
        (ROLE_CUSTOMER, "Customer"),
    ]

    member_id = models.CharField(max_length=20, unique=True, editable=False)
    mobile = models.CharField(max_length=15, unique=True, null=True, blank=True)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default=ROLE_CUSTOMER)
    is_verified = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = UserManager()

    @property
    def is_distributor(self):
        return self.role == self.ROLE_DISTRIBUTOR

    @property
    def is_company_admin(self):
        return self.is_superuser or self.role == self.ROLE_ADMIN

    def save(self, *args, **kwargs):
        if not self.member_id:
            with transaction.atomic():
                last = User.objects.select_for_update().order_by("-id").values_list("id", flat=True).first() or 0
                self.member_id = f"BP{100001 + last}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.member_id} – {self.get_full_name() or self.username}"


class OTPCode(models.Model):
    """One-time password. Only a hash of the code is stored."""

    PURPOSE_VERIFY_MOBILE = "verify_mobile"
    PURPOSE_WITHDRAWAL = "withdrawal"
    PURPOSES = [(PURPOSE_VERIFY_MOBILE, "Verify mobile"), (PURPOSE_WITHDRAWAL, "Confirm withdrawal")]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="otp_codes")
    purpose = models.CharField(max_length=30, choices=PURPOSES)
    destination = models.CharField(max_length=150)
    code_hash = models.CharField(max_length=128)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "purpose", "created_at"])]
