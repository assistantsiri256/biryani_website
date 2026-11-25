"""OTP issue/verify with expiry, attempt limits, resend cooldown and hourly rate limit."""

import hashlib
import hmac
import secrets
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone

from .models import OTPCode


def mask_email(email: str) -> str:
    """a****@gmail.com style mask for showing where the code went."""
    local, _, domain = (email or "").partition("@")
    return f"{local[:1]}{'*' * max(len(local) - 1, 3)}@{domain}" if domain else ""


class OTPError(Exception):
    pass


def _hash(code: str, otp_id_salt: str) -> str:
    return hmac.new(settings.SECRET_KEY.encode(), f"{otp_id_salt}:{code}".encode(), hashlib.sha256).hexdigest()


def _generate() -> str:
    return "".join(secrets.choice("0123456789") for _ in range(settings.OTP_LENGTH))


@transaction.atomic
def issue_otp(user, purpose: str) -> OTPCode:
    """Create and email a new OTP to the user. Raises OTPError when rate limited."""
    if not user.email:
        raise OTPError("No email address on this account.")
    now = timezone.now()
    recent = OTPCode.objects.select_for_update().filter(user=user, created_at__gte=now - timedelta(hours=1))
    last = recent.filter(purpose=purpose).first()
    if last and (now - last.created_at).total_seconds() < settings.OTP_RESEND_COOLDOWN_SECONDS:
        wait = int(settings.OTP_RESEND_COOLDOWN_SECONDS - (now - last.created_at).total_seconds())
        raise OTPError(f"Please wait {wait} seconds before requesting another code.")
    if recent.count() >= settings.OTP_MAX_PER_HOUR:
        raise OTPError("Too many codes requested. Try again in an hour.")

    # Invalidate any earlier unused codes for this purpose.
    OTPCode.objects.filter(user=user, purpose=purpose, used_at__isnull=True).update(used_at=now)

    code = _generate()
    salt = secrets.token_hex(8)
    otp = OTPCode.objects.create(
        user=user, purpose=purpose, destination=user.email,
        code_hash=f"{salt}${_hash(code, salt)}", expires_at=now + timedelta(seconds=settings.OTP_TTL_SECONDS),
    )
    minutes = settings.OTP_TTL_SECONDS // 60
    send_mail(
        subject=f"{code} is your BinaryPro verification code",
        message=(
            f"Hello {user.get_full_name() or user.username},\n\n"
            f"Your BinaryPro verification code is: {code}\n\n"
            f"It is valid for {minutes} minutes. Do not share it with anyone.\n"
            "If you did not request this code, you can ignore this email."
        ),
        from_email=None,  # DEFAULT_FROM_EMAIL
        recipient_list=[user.email],
    )
    return otp


def verify_otp(user, purpose: str, code: str) -> bool:
    """Raises OTPError on failure. Attempt counters are committed before the error is raised."""
    error = _check_and_consume(user, purpose, code)
    if error:
        raise OTPError(error)
    return True


@transaction.atomic
def _check_and_consume(user, purpose, code) -> str | None:
    now = timezone.now()
    otp = OTPCode.objects.select_for_update().filter(user=user, purpose=purpose, used_at__isnull=True).first()
    if otp is None:
        return "No active code. Please request a new one."
    if otp.expires_at < now:
        otp.used_at = now
        otp.save(update_fields=["used_at"])
        return "This code has expired. Please request a new one."

    salt, expected = otp.code_hash.split("$", 1)
    if not hmac.compare_digest(expected, _hash((code or "").strip(), salt)):
        otp.attempts += 1
        if otp.attempts >= settings.OTP_MAX_ATTEMPTS:
            otp.used_at = now  # burn the code
        otp.save(update_fields=["attempts", "used_at"])
        left = settings.OTP_MAX_ATTEMPTS - otp.attempts
        return f"Incorrect code. {left} attempt(s) left." if left > 0 else "Too many wrong attempts. Please request a new code."

    otp.used_at = now
    otp.save(update_fields=["used_at"])
    return None
