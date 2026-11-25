from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from apps.binary_network.services import PlacementError
from apps.core.utils import audit
from apps.members.models import MemberProfile
from apps.members.services import register_member

from . import otp
from .forms import LoginForm, OTPForm, ProfileForm, RegistrationForm
from .models import OTPCode


class LoginView(auth_views.LoginView):
    template_name = "accounts/login.html"
    authentication_form = LoginForm
    redirect_authenticated_user = True


def register(request):
    if request.user.is_authenticated:
        return redirect("reports:dashboard")
    initial = {
        "sponsor_id": request.GET.get("ref", ""),
        "leg": request.GET.get("leg", "left"),
        "under": request.GET.get("under", ""),
    }
    form = RegistrationForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            user = register_member(
                user_data={k: d[k] for k in ("first_name", "last_name", "username", "email", "mobile")},
                password=d["password1"],
                sponsor=d["sponsor"],
                leg=d["leg"],
                under=d["under_node"],
                join_as=d["join_as"],
            )
        except PlacementError as e:
            form.add_error(None, str(e))
        else:
            login(request, user)
            messages.success(request, f"Welcome to BinaryPro! Your member ID is {user.member_id}. We've emailed you a verification code.")
            _send_otp(request, user, OTPCode.PURPOSE_VERIFY_MOBILE)
            return redirect("accounts:verify_mobile")
    from apps.commissions.models import CommissionRule

    return render(request, "accounts/register.html", {"form": form, "placement_mode": CommissionRule.current().placement_mode})


def _send_otp(request, user, purpose) -> bool:
    try:
        otp.issue_otp(user, purpose)
        return True
    except otp.OTPError as e:
        messages.error(request, str(e))
    except Exception:
        messages.error(request, "We couldn't send the email right now. Please try again shortly.")
    return False


@login_required
def verify_mobile(request):
    if request.user.is_verified:
        return redirect("reports:dashboard")
    form = OTPForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            otp.verify_otp(request.user, OTPCode.PURPOSE_VERIFY_MOBILE, form.cleaned_data["code"])
        except otp.OTPError as e:
            form.add_error("code", str(e))
        else:
            request.user.is_verified = True
            request.user.save(update_fields=["is_verified"])
            audit(request.user, "member.mobile_verified", request.user)
            messages.success(request, "Email address verified.")
            return redirect("reports:dashboard")
    return render(request, "accounts/verify_otp.html", {
        "form": form, "title": "Verify your email",
        "subtitle": f"Enter the 6-digit code sent to {otp.mask_email(request.user.email)}",
        "resend_url": "accounts:resend_otp",
    })


@login_required
@require_POST
def resend_otp(request):
    if not request.user.is_verified and _send_otp(request, request.user, OTPCode.PURPOSE_VERIFY_MOBILE):
        messages.info(request, "A new code has been sent.")
    return redirect("accounts:verify_mobile")


@login_required
def profile(request):
    prof, _ = MemberProfile.objects.get_or_create(user=request.user)
    form = ProfileForm(request.POST or None, instance=prof, user=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Profile updated.")
        return redirect("accounts:profile")
    return render(request, "accounts/profile.html", {"form": form, "profile": prof})


@login_required
def settings_page(request):
    profile, _ = MemberProfile.objects.get_or_create(user=request.user)
    return render(request, "accounts/settings.html", {"profile": profile})
