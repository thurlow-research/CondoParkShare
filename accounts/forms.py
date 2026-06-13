"""
accounts.forms — authentication, registration, and preference forms.
"""

from django import forms
from django.contrib.auth import get_user_model

User = get_user_model()


# ---------------------------------------------------------------------------
# Login / TOTP
# ---------------------------------------------------------------------------


class LoginForm(forms.Form):
    email = forms.EmailField(
        label="Email address",
        widget=forms.EmailInput(attrs={"autofocus": True, "autocomplete": "username"}),
    )
    password = forms.CharField(
        label="Password",
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
    )


class TOTPVerifyForm(forms.Form):
    token = forms.CharField(
        label="Authenticator code",
        max_length=6,
        min_length=6,
        widget=forms.TextInput(
            attrs={
                "autocomplete": "one-time-code",
                "inputmode": "numeric",
                "pattern": "[0-9]{6}",
                "autofocus": True,
            }
        ),
    )


# ---------------------------------------------------------------------------
# Recovery / lost authenticator
# ---------------------------------------------------------------------------


class RecoveryCodeForm(forms.Form):
    code = forms.CharField(
        label="Recovery code",
        max_length=64,
        widget=forms.TextInput(attrs={"autocomplete": "off"}),
    )


class LostAuthenticatorForm(forms.Form):
    email = forms.EmailField(
        label="Email address",
        widget=forms.EmailInput(attrs={"autofocus": True, "autocomplete": "username"}),
    )


class LostAuthenticatorVerifyForm(forms.Form):
    code = forms.CharField(
        label="One-time code",
        max_length=6,
        min_length=6,
        widget=forms.TextInput(
            attrs={
                "autocomplete": "one-time-code",
                "inputmode": "numeric",
                "pattern": "[0-9]{6}",
                "autofocus": True,
            }
        ),
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

_PRIVACY_NOTICE_TEXT = (
    "By registering, you agree that CondoParkShare will store your name, "
    "email address, and phone number to manage your parking access. "
    "This information is processed on the basis of your consent and our "
    "legitimate interest in operating the service. See our Privacy Policy "
    "for details on retention, your rights of access/erasure, and how to "
    "withdraw consent."
)


class InviteRegistrationForm(forms.Form):
    """
    Used when a resident registers via an invite link.  ``unit_number`` is
    pre-filled from the invite and rendered as read-only.
    """

    email = forms.EmailField(
        label="Email address",
        widget=forms.EmailInput(attrs={"autocomplete": "username"}),
    )
    display_name = forms.CharField(
        label="Full name",
        max_length=255,
    )
    password = forms.CharField(
        label="Password",
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
    password_confirm = forms.CharField(
        label="Confirm password",
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
    unit_number = forms.CharField(
        label="Unit number",
        max_length=50,
        required=False,
        widget=forms.TextInput(attrs={"readonly": True}),
    )
    marketing_email_opted_in = forms.BooleanField(
        label="I agree to receive marketing emails",
        required=False,
    )
    # Non-editable privacy/consent notice rendered as help text in templates.
    # Satisfies GDPR Art. 13 / CCPA §1798.100 disclosure at point of collection.
    privacy_notice = forms.CharField(
        label="",
        required=False,
        widget=forms.HiddenInput(),
        help_text=_PRIVACY_NOTICE_TEXT,
    )

    def __init__(self, *args, organization=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._organization = organization

    def clean(self):
        cleaned = super().clean()
        password = cleaned.get("password")
        password_confirm = cleaned.get("password_confirm")

        if password and password_confirm and password != password_confirm:
            raise forms.ValidationError("Passwords do not match.")

        email = cleaned.get("email")
        if email and self._organization:
            if User.objects.filter(
                organization=self._organization,
                email=email,
            ).exists():
                raise forms.ValidationError(
                    "An account with this email already exists for this building."
                )

        return cleaned


class SelfRegistrationForm(forms.Form):
    """
    Used when self-registration is enabled (mode = 'approve' or 'both').
    No password is set at this stage — it is set after HOA approval.
    """

    email = forms.EmailField(
        label="Email address",
        widget=forms.EmailInput(attrs={"autocomplete": "username"}),
    )
    display_name = forms.CharField(
        label="Full name",
        max_length=255,
    )
    unit_number = forms.CharField(
        label="Unit number",
        max_length=50,
        required=False,
    )
    marketing_email_opted_in = forms.BooleanField(
        label="I agree to receive marketing emails",
        required=False,
    )
    # Non-editable privacy/consent notice rendered as help text in templates.
    # Satisfies GDPR Art. 13 / CCPA §1798.100 disclosure at point of collection.
    privacy_notice = forms.CharField(
        label="",
        required=False,
        widget=forms.HiddenInput(),
        help_text=_PRIVACY_NOTICE_TEXT,
    )

    def __init__(self, *args, organization=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._organization = organization

    def clean_email(self):
        email = self.cleaned_data.get("email")
        if email and self._organization:
            if User.objects.filter(
                organization=self._organization,
                email=email,
            ).exists():
                raise forms.ValidationError(
                    "An account with this email already exists for this building."
                )
        return email


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------


class NotificationPrefsForm(forms.Form):
    push = forms.BooleanField(
        label="Enable push notifications",
        required=False,
    )
    marketing_email_opted_in = forms.BooleanField(
        label="Receive marketing emails",
        required=False,
    )
