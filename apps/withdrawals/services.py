from django.db import transaction
from django.utils import timezone

from apps.commissions.models import CommissionRule
from apps.core.utils import audit
from apps.notifications.models import notify
from apps.wallet import services as wallet

from .models import WithdrawalRequest


class WithdrawalError(Exception):
    pass


def precheck(user, amount):
    """Validate a withdrawal before sending the OTP. Returns the member profile."""
    from apps.members.models import MemberProfile

    rule = CommissionRule.current()
    if not user.is_verified:
        raise WithdrawalError("Verify your mobile number before requesting a withdrawal.")
    if amount < rule.min_withdrawal:
        raise WithdrawalError(f"Minimum withdrawal is ₹{rule.min_withdrawal}.")
    profile = MemberProfile.objects.filter(user=user).first()
    if profile is None or not profile.has_payout_details():
        raise WithdrawalError("Add your bank or UPI details in your profile first.")
    if wallet.get_balance(user) < amount:
        raise WithdrawalError("Insufficient wallet balance.")
    return profile


@transaction.atomic
def request_withdrawal(user, amount) -> WithdrawalRequest:
    """Funds are debited (held) immediately; refunded to wallet if rejected.

    Callers must have confirmed the member's OTP first.
    """
    profile = precheck(user, amount)
    wr = WithdrawalRequest.objects.create(
        member=user,
        amount=amount,
        payout_details={
            "account_name": profile.bank_account_name,
            "account_number": profile.bank_account_number,
            "ifsc": profile.bank_ifsc,
            "upi_id": profile.upi_id,
        },
    )
    try:
        wallet.debit(user, amount, f"withdrawal:{wr.pk}", f"Withdrawal request #{wr.pk}")
    except wallet.InsufficientBalance as e:
        raise WithdrawalError(str(e))
    audit(user, "withdrawal.request", wr, amount=amount)
    return wr


@transaction.atomic
def process_withdrawal(wr: WithdrawalRequest, action: str, actor, note="", payment_reference=""):
    wr = WithdrawalRequest.objects.select_for_update().get(pk=wr.pk)
    allowed = {
        "approve": ([WithdrawalRequest.PENDING], WithdrawalRequest.APPROVED),
        "pay": ([WithdrawalRequest.PENDING, WithdrawalRequest.APPROVED], WithdrawalRequest.PAID),
        "reject": ([WithdrawalRequest.PENDING, WithdrawalRequest.APPROVED], WithdrawalRequest.REJECTED),
    }
    if action not in allowed or wr.status not in allowed[action][0]:
        raise WithdrawalError(f"Cannot {action} a {wr.status} request.")
    wr.status = allowed[action][1]
    wr.admin_note = note or wr.admin_note
    wr.payment_reference = payment_reference or wr.payment_reference
    wr.processed_by = actor
    wr.processed_at = timezone.now()
    wr.save()
    if action == "reject":
        wallet.credit(wr.member, wr.amount, f"withdrawal-refund:{wr.pk}", f"Withdrawal #{wr.pk} rejected – refunded")
    notify(wr.member, f"Withdrawal {wr.get_status_display().lower()}", f"Your withdrawal of ₹{wr.amount} is {wr.get_status_display().lower()}. {note}", "/withdrawals/")
    audit(actor, f"withdrawal.{action}", wr, amount=wr.amount, note=note)
    return wr
