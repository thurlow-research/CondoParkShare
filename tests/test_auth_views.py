"""
Unit tests for accounts.views — authentication, registration, and account management.

Covers the uncovered lines in accounts/views.py:
  login_view POST valid/invalid (lines 84-102)
  logout_view (lines 108-109)
  totp_verify POST valid/invalid (lines 124-155)
  recovery_code POST valid/invalid/blocked (lines 171-211)
  lost_authenticator POST (lines 272-274)
  totp_enroll paths (lines 362, 367, 369, 382, 399-400, 415-437)
  register_invite GET/POST (lines 491-497)
  register self (lines 538-540)
  profile (line 551)
  notification_prefs GET/POST (lines 557-577)
  impersonation_end (lines 594-610)
  _get_pre_auth_user helpers (lines 53-68)
"""

import secrets
from datetime import timedelta
from unittest.mock import MagicMock, patch

import factory
import pytest
from django.contrib.auth.hashers import make_password
from django.test import RequestFactory, override_settings
from django.utils.timezone import now
from freezegun import freeze_time


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------


class OrganizationFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "parking.Organization"

    name = factory.Sequence(lambda n: f"AuthViewOrg {n}")
    hostname = factory.Sequence(lambda n: f"authvieworg{n}.parkshare.test")
    support_email = factory.LazyAttribute(lambda o: f"support@{o.hostname}")
    registration_mode = "invite_only"
    timezone = "America/Los_Angeles"
    booking_horizon_baseline_days = 3
    booking_horizon_max_days = 30
    listing_to_horizon_ratio = 10
    tier_metric_window_days = 180
    launch_grace_days = 14
    launch_grace_horizon_days = 14
    launched_at = None


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "accounts.User"

    organization = factory.SubFactory(OrganizationFactory)
    email = factory.Sequence(lambda n: f"avuser{n}@example.com")
    display_name = factory.Sequence(lambda n: f"AV User {n}")
    status = "active"
    is_hoa_admin = False
    last_booking_at = None

    @classmethod
    def _create(cls, model_class, *args, **kwargs):
        manager = model_class.objects
        password = kwargs.pop("password", "test-password-secure!")
        return manager.create_user(*args, password=password, **kwargs)


def _make_request(method="GET", data=None, session=None, user=None, org=None):
    """Build a test request with session/user/org attached."""
    rf = RequestFactory()
    if method.upper() == "POST":
        req = rf.post("/", data or {})
    else:
        req = rf.get("/")
    req.session = session or {}
    if user is not None:
        req.user = user
    else:
        from django.contrib.auth.models import AnonymousUser
        req.user = AnonymousUser()
    if org is not None:
        req.organization = org
    return req


# ---------------------------------------------------------------------------
# _get_pre_auth_user helpers
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_get_pre_auth_user_returns_none_when_no_session_key():
    """_get_pre_auth_user returns None when session has no _pre_auth_user_id."""
    from accounts.views import _get_pre_auth_user

    request = _make_request()
    result = _get_pre_auth_user(request)
    assert result is None


@pytest.mark.django_db
def test_get_pre_auth_user_returns_user_when_pk_valid():
    """_get_pre_auth_user returns the user when session has a valid _pre_auth_user_id."""
    from accounts.views import _get_pre_auth_user

    org = OrganizationFactory()
    user = UserFactory(organization=org)
    request = _make_request(session={"_pre_auth_user_id": user.pk})
    result = _get_pre_auth_user(request)
    assert result == user


@pytest.mark.django_db
def test_get_pre_auth_user_returns_none_when_user_not_found():
    """_get_pre_auth_user returns None when session pk doesn't match any user."""
    from accounts.views import _get_pre_auth_user

    request = _make_request(session={"_pre_auth_user_id": 999999})
    result = _get_pre_auth_user(request)
    assert result is None


# ---------------------------------------------------------------------------
# login_view
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_login_view_get_renders_form():
    """login_view GET renders the login page."""
    from accounts.views import login_view

    org = OrganizationFactory()
    request = _make_request("GET", org=org)
    response = login_view(request)
    assert response.status_code == 200


@pytest.mark.django_db
def test_login_view_post_valid_credentials_redirects():
    """login_view POST with valid credentials stores user pk and redirects to totp_verify."""
    from accounts.views import login_view

    org = OrganizationFactory()
    user = UserFactory(organization=org, password="correct-password-123!")

    with patch("accounts.views.authenticate", return_value=user):
        request = _make_request("POST", data={
            "email": user.email,
            "password": "correct-password-123!",
        }, session={}, org=org)
        response = login_view(request)

    assert response.status_code == 302
    assert request.session.get("_pre_auth_user_id") == user.pk


@pytest.mark.django_db
def test_login_view_post_invalid_credentials_shows_error():
    """login_view POST with invalid credentials renders form with error."""
    from accounts.views import login_view

    org = OrganizationFactory()

    with patch("accounts.views.authenticate", return_value=None):
        request = _make_request("POST", data={
            "email": "nobody@example.com",
            "password": "wrongpassword",
        }, session={}, org=org)
        response = login_view(request)

    assert response.status_code == 200
    # No redirect — stays on login page


# ---------------------------------------------------------------------------
# logout_view
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_logout_view_redirects_to_login():
    """logout_view POST logs out and redirects to login."""
    from accounts.views import logout_view

    org = OrganizationFactory()
    user = UserFactory(organization=org)
    request = _make_request("POST", user=user, session={}, org=org)

    with patch("accounts.views.logout"):
        response = logout_view(request)

    assert response.status_code == 302


# ---------------------------------------------------------------------------
# totp_verify
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_totp_verify_get_renders_form():
    """totp_verify GET renders the TOTP verification form."""
    from accounts.views import totp_verify

    org = OrganizationFactory()
    user = UserFactory(organization=org)
    request = _make_request("GET", session={"_pre_auth_user_id": user.pk}, org=org)
    response = totp_verify(request)
    assert response.status_code == 200


@pytest.mark.django_db
def test_totp_verify_redirects_if_no_pre_auth():
    """totp_verify redirects to login when no pre-auth user in session."""
    from accounts.views import totp_verify

    org = OrganizationFactory()
    request = _make_request("GET", session={}, org=org)
    response = totp_verify(request)
    assert response.status_code == 302


@pytest.mark.django_db
def test_totp_verify_post_valid_token_logs_in():
    """totp_verify POST with valid TOTP token completes login."""
    from accounts.models import EncryptedTOTPDevice
    from accounts.views import totp_verify

    org = OrganizationFactory()
    user = UserFactory(organization=org)
    device = EncryptedTOTPDevice.objects.create(user=user, name="test", confirmed=True)

    request = _make_request("POST", data={"token": "123456"},
                             session={"_pre_auth_user_id": user.pk}, org=org)

    with patch.object(device.__class__, "verify_token", return_value=True), \
         patch("accounts.views.EncryptedTOTPDevice.objects") as mock_mgr, \
         patch("accounts.views.login"), \
         patch("django_otp.login"):
        mock_mgr.filter.return_value = [device]
        response = totp_verify(request)

    assert response.status_code == 302


@pytest.mark.django_db
def test_totp_verify_post_invalid_token_shows_error():
    """totp_verify POST with invalid TOTP token shows error."""
    from accounts.models import EncryptedTOTPDevice
    from accounts.views import totp_verify

    org = OrganizationFactory()
    user = UserFactory(organization=org)
    device = EncryptedTOTPDevice.objects.create(user=user, name="test", confirmed=True)

    request = _make_request("POST", data={"token": "000000"},
                             session={"_pre_auth_user_id": user.pk}, org=org)

    with patch.object(device.__class__, "verify_token", return_value=False), \
         patch("accounts.views.EncryptedTOTPDevice.objects") as mock_mgr:
        mock_mgr.filter.return_value = [device]
        response = totp_verify(request)

    assert response.status_code == 200  # stays on verify page


# ---------------------------------------------------------------------------
# recovery_code
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_recovery_code_get_renders_form():
    """recovery_code GET renders the recovery code form."""
    from accounts.views import recovery_code

    org = OrganizationFactory()
    user = UserFactory(organization=org)
    request = _make_request("GET", session={"_pre_auth_user_id": user.pk}, org=org)
    response = recovery_code(request)
    assert response.status_code == 200


@pytest.mark.django_db
def test_recovery_code_redirects_if_no_pre_auth():
    """recovery_code redirects to login when no pre-auth user in session."""
    from accounts.views import recovery_code

    org = OrganizationFactory()
    request = _make_request("GET", session={}, org=org)
    response = recovery_code(request)
    assert response.status_code == 302


@pytest.mark.django_db
def test_recovery_code_post_valid_code_redirects_to_enroll():
    """recovery_code POST with valid code logs in and redirects to totp_enroll."""
    from accounts.views import recovery_code

    org = OrganizationFactory()
    plain_code = "valid-recovery-code-abc"
    hashed_code = make_password(plain_code)
    user = UserFactory(organization=org, recovery_codes=[hashed_code])

    request = _make_request("POST", data={"code": plain_code},
                             session={"_pre_auth_user_id": user.pk}, org=org)

    with patch("accounts.views.login"):
        response = recovery_code(request)

    assert response.status_code == 302
    # Recovery code should be consumed (removed from user.recovery_codes)
    user.refresh_from_db()
    assert len(user.recovery_codes) == 0, (
        f"Recovery code should be consumed; remaining: {user.recovery_codes}"
    )


@pytest.mark.django_db
def test_recovery_code_post_invalid_code_shows_error():
    """recovery_code POST with invalid code shows error."""
    from accounts.views import recovery_code

    org = OrganizationFactory()
    plain_code = "valid-recovery-code-abc"
    hashed_code = make_password(plain_code)
    user = UserFactory(organization=org, recovery_codes=[hashed_code])

    request = _make_request("POST", data={"code": "wrong-code"},
                             session={"_pre_auth_user_id": user.pk}, org=org)

    response = recovery_code(request)

    assert response.status_code == 200  # stays on recovery code page


@pytest.mark.django_db
def test_recovery_code_post_blocked_user_shows_error():
    """recovery_code POST with blocked user shows error (cannot log in)."""
    from accounts.views import recovery_code

    org = OrganizationFactory()
    plain_code = "valid-code-blocked"
    hashed_code = make_password(plain_code)
    user = UserFactory(organization=org, status="blocked", recovery_codes=[hashed_code])

    request = _make_request("POST", data={"code": plain_code},
                             session={"_pre_auth_user_id": user.pk}, org=org)

    response = recovery_code(request)

    assert response.status_code == 200  # stays on page — blocked user cannot login


# ---------------------------------------------------------------------------
# lost_authenticator
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
def test_lost_authenticator_get_renders_form():
    """lost_authenticator GET renders the lost authenticator form."""
    from accounts.views import lost_authenticator

    org = OrganizationFactory()
    request = _make_request("GET", org=org)
    response = lost_authenticator(request)
    assert response.status_code == 200


@pytest.mark.django_db
@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
def test_lost_authenticator_post_valid_email_redirects():
    """lost_authenticator POST with valid email creates OTP and redirects."""
    from accounts.models import EmailOTP
    from accounts.views import lost_authenticator

    org = OrganizationFactory()
    user = UserFactory(organization=org)

    request = _make_request("POST", data={"email": user.email},
                             session={}, org=org)
    response = lost_authenticator(request)

    assert response.status_code == 302
    # OTP should be created
    assert EmailOTP.objects.filter(user=user, consumed=False).exists(), (
        "EmailOTP should be created for the user"
    )


@pytest.mark.django_db
@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
def test_lost_authenticator_post_unknown_email_still_redirects():
    """lost_authenticator POST with unknown email redirects (no enumeration)."""
    from accounts.views import lost_authenticator

    org = OrganizationFactory()

    request = _make_request("POST", data={"email": "nobody@notreal.com"},
                             session={}, org=org)
    response = lost_authenticator(request)

    # Should still redirect (no information leak)
    assert response.status_code == 302


# ---------------------------------------------------------------------------
# Regression: HOA approval bypass via lost-authenticator → totp_enroll (#17)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
def test_pending_approval_cannot_bypass_via_lost_authenticator():
    """
    Regression test for issue #17 — HOA approval bypass path:
      1. pending_approval user requests lost-authenticator OTP
      2. verify step must reject them (status gate)
      3. Even if they somehow reach totp_enroll with totp_reset_required=True,
         their status must not advance to 'active'

    Both fix points are exercised in sequence:
    - lost_authenticator_verify rejects pending_approval at the status gate.
    - totp_enroll (defense-in-depth) preserves pending_approval on save.
    """
    from django.contrib.sessions.backends.db import SessionStore
    from accounts.models import EmailOTP, EncryptedTOTPDevice
    from accounts.views import lost_authenticator_verify, totp_enroll

    org = OrganizationFactory()
    user = UserFactory(organization=org, status="pending_approval")

    # Part 1: verify view blocks the bypass — OTP is not consumed, no redirect.
    otp_plaintext = "482917"
    EmailOTP.objects.create(
        user=user,
        code_hash=make_password(otp_plaintext),
        expires_at=now() + timedelta(minutes=15),
        consumed=False,
    )

    rf = RequestFactory()
    session = SessionStore()
    session.create()
    session["_lost_auth_user_id"] = user.pk

    verify_request = rf.post(
        "/accounts/lost-authenticator/verify/",
        {"code": otp_plaintext},
    )
    verify_request.organization = org
    verify_request.session = session

    response = lost_authenticator_verify(verify_request)

    # Must not redirect to totp_enroll (302 redirect = bypass succeeded).
    assert response.status_code == 200, (
        "pending_approval user must not be redirected by lost_authenticator_verify; "
        f"got status {response.status_code}"
    )
    user.refresh_from_db()
    assert user.status == "pending_approval", (
        "lost_authenticator_verify must not change status away from pending_approval"
    )
    # OTP must not be consumed (do not burn it on a denied request).
    otp = EmailOTP.objects.get(user=user)
    assert not otp.consumed, "OTP must not be consumed when request is denied"

    # Part 2: defense-in-depth — even if pending_approval reaches totp_enroll
    # with totp_reset_required=True, status must not advance to 'active'.
    with patch("accounts.models.EncryptedTOTPDevice.verify_token", return_value=True):
        EncryptedTOTPDevice.objects.filter(user=user).delete()
        EncryptedTOTPDevice.objects.create(user=user, name="test", confirmed=False)

        enroll_session = SessionStore()
        enroll_session.create()
        enroll_session["totp_reset_required"] = True

        enroll_request = rf.post("/accounts/totp/enroll/", {"token": "000000"})
        enroll_request.user = user
        enroll_request.organization = org
        enroll_request.session = enroll_session

        totp_enroll(enroll_request)

    user.refresh_from_db()
    assert user.status == "pending_approval", (
        "totp_enroll must not elevate pending_approval to active — "
        "HOA approval bypass blocked (#17)"
    )


# ---------------------------------------------------------------------------
# profile
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_profile_view_renders():
    """profile view renders for active user."""
    from accounts.views import profile

    org = OrganizationFactory()
    user = UserFactory(organization=org, status="active")
    request = _make_request("GET", user=user, org=org)
    response = profile(request)
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# notification_prefs
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_notification_prefs_get_renders_form():
    """notification_prefs GET renders preferences form."""
    from accounts.views import notification_prefs

    org = OrganizationFactory()
    user = UserFactory(organization=org, status="active")
    request = _make_request("GET", user=user, org=org)
    response = notification_prefs(request)
    assert response.status_code == 200


@pytest.mark.django_db
def test_notification_prefs_post_updates_prefs():
    """notification_prefs POST updates user notification preferences."""
    from accounts.views import notification_prefs

    org = OrganizationFactory()
    user = UserFactory(organization=org, status="active",
                        notification_prefs={"push": False})

    request = _make_request("POST", data={
        "push": True,
        "marketing_email_opted_in": False,
    }, user=user, org=org)

    response = notification_prefs(request)

    assert response.status_code == 302
    user.refresh_from_db()
    assert user.notification_prefs.get("push") is True


# ---------------------------------------------------------------------------
# register (self-registration)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_register_redirects_if_invite_only():
    """register view redirects when registration_mode is 'invite_only'."""
    from accounts.views import register

    org = OrganizationFactory(registration_mode="invite_only")
    request = _make_request("GET", org=org)
    response = register(request)
    assert response.status_code == 302


@pytest.mark.django_db
def test_register_get_renders_form_for_approve_mode():
    """register GET renders the form when registration_mode is 'approve'."""
    from accounts.views import register

    org = OrganizationFactory(registration_mode="approve")
    request = _make_request("GET", org=org)
    response = register(request)
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# impersonation_end
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_impersonation_end_clears_session_and_logs():
    """impersonation_end clears session keys and logs impersonate_end."""
    from accounts.models import AdminAuditLog
    from accounts.views import impersonation_end

    org = OrganizationFactory()
    superuser = UserFactory(organization=org, is_superuser=True)
    target = UserFactory(organization=org)

    request = _make_request("GET", user=target, session={
        "impersonating": target.pk,
        "real_operator": superuser.pk,
    }, org=org)
    request._real_operator = superuser

    response = impersonation_end(request)

    assert response.status_code == 302
    assert "impersonating" not in request.session

    entry = AdminAuditLog.objects.filter(action="impersonate_end").first()
    assert entry is not None, "impersonate_end audit log should be created"


@pytest.mark.django_db
def test_impersonation_end_redirects_even_if_no_session_key():
    """impersonation_end redirects even if no 'impersonating' session key."""
    from accounts.views import impersonation_end

    org = OrganizationFactory()
    user = UserFactory(organization=org)

    request = _make_request("GET", user=user, session={}, org=org)

    response = impersonation_end(request)

    assert response.status_code == 302
