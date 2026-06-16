"""
accounts.views — authentication, registration, and account management views.

Auth flow summary
-----------------
1. login_view        → stores _pre_auth_user_id in session; redirects to totp_verify
2. totp_verify       → verifies TOTP token; calls django_otp.login + django login
3. recovery_code     → alternative to TOTP: consumes a recovery code; redirects to totp_enroll
4. lost_authenticator        → sends email OTP
5. lost_authenticator_verify → validates email OTP; redirects to totp_enroll
6. totp_enroll       → generates/confirms TOTPDevice; generates recovery codes; activates user
"""

import io
import secrets
from datetime import timedelta

import django_otp
import segno
from django.contrib.auth import authenticate, get_user_model, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.hashers import check_password, make_password
from django.core.mail import send_mail
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.timezone import now
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit

from accounts.decorators import active_required
from accounts.forms import (
    InviteRegistrationForm,
    LoginForm,
    LostAuthenticatorForm,
    LostAuthenticatorVerifyForm,
    NotificationPrefsForm,
    RecoveryCodeForm,
    SelfRegistrationForm,
    TOTPVerifyForm,
)
from accounts.models import AdminAuditLog, EmailOTP, EncryptedTOTPDevice, Invite

User = get_user_model()


# ---------------------------------------------------------------------------
# Rate-limit key helpers
# ---------------------------------------------------------------------------


def _key_pre_auth_user(group, request):
    """Rate-limit key: the pre-auth user PK stored in session, or fall back to
    IP.  Binds brute-force limits to the target account, not just the origin
    IP, so distributed attacks from many IPs against one account are still
    throttled."""
    user_pk = request.session.get("_pre_auth_user_id")
    if user_pk is not None:
        return f"pre_auth:{user_pk}"
    return request.META.get("REMOTE_ADDR", "unknown")


def _key_lost_auth_user(group, request):
    """Rate-limit key: the lost-auth user PK stored in session, or IP."""
    user_pk = request.session.get("_lost_auth_user_id")
    if user_pk is not None:
        return f"lost_auth:{user_pk}"
    return request.META.get("REMOTE_ADDR", "unknown")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _qr_svg_inline(data: str) -> str:
    """Return an inline SVG string for *data* generated locally via segno.
    The TOTP secret must never leave the server — no third-party QR service."""
    buf = io.BytesIO()
    segno.make(data, error="M").save(buf, kind="svg", xmldecl=False, svgns=True, title="", nl=False, scale=4)
    return buf.getvalue().decode("utf-8")


def _get_pre_auth_user(request):
    """Return the User stored in the pre-auth session key, or None."""
    user_pk = request.session.get("_pre_auth_user_id")
    if user_pk is None:
        return None
    try:
        return User.objects.get(pk=user_pk)
    except User.DoesNotExist:
        return None


# ---------------------------------------------------------------------------
# Login / logout
# ---------------------------------------------------------------------------


@ratelimit(key="ip", rate="5/m", method=["POST"], block=True)
def login_view(request):
    """
    Email + password first factor.

    On success: store user pk in session as '_pre_auth_user_id' and redirect
    to totp_verify.  On failure: render form with a *generic* error (do not
    reveal whether the email exists).
    """
    if request.method == "POST":
        form = LoginForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data["email"]
            password = form.cleaned_data["password"]

            # authenticate() uses the multi-tenant backend — it scopes to the
            # current organisation via TenantMiddleware.
            user = authenticate(request, username=email, password=password)
            if user is not None:
                # First factor passed — store PK; do NOT call django login yet.
                request.session["_pre_auth_user_id"] = user.pk
                return redirect("totp_verify")
            else:
                form.add_error(None, "Invalid credentials.")
    else:
        form = LoginForm()

    return render(request, "accounts/login.html", {"form": form})


@require_POST
def logout_view(request):
    """Clear the session and redirect to login."""
    logout(request)
    return redirect("login")


# ---------------------------------------------------------------------------
# TOTP verify
# ---------------------------------------------------------------------------


@ratelimit(key=_key_pre_auth_user, rate="5/5m", method=["POST"], block=True)
def totp_verify(request):
    """
    Second factor: TOTP code verification.

    Reads the user from '_pre_auth_user_id'.  On success: fully authenticates
    the session via django_otp.login() + django login().
    """
    user = _get_pre_auth_user(request)
    if user is None:
        return redirect("login")

    if request.method == "POST":
        form = TOTPVerifyForm(request.POST)
        if form.is_valid():
            token = form.cleaned_data["token"]
            # Find a confirmed TOTP device for this user and verify the token.
            devices = EncryptedTOTPDevice.objects.filter(user=user, confirmed=True)
            matched_device = None
            for device in devices:
                if device.verify_token(token):
                    matched_device = device
                    break

            if matched_device is not None:
                # Full two-factor login.
                # auth.login() must come first so request.user carries the real
                # user pk before django_otp.login() checks device.user_id == request.user.pk.
                del request.session["_pre_auth_user_id"]
                login(request, user, backend="django.contrib.auth.backends.ModelBackend")
                django_otp.login(request, matched_device)
                return redirect("book_request")
            else:
                form.add_error("token", "Invalid code. Please try again.")
    else:
        form = TOTPVerifyForm()

    return render(request, "accounts/totp_verify.html", {"form": form})


# ---------------------------------------------------------------------------
# Recovery code
# ---------------------------------------------------------------------------


@ratelimit(key=_key_pre_auth_user, rate="3/10m", method=["POST"], block=True)
def recovery_code(request):
    """
    First-line TOTP fallback: consume one of the user's hashed recovery codes.

    On success: remove the matched code from user.recovery_codes, set
    session['totp_reset_required']=True, log the user in, and redirect to
    totp_enroll.
    """
    user = _get_pre_auth_user(request)
    if user is None:
        return redirect("login")

    if request.method == "POST":
        form = RecoveryCodeForm(request.POST)
        if form.is_valid():
            submitted = form.cleaned_data["code"]

            # select_for_update + atomic prevents two concurrent requests from
            # both passing check_password before either save() runs, which
            # would let a single code grant two logins.
            with transaction.atomic():
                locked_user = User.objects.select_for_update().get(pk=user.pk)

                matched_index = None
                for i, hashed in enumerate(locked_user.recovery_codes):
                    if check_password(submitted, hashed):
                        matched_index = i
                        break

                if matched_index is not None:
                    # Blocked accounts must not be able to log in via any path.
                    if locked_user.status == "blocked":
                        form.add_error("code", "Invalid recovery code.")
                        return render(request, "accounts/recovery_code.html", {"form": form})

                    # Consume the recovery code (single-use).
                    codes = list(locked_user.recovery_codes)
                    codes.pop(matched_index)
                    locked_user.recovery_codes = codes
                    locked_user.save(update_fields=["recovery_codes"])

            if matched_index is not None:
                # Force TOTP re-enrollment.
                del request.session["_pre_auth_user_id"]
                request.session["totp_reset_required"] = True
                login(
                    request,
                    locked_user,
                    backend="django.contrib.auth.backends.ModelBackend",
                )
                return redirect("totp_enroll")
            else:
                form.add_error("code", "Invalid recovery code.")
    else:
        form = RecoveryCodeForm()

    return render(request, "accounts/recovery_code.html", {"form": form})


# ---------------------------------------------------------------------------
# Lost authenticator (email OTP)
# ---------------------------------------------------------------------------


@ratelimit(key="ip", rate="3/15m", method=["POST"], block=True)
def lost_authenticator(request):
    """
    Backstop flow when both TOTP and recovery codes are unavailable.

    POST: Looks up the email internally (but never reveals whether it exists).
    Invalidates prior non-consumed OTPs, creates a new one, sends it by email.
    Always returns the same response regardless of whether the email is found.
    """
    if request.method == "POST":
        form = LostAuthenticatorForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data["email"]
            # Enumerate-safe: look up silently; proceed to the same page.
            try:
                user = User.objects.get(
                    email=email,
                    organization=request.organization,
                )
                # Invalidate unexpired, unconsumed OTPs for this user.
                EmailOTP.objects.filter(
                    user=user,
                    consumed=False,
                    expires_at__gt=now(),
                ).update(consumed=True)

                otp_code = f"{secrets.randbelow(1000000):06d}"
                code_hash = make_password(otp_code)
                EmailOTP.objects.create(
                    user=user,
                    code_hash=code_hash,
                    expires_at=now() + timedelta(minutes=15),
                )

                send_mail(
                    subject="Your CondoParkShare one-time code",
                    message=(
                        f"Your one-time code is: {otp_code}\n\n"
                        "This code expires in 15 minutes.\n\n"
                        "If you did not request this, you can safely ignore this email."
                    ),
                    from_email=None,  # uses DEFAULT_FROM_EMAIL
                    recipient_list=[user.email],
                    fail_silently=True,
                )
                # Bind the pending recovery to this specific user so the verify
                # view can filter by user_id rather than scanning all org OTPs.
                request.session["_lost_auth_user_id"] = user.pk
            except User.DoesNotExist:
                pass  # Enumerate-safe: identical response path.

            # Always redirect to verify page — do not reveal whether email exists.
            return redirect("lost_authenticator_verify")
    else:
        form = LostAuthenticatorForm()

    return render(request, "accounts/lost_authenticator.html", {"form": form})


@ratelimit(key=_key_lost_auth_user, rate="5/15m", method=["POST"], block=True)
def lost_authenticator_verify(request):
    """
    Validate the email OTP sent by lost_authenticator.

    On success: consume OTP, set totp_reset_required, partially authenticate
    user, redirect to totp_enroll.
    """
    if request.method == "POST":
        form = LostAuthenticatorVerifyForm(request.POST)
        if form.is_valid():
            submitted = form.cleaned_data["code"]

            # Require that the verify page was reached via the email-submit step
            # (which stores the user PK). Guards against direct navigation and
            # cross-user OTP consumption within an org (#22).
            lost_auth_user_id = request.session.get("_lost_auth_user_id")

            # Find non-consumed, non-expired OTP for the specific user who
            # initiated the flow — not any OTP in the org.
            # select_for_update + atomic prevents two concurrent requests from
            # both seeing consumed=False before either sets it to True.
            matched_otp = None
            matched_user = None
            with transaction.atomic():
                candidates = EmailOTP.objects.none()
                if lost_auth_user_id is not None:
                    candidates = (
                        EmailOTP.objects.select_for_update()
                        .filter(
                            user_id=lost_auth_user_id,
                            consumed=False,
                            expires_at__gt=now(),
                            user__organization=request.organization,
                        )
                        .select_related("user")
                    )

                for otp in candidates:
                    if check_password(submitted, otp.code_hash):
                        matched_otp = otp
                        matched_user = otp.user
                        break

                if matched_otp is not None:
                    # Blocked or unapproved accounts must not recover via this
                    # path — HOA approval cannot be bypassed via TOTP reset (#17).
                    if matched_user.status in ("blocked", "pending_approval"):
                        # Do not consume the code — just deny.
                        matched_otp = None
                    else:
                        matched_otp.consumed = True
                        matched_otp.save(update_fields=["consumed"])

            if matched_otp is not None:
                request.session.pop("_lost_auth_user_id", None)
                request.session["totp_reset_required"] = True
                login(
                    request,
                    matched_user,
                    backend="django.contrib.auth.backends.ModelBackend",
                )
                return redirect("totp_enroll")
            elif matched_user is not None and matched_user.status in (
                "blocked",
                "pending_approval",
            ):
                form.add_error("code", "Invalid or expired code.")
            else:
                form.add_error("code", "Invalid or expired code.")
    else:
        form = LostAuthenticatorVerifyForm()

    return render(request, "accounts/lost_authenticator_verify.html", {"form": form})


# ---------------------------------------------------------------------------
# TOTP enroll
# ---------------------------------------------------------------------------


def totp_enroll(request):
    """
    TOTP device enrollment.

    Accessible when:
    - request.user.status == 'pending_totp', OR
    - session['totp_reset_required'] is True (lost-authenticator or recovery-code path)

    GET:  Create an unconfirmed EncryptedTOTPDevice and render the QR code.
    POST: Verify the submitted token.  On success:
          - Confirm the device.
          - Generate 10 recovery codes; hash each; store hashed list on user.
          - Show plaintext codes ONCE.
          - Set user.status = 'active'.
    """
    if not request.user.is_authenticated:
        return redirect("login")

    # Gate: only pending_totp users or forced re-enrollment.
    is_reset = request.session.get("totp_reset_required", False)
    if request.user.status not in ("pending_totp", "active") and not is_reset:
        return redirect("login")
    if request.user.status == "active" and not is_reset:
        return redirect("book_request")

    if request.method == "POST":
        token = request.POST.get("token", "").strip()

        # Find the unconfirmed device created during the GET phase.
        device = EncryptedTOTPDevice.objects.filter(
            user=request.user,
            confirmed=False,
        ).first()

        if device is None:
            # Edge case: no pending device — restart GET flow.
            return redirect("totp_enroll")

        if device.verify_token(token):
            device.confirmed = True
            device.save()

            # Delete any other (old) TOTP devices for this user.
            EncryptedTOTPDevice.objects.filter(user=request.user).exclude(pk=device.pk).delete()

            # Generate 10 single-use recovery codes.
            plaintext_codes = [secrets.token_urlsafe(10) for _ in range(10)]
            hashed_codes = [make_password(code) for code in plaintext_codes]
            request.user.recovery_codes = hashed_codes
            # Only advance pending_totp → active. Never elevate pending_approval
            # (defense-in-depth: verify view already blocks this path, #17).
            enroll_fields = ["recovery_codes"]
            if request.user.status == "pending_totp":
                request.user.status = "active"
                enroll_fields.append("status")
            request.user.save(update_fields=enroll_fields)

            # Clear re-enrollment flag.
            request.session.pop("totp_reset_required", None)

            return render(
                request,
                "accounts/totp_enroll_complete.html",
                {
                    "recovery_codes": plaintext_codes,
                },
            )
        else:
            # Wrong token — keep device, show error.
            device_url = device.config_url
            return render(
                request,
                "accounts/totp_enroll.html",
                {
                    "qr_svg": _qr_svg_inline(device_url),
                    "device_url": device_url,
                    "error": "Invalid code. Scan the QR code and try again.",
                },
            )

    else:  # GET
        # Remove any prior unconfirmed devices before creating a fresh one.
        EncryptedTOTPDevice.objects.filter(user=request.user, confirmed=False).delete()

        device = EncryptedTOTPDevice.objects.create(
            user=request.user,
            name=f"{request.user.email} TOTP",
            confirmed=False,
        )
        device_url = device.config_url

        return render(
            request,
            "accounts/totp_enroll.html",
            {
                "qr_svg": _qr_svg_inline(device_url),
                "device_url": device_url,
            },
        )


# ---------------------------------------------------------------------------
# Registration — invite
# ---------------------------------------------------------------------------


def register_invite(request, code):
    """
    Invite-based registration (registration_mode = 'invite_only' or 'both').

    GET:  Load the invite; validate it; pre-fill unit_number (readonly).
    POST: Validate InviteRegistrationForm; create User(status='pending_totp');
          increment invite.use_count; redirect to totp_enroll.
    """
    invite = get_object_or_404(Invite, code=code, organization=request.organization)

    if not invite.is_valid():
        return render(request, "accounts/invite_invalid.html", status=410)

    if request.method == "POST":
        form = InviteRegistrationForm(
            request.POST,
            organization=request.organization,
        )
        if form.is_valid():
            # select_for_update + atomic prevents two concurrent registrations
            # from both passing is_valid() before either increments use_count,
            # which would allow a max_uses=1 invite to create two accounts.
            with transaction.atomic():
                locked_invite = Invite.objects.select_for_update().get(pk=invite.pk)
                if not locked_invite.is_valid():
                    return render(request, "accounts/invite_invalid.html", status=410)

                user = User.objects.create_user(
                    email=form.cleaned_data["email"],
                    organization=request.organization,
                    display_name=form.cleaned_data["display_name"],
                    password=form.cleaned_data["password"],
                    status="pending_totp",
                    marketing_email_opted_in=form.cleaned_data.get("marketing_email_opted_in", False),
                )

                locked_invite.use_count += 1
                locked_invite.consumed_by = user
                locked_invite.consumed_at = now()
                locked_invite.save(update_fields=["use_count", "consumed_by", "consumed_at"])

            # Partially authenticate so totp_enroll can access request.user.
            login(request, user, backend="django.contrib.auth.backends.ModelBackend")
            return redirect("totp_enroll")
    else:
        initial = {"unit_number": invite.unit_number}
        form = InviteRegistrationForm(
            initial=initial,
            organization=request.organization,
        )

    return render(
        request,
        "accounts/register_invite.html",
        {
            "form": form,
            "invite": invite,
        },
    )


# ---------------------------------------------------------------------------
# Registration — self-register
# ---------------------------------------------------------------------------


def register(request):
    """
    Self-registration (registration_mode = 'approve' or 'both').

    Creates a User with status='pending_approval'.  The HOA admin must approve
    the account before it becomes active.
    """
    org = request.organization
    if org.registration_mode not in ("approve", "both"):
        return redirect("login")

    if request.method == "POST":
        form = SelfRegistrationForm(request.POST, organization=org)
        if form.is_valid():
            User.objects.create_user(
                email=form.cleaned_data["email"],
                organization=org,
                display_name=form.cleaned_data["display_name"],
                password=None,  # No password until approved
                status="pending_approval",
                marketing_email_opted_in=form.cleaned_data.get("marketing_email_opted_in", False),
            )
            return render(request, "accounts/register_pending.html")
    else:
        form = SelfRegistrationForm(organization=org)

    return render(request, "accounts/register.html", {"form": form})


# ---------------------------------------------------------------------------
# Profile and preferences
# ---------------------------------------------------------------------------


@active_required
def profile(request):
    """Render user profile information."""
    return render(request, "accounts/profile.html", {"user": request.user})


@active_required
def notification_prefs(request):
    """View/update notification preferences."""
    user = request.user
    if request.method == "POST":
        form = NotificationPrefsForm(request.POST)
        if form.is_valid():
            prefs = user.notification_prefs.copy()
            prefs["push"] = form.cleaned_data["push"]
            user.notification_prefs = prefs
            user.marketing_email_opted_in = form.cleaned_data["marketing_email_opted_in"]
            user.save(update_fields=["notification_prefs", "marketing_email_opted_in"])
            return redirect("notification_prefs")
    else:
        form = NotificationPrefsForm(
            initial={
                "push": user.notification_prefs.get("push", False),
                "marketing_email_opted_in": user.marketing_email_opted_in,
            }
        )

    return render(request, "accounts/notification_prefs.html", {"form": form})


# ---------------------------------------------------------------------------
# Impersonation end (referenced in parkshare/urls.py)
# ---------------------------------------------------------------------------


@login_required
def impersonation_end(request):
    """
    End an active impersonation session.

    Clears the 'impersonating' and 'real_operator' session keys and redirects
    the operator back to the admin index. Logs the end of the impersonation to
    AdminAuditLog.
    """
    if "impersonating" in request.session:
        # Resolve the real operator via the request attribute set by
        # ImpersonationMiddleware before this view runs.
        real_operator = getattr(request, "_real_operator", None)

        AdminAuditLog.objects.create(
            organization=getattr(request.user, "organization", None),
            actor=real_operator or request.user,
            action="impersonate_end",
            target_type="user",
            target_id=request.session["impersonating"],
        )

        del request.session["impersonating"]
        request.session.pop("real_operator", None)

    return redirect("admin:index")
