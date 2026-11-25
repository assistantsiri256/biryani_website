import re

from django import forms
from django.contrib.auth import password_validation
from django.contrib.auth.forms import AuthenticationForm

from apps.binary_network.models import BinaryNode
from apps.members.models import MemberProfile

from .models import User


class BootstrapMixin:
    def _style(self):
        for f in self.fields.values():
            css = "form-select" if isinstance(f.widget, forms.Select) else "form-control"
            if isinstance(f.widget, forms.RadioSelect):
                continue
            f.widget.attrs.setdefault("class", css)


class LoginForm(BootstrapMixin, AuthenticationForm):
    username = forms.CharField(label="Username or Member ID")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style()

    def clean(self):
        from .throttle import client_ip, is_locked, register_failure, reset

        # Allow login with member ID as well as username.
        uname = self.cleaned_data.get("username", "")
        match = User.objects.filter(member_id__iexact=uname).first()
        if match:
            self.cleaned_data["username"] = uname = match.username
        ip = client_ip(self.request) if self.request else ""
        if is_locked(uname, ip):
            raise forms.ValidationError("Too many failed login attempts. Please try again in 15 minutes.")
        try:
            data = super().clean()
        except forms.ValidationError:
            register_failure(uname, ip)
            raise
        reset(uname, ip)
        return data


class OTPForm(forms.Form):
    code = forms.RegexField(
        regex=r"^\d{6}$", max_length=6, label="Verification code",
        widget=forms.TextInput(attrs={"class": "form-control form-control-lg text-center", "inputmode": "numeric", "autocomplete": "one-time-code", "maxlength": 6}),
        error_messages={"invalid": "Enter the 6-digit code."},
    )


class RegistrationForm(BootstrapMixin, forms.Form):
    first_name = forms.CharField(max_length=150)
    last_name = forms.CharField(max_length=150, required=False)
    username = forms.CharField(max_length=150)
    email = forms.EmailField()
    mobile = forms.RegexField(regex=r"^[6-9]\d{9}$", max_length=10, error_messages={"invalid": "Enter a valid 10-digit mobile number."})
    sponsor_id = forms.CharField(label="Sponsor ID", max_length=20, required=False, help_text="Member ID of the person who referred you")
    join_as = forms.ChoiceField(choices=[("distributor", "Distributor (build a team)"), ("customer", "Customer (buy products only)")])
    leg = forms.ChoiceField(label="Placement leg", choices=BinaryNode.POSITION_CHOICES, initial="left")
    under = forms.CharField(widget=forms.HiddenInput, required=False)
    password1 = forms.CharField(label="Password", widget=forms.PasswordInput)
    password2 = forms.CharField(label="Confirm password", widget=forms.PasswordInput)
    agree = forms.BooleanField(label="I agree to the terms and the company's income disclosure")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style()
        self.fields["agree"].widget.attrs["class"] = "form-check-input"

    def clean_username(self):
        u = self.cleaned_data["username"]
        if User.objects.filter(username__iexact=u).exists():
            raise forms.ValidationError("This username is taken.")
        return u

    def clean_email(self):
        e = self.cleaned_data["email"].lower()
        if User.objects.filter(email__iexact=e).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return e

    def clean_mobile(self):
        m = self.cleaned_data["mobile"]
        if User.objects.filter(mobile=m).exists():
            raise forms.ValidationError("This mobile number is already registered.")
        return m

    def clean(self):
        data = super().clean()
        if data.get("password1") != data.get("password2"):
            self.add_error("password2", "Passwords do not match.")
        elif data.get("password1"):
            try:
                password_validation.validate_password(data["password1"])
            except forms.ValidationError as e:
                self.add_error("password1", e)

        tree_exists = BinaryNode.objects.exists()
        sid = (data.get("sponsor_id") or "").strip().upper()
        sponsor = None
        if sid:
            sponsor = User.objects.filter(member_id=sid, is_active=True).first()
            if sponsor is None:
                self.add_error("sponsor_id", "No active member with this ID.")
            elif data.get("join_as") == "distributor" and not hasattr(sponsor, "binary_node"):
                self.add_error("sponsor_id", "This sponsor is not a distributor.")
        elif tree_exists:
            self.add_error("sponsor_id", "A sponsor ID is required.")
        data["sponsor"] = sponsor

        under = None
        if data.get("under") and sponsor is not None:
            under = BinaryNode.objects.filter(member__member_id=data["under"].upper()).first()
            if under is None or not hasattr(sponsor, "binary_node") or not under.is_descendant_of(sponsor.binary_node):
                self.add_error(None, "Invalid placement position.")
                under = None
            elif under.children.filter(position=data.get("leg")).exists():
                self.add_error("leg", "That position is already filled.")
        data["under_node"] = under
        return data


class ProfileForm(BootstrapMixin, forms.ModelForm):
    first_name = forms.CharField(max_length=150)
    last_name = forms.CharField(max_length=150, required=False)
    email = forms.EmailField()

    class Meta:
        model = MemberProfile
        fields = ["address", "city", "state", "pincode", "pan_number", "bank_account_name", "bank_account_number", "bank_ifsc", "upi_id"]
        widgets = {"address": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        self.fields["first_name"].initial = user.first_name
        self.fields["last_name"].initial = user.last_name
        self.fields["email"].initial = user.email
        self._style()

    def clean_email(self):
        e = self.cleaned_data["email"].lower()
        if User.objects.filter(email__iexact=e).exclude(pk=self.user.pk).exists():
            raise forms.ValidationError("Email already in use.")
        return e

    def _match(self, field, pattern, message, upper=True):
        value = (self.cleaned_data.get(field) or "").strip()
        value = value.upper() if upper else value
        if value and not re.fullmatch(pattern, value):
            raise forms.ValidationError(message)
        return value

    def clean_pan_number(self):
        return self._match("pan_number", r"[A-Z]{5}[0-9]{4}[A-Z]", "Enter a valid PAN, e.g. ABCDE1234F.")

    def clean_bank_ifsc(self):
        return self._match("bank_ifsc", r"[A-Z]{4}0[A-Z0-9]{6}", "Enter a valid 11-character IFSC code.")

    def clean_bank_account_number(self):
        return self._match("bank_account_number", r"[0-9]{9,18}", "Account number must be 9–18 digits.")

    def clean_upi_id(self):
        return self._match("upi_id", r"[A-Za-z0-9.\-_]{2,256}@[A-Za-z]{2,64}", "Enter a valid UPI ID, e.g. name@okbank.", upper=False)

    def clean(self):
        data = super().clean()
        if data.get("bank_account_number") and not data.get("bank_ifsc"):
            self.add_error("bank_ifsc", "IFSC is required with a bank account.")
        return data

    def save(self, commit=True):
        for f in ("first_name", "last_name", "email"):
            setattr(self.user, f, self.cleaned_data[f])
        self.user.save(update_fields=["first_name", "last_name", "email"])
        return super().save(commit)
