from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from apps.accounts import otp
from apps.accounts.forms import OTPForm
from apps.accounts.models import OTPCode
from apps.commissions.models import CommissionRule
from apps.members.models import MemberProfile
from apps.wallet.services import get_balance

from .services import WithdrawalError, precheck, request_withdrawal

SESSION_KEY = "pending_withdrawal_amount"


@login_required
def withdrawals(request):
    if request.method == "POST":
        try:
            amount = Decimal(request.POST.get("amount", "0")).quantize(Decimal("0.01"))
            if amount <= 0:
                raise InvalidOperation
            precheck(request.user, amount)
            otp.issue_otp(request.user, OTPCode.PURPOSE_WITHDRAWAL)
        except InvalidOperation:
            messages.error(request, "Enter a valid amount.")
        except (WithdrawalError, otp.OTPError) as e:
            messages.error(request, str(e))
        else:
            request.session[SESSION_KEY] = str(amount)
            return redirect("withdrawals:confirm")
        return redirect("withdrawals:list")

    profile = MemberProfile.objects.filter(user=request.user).first()
    return render(
        request,
        "withdrawals/list.html",
        {
            "requests": request.user.withdrawals.all(),
            "balance": get_balance(request.user),
            "min_withdrawal": CommissionRule.current().min_withdrawal,
            "has_payout": bool(profile and profile.has_payout_details()),
            "profile": profile,
        },
    )


@login_required
def confirm(request):
    amount = request.session.get(SESSION_KEY)
    if not amount:
        return redirect("withdrawals:list")
    amount = Decimal(amount)
    form = OTPForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            otp.verify_otp(request.user, OTPCode.PURPOSE_WITHDRAWAL, form.cleaned_data["code"])
            request_withdrawal(request.user, amount)
        except (otp.OTPError, WithdrawalError) as e:
            form.add_error("code", str(e))
        else:
            request.session.pop(SESSION_KEY, None)
            messages.success(request, f"Withdrawal request for ₹{amount} submitted for approval.")
            return redirect("withdrawals:list")
    return render(request, "accounts/verify_otp.html", {
        "form": form, "title": "Confirm withdrawal", "button": f"Confirm ₹{amount}",
        "subtitle": f"Enter the code sent to {otp.mask_email(request.user.email)} to withdraw ₹{amount}.",
        "resend_url": "withdrawals:resend", "cancel_url": "withdrawals:list",
    })


@login_required
@require_POST
def resend(request):
    if request.session.get(SESSION_KEY):
        try:
            otp.issue_otp(request.user, OTPCode.PURPOSE_WITHDRAWAL)
            messages.info(request, "A new code has been sent.")
        except otp.OTPError as e:
            messages.error(request, str(e))
    return redirect("withdrawals:confirm")
