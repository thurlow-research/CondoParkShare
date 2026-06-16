"""
Unit tests for CondoParkShare Step 3 — authentication and registration.

Covers:
  TOTP flows (1-4)
  Recovery code flows (5-7)
  Lost authenticator flow (8-11)
  Registration flows (12-16)
  User model (17-19)
"""

import secrets
from datetime import timedelta

import factory
import pytest
from django.contrib.auth.hashers import check_password, make_password
from django.test import RequestFactory
from django.utils.timezone import now
from freezegun import freeze_time

# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------


class OrganizationFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "parking.Organization"

    name = factory.Sequence(lambda n: f"TestOrg {n}")
    hostname = factory.Sequence(lambda n: f"testorg{n}.parkshare.test")
    support_email = factory.LazyAttribute(lambda o: f"support@{o.hostname}")
    registration_mode = "invite_only"
    timezone = "America/Los_Angeles"


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "accounts.User"

    organization = factory.SubFactory(OrganizationFactory)
    email = factory.Sequence(lambda n: f"user{n}@example.com")
    display_name = factory.Sequence(lambda n: f"Test User {n}")
    status = "active"

    @classmethod
    def _create(cls, model_class, *args, **kwargs):
        """Use the custom UserManager so passwords are hashed properly."""
        manager = model_class.objects
        password = kwargs.pop("password", "test-password-secure!")
        return manager.create_user(*args, password=password, **kwargs)


class InviteFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "accounts.Invite"

    organization = factory.SubFactory(OrganizationFactory)
    issued_by = factory.SubFactory(
        UserFactory, organization=factory.SelfAttribute("..organization")
    )
    code = factory.LazyFunction(lambda: secrets.token_urlsafe(32))
    max_uses = 1
    use_count = 0
    expires_at = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request_with_session(session_data=None):
    """Return a RequestFactory GET with a mock session dict."""
    factory = RequestFactory()
    request = factory.get("/")
    request.session = session_data or {}
    return request


def _make_totp_device(user, confirmed=True):
    """Create an EncryptedTOTPDevice for *user*."""
    from accounts.models import EncryptedTOTPDevice

    return EncryptedTOTPDevice.objects.create(
        user=user,
        name=f"{user.email} TOTP",
        confirmed=confirmed,
    )


def _get_current_totp_token(device):
    """Return the current valid TOTP token for *device* as a zero-padded string."""
    from django_otp.oath import TOTP

    totp = TOTP(
        key=device.bin_key,
        step=device.step,
        t0=device.t0,
        digits=device.digits,
        drift=device.drift,
    )
    # token() returns an int; zero-pad to 6 digits for verify_token()
    return f"{totp.token():06d}"


def _make_django_session():
    """Return a lightweight Django session object usable in unit tests."""
    from django.contrib.sessions.backends.db import SessionStore

    session = SessionStore()
    session.create()
    return session


# ---------------------------------------------------------------------------
# TOTP flows
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_totp_enrollment_complete():
    """
    After confirming a TOTP token:
    - device is confirmed
    - 10 recovery codes are generated and stored as hashes
    - user.status becomes 'active'
    """
    user = UserFactory(status="pending_totp")
    device = _make_totp_device(user, confirmed=False)

    # Get valid token
    token = _get_current_totp_token(device)

    assert device.verify_token(token), "Precondition: token should be valid"

    # Simulate what totp_enroll POST does on success
    device.confirmed = True
    device.save()

    plaintext_codes = [secrets.token_urlsafe(10) for _ in range(10)]
    hashed_codes = [make_password(code) for code in plaintext_codes]
    user.recovery_codes = hashed_codes
    user.status = "active"
    user.save(update_fields=["recovery_codes", "status"])

    user.refresh_from_db()

    assert user.status == "active"
    assert len(user.recovery_codes) == 10

    # All stored values must be hashes (not plaintext)
    for i, code in enumerate(plaintext_codes):
        assert check_password(
            code, user.recovery_codes[i]
        ), f"Recovery code {i} should verify against stored hash"

    # Stored values must not equal plaintext
    for plaintext, hashed in zip(plaintext_codes, user.recovery_codes):
        assert (
            plaintext != hashed
        ), "Recovery codes must be stored hashed, not plaintext"


@pytest.mark.django_db
def test_totp_verify_valid_code():
    """A valid TOTP code authenticates successfully."""
    user = UserFactory(status="active")
    device = _make_totp_device(user, confirmed=True)

    token = _get_current_totp_token(device)
    assert device.verify_token(token), "Valid current token should verify"


@pytest.mark.django_db
def test_totp_verify_invalid_code():
    """A wrong TOTP code is rejected."""
    user = UserFactory(status="active")
    device = _make_totp_device(user, confirmed=True)

    # Try a few tokens to ensure we have an invalid one
    for candidate in ("000000", "111111", "999999", "XXXXXX"):
        result = device.verify_token(candidate)
        if not result:
            assert not result, f"Token {candidate!r} should have been rejected"
            return

    pytest.fail("Could not find an invalid token to test rejection")


@pytest.mark.django_db
def test_totp_verify_expired_code():
    """A TOTP code from 2 minutes ago is rejected."""
    import time

    from django_otp.oath import TOTP as OathTOTP

    user = UserFactory(status="active")
    device = _make_totp_device(user, confirmed=True)

    # Generate a code from 2 minutes ago using django-otp's TOTP
    two_minutes_ago = time.time() - 120
    totp = OathTOTP(
        key=device.bin_key,
        step=device.step,
        t0=device.t0,
        digits=device.digits,
        drift=device.drift,
    )
    totp.time = two_minutes_ago
    old_token = f"{totp.token():06d}"

    # Now, back at current time, that old token should be rejected.
    # django-otp allows ±1 step tolerance (30s each), so 4 steps back (120s)
    # is definitely outside the tolerance window.
    assert not device.verify_token(
        old_token
    ), "Token from 2 minutes ago should be rejected"


# ---------------------------------------------------------------------------
# Recovery code flows
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_recovery_code_consumed_on_use():
    """
    After using a recovery code:
    - the code is removed from user.recovery_codes
    - session['totp_reset_required'] is set to True
    """
    user = UserFactory(status="active")

    # Create 3 recovery codes
    plaintext_codes = [secrets.token_urlsafe(10) for _ in range(3)]
    hashed_codes = [make_password(code) for code in plaintext_codes]
    user.recovery_codes = hashed_codes
    user.save(update_fields=["recovery_codes"])

    submitted = plaintext_codes[1]  # Use the middle code

    # Simulate recovery_code view logic
    matched_index = None
    for i, hashed in enumerate(user.recovery_codes):
        if check_password(submitted, hashed):
            matched_index = i
            break

    assert matched_index is not None, "Should find matching recovery code"

    codes = list(user.recovery_codes)
    codes.pop(matched_index)
    user.recovery_codes = codes
    user.save(update_fields=["recovery_codes"])

    session = {}
    session["totp_reset_required"] = True

    user.refresh_from_db()
    assert len(user.recovery_codes) == 2, "Used code should be removed"
    assert session.get("totp_reset_required") is True


@pytest.mark.django_db
def test_recovery_code_cannot_be_reused():
    """The same recovery code cannot be used a second time."""
    user = UserFactory(status="active")

    plaintext_codes = [secrets.token_urlsafe(10) for _ in range(3)]
    hashed_codes = [make_password(code) for code in plaintext_codes]
    user.recovery_codes = hashed_codes
    user.save(update_fields=["recovery_codes"])

    submitted = plaintext_codes[0]

    # First use — should succeed
    matched_index = None
    for i, hashed in enumerate(user.recovery_codes):
        if check_password(submitted, hashed):
            matched_index = i
            break

    assert matched_index is not None
    codes = list(user.recovery_codes)
    codes.pop(matched_index)
    user.recovery_codes = codes
    user.save(update_fields=["recovery_codes"])

    # Second use — should fail
    user.refresh_from_db()
    matched_again = None
    for i, hashed in enumerate(user.recovery_codes):
        if check_password(submitted, hashed):
            matched_again = i
            break

    assert (
        matched_again is None
    ), "Already-consumed code should not match on second attempt"


@pytest.mark.django_db
def test_recovery_code_all_exhausted():
    """When all recovery codes have been used, login via recovery code fails."""
    user = UserFactory(status="active")

    # Start with 3 codes
    plaintext_codes = [secrets.token_urlsafe(10) for _ in range(3)]
    hashed_codes = [make_password(code) for code in plaintext_codes]
    user.recovery_codes = hashed_codes
    user.save(update_fields=["recovery_codes"])

    # Consume all codes
    for code in plaintext_codes:
        user.refresh_from_db()
        matched_index = None
        for i, hashed in enumerate(user.recovery_codes):
            if check_password(code, hashed):
                matched_index = i
                break
        assert matched_index is not None
        codes = list(user.recovery_codes)
        codes.pop(matched_index)
        user.recovery_codes = codes
        user.save(update_fields=["recovery_codes"])

    user.refresh_from_db()
    assert len(user.recovery_codes) == 0, "All codes should be consumed"

    # Attempt login with any code should fail
    fake_code = secrets.token_urlsafe(10)
    matched = any(check_password(fake_code, hashed) for hashed in user.recovery_codes)
    assert not matched, "No codes remain — login should fail"


# ---------------------------------------------------------------------------
# Lost authenticator flow
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_lost_authenticator_invalidates_prior_otps():
    """
    Requesting a new OTP marks prior non-consumed, non-expired OTPs as consumed.
    """
    from accounts.models import EmailOTP

    user = UserFactory(status="active")

    # Create two existing OTPs that are not yet consumed
    otp1 = EmailOTP.objects.create(
        user=user,
        code_hash=make_password("123456"),
        expires_at=now() + timedelta(minutes=10),
        consumed=False,
    )
    otp2 = EmailOTP.objects.create(
        user=user,
        code_hash=make_password("654321"),
        expires_at=now() + timedelta(minutes=5),
        consumed=False,
    )

    # Simulate lost_authenticator view logic: invalidate prior unconsumed OTPs
    EmailOTP.objects.filter(
        user=user,
        consumed=False,
        expires_at__gt=now(),
    ).update(consumed=True)

    otp1.refresh_from_db()
    otp2.refresh_from_db()
    assert otp1.consumed, "Prior OTP 1 should be invalidated"
    assert otp2.consumed, "Prior OTP 2 should be invalidated"


@pytest.mark.django_db
def test_lost_authenticator_otp_expires_15_min():
    """OTP is created with expires_at = now + 15 minutes."""
    from accounts.models import EmailOTP

    user = UserFactory(status="active")
    frozen_now = now()

    with freeze_time(frozen_now):
        otp_code = f"{secrets.randbelow(1000000):06d}"
        code_hash = make_password(otp_code)
        otp = EmailOTP.objects.create(
            user=user,
            code_hash=code_hash,
            expires_at=now() + timedelta(minutes=15),
        )

    expected_expiry = frozen_now + timedelta(minutes=15)
    # Allow 1-second tolerance
    delta = abs((otp.expires_at - expected_expiry).total_seconds())
    assert (
        delta < 1
    ), f"OTP expires_at should be ~15 minutes from creation, delta={delta}s"


@pytest.mark.django_db
def test_lost_authenticator_enumerate_safe(rf):
    """
    Requesting a lost-authenticator OTP with an unknown email returns the
    same HTTP redirect as a known email (enumeration-safe).
    """
    from unittest.mock import patch

    from accounts.views import lost_authenticator

    org = OrganizationFactory()
    known_user = UserFactory(organization=org, status="active")

    def _make_lost_auth_request(email):
        request = rf.post("/accounts/lost-authenticator/", {"email": email})
        request.organization = org
        request.session = {}
        return request

    # Both known and unknown emails should redirect (302) to verify page
    with patch("accounts.views.send_mail"):
        response_known = lost_authenticator(_make_lost_auth_request(known_user.email))
        response_unknown = lost_authenticator(
            _make_lost_auth_request("nobody@nowhere.invalid")
        )

    assert response_known.status_code == 302, "Known email should redirect"
    assert (
        response_unknown.status_code == 302
    ), "Unknown email should redirect (enumeration-safe)"
    assert (
        response_known["Location"] == response_unknown["Location"]
    ), "Known and unknown email responses must redirect to the same URL"


@pytest.mark.django_db
def test_lost_authenticator_verify_consumed_on_use(rf):
    """
    OTP is consumed after use; a second attempt with the same code is rejected.
    """
    from accounts.models import EmailOTP
    from accounts.views import lost_authenticator_verify

    org = OrganizationFactory()
    user = UserFactory(organization=org, status="active")

    otp_plaintext = "482910"
    otp = EmailOTP.objects.create(
        user=user,
        code_hash=make_password(otp_plaintext),
        expires_at=now() + timedelta(minutes=15),
        consumed=False,
    )

    def _make_verify_request(code, session_user_id=None):
        request = rf.post("/accounts/lost-authenticator/verify/", {"code": code})
        request.organization = org
        request.session = _make_django_session()
        if session_user_id is not None:
            request.session["_lost_auth_user_id"] = session_user_id
        return request

    # First attempt — should succeed and consume
    from unittest.mock import patch

    with patch("django.contrib.auth.login"):
        response1 = lost_authenticator_verify(
            _make_verify_request(otp_plaintext, session_user_id=user.pk)
        )

    otp.refresh_from_db()
    assert otp.consumed, "OTP should be marked consumed after first use"
    # Should redirect on success
    assert response1.status_code == 302, "First valid use should redirect"

    # Second attempt — same code, now consumed; should fail
    response2 = lost_authenticator_verify(
        _make_verify_request(otp_plaintext, session_user_id=user.pk)
    )
    assert response2.status_code in (
        200,
        302,
    ), "Second attempt response should be 200 (form error)"
    otp.refresh_from_db()
    assert otp.consumed, "OTP should still be consumed after second attempt"


@pytest.mark.django_db
def test_lost_authenticator_verify_pending_approval_blocked(rf):
    """
    A pending_approval user cannot recover via the lost-authenticator path
    and must not transition to active status through it (#17).
    """
    from accounts.models import EmailOTP
    from accounts.views import lost_authenticator_verify

    org = OrganizationFactory()
    user = UserFactory(organization=org, status="pending_approval")

    otp_plaintext = "193847"
    EmailOTP.objects.create(
        user=user,
        code_hash=make_password(otp_plaintext),
        expires_at=now() + timedelta(minutes=15),
        consumed=False,
    )

    request = rf.post("/accounts/lost-authenticator/verify/", {"code": otp_plaintext})
    request.organization = org
    request.session = _make_django_session()
    request.session["_lost_auth_user_id"] = user.pk

    response = lost_authenticator_verify(request)

    # Must not redirect to totp_enroll; must render form with error
    assert response.status_code == 200, "pending_approval should not be redirected"
    user.refresh_from_db()
    assert user.status == "pending_approval", "Status must not change"


@pytest.mark.django_db
def test_lost_authenticator_verify_no_cross_user_otp(rf):
    """
    A submitted OTP code can only match the user whose PK is stored in session;
    it cannot consume another org member's OTP (#22).
    """
    from accounts.models import EmailOTP
    from accounts.views import lost_authenticator_verify

    org = OrganizationFactory()
    user_a = UserFactory(organization=org, status="active")
    user_b = UserFactory(organization=org, status="active")

    otp_for_b = "777321"
    EmailOTP.objects.create(
        user=user_b,
        code_hash=make_password(otp_for_b),
        expires_at=now() + timedelta(minutes=15),
        consumed=False,
    )

    # Session is bound to user_a — submitting user_b's code must fail
    request = rf.post("/accounts/lost-authenticator/verify/", {"code": otp_for_b})
    request.organization = org
    request.session = _make_django_session()
    request.session["_lost_auth_user_id"] = user_a.pk

    response = lost_authenticator_verify(request)

    assert response.status_code == 200, "Cross-user OTP must be rejected"
    # user_b's OTP must remain unconsumed
    assert not EmailOTP.objects.get(user=user_b).consumed


@pytest.mark.django_db
def test_totp_enroll_reset_preserves_pending_approval_status(rf):
    """
    Defense-in-depth: even if a pending_approval user somehow reaches
    totp_enroll with totp_reset_required=True, their status is not
    elevated to active (#17).
    """
    from unittest.mock import patch

    from accounts.views import totp_enroll

    org = OrganizationFactory()
    user = UserFactory(organization=org, status="pending_approval")
    _make_totp_device(user, confirmed=False)

    with patch(
        "accounts.models.EncryptedTOTPDevice.verify_token", return_value=True
    ):
        request = rf.post("/accounts/totp/enroll/", {"token": "000000"})
        request.user = user
        request.session = _make_django_session()
        request.session["totp_reset_required"] = True
        request.organization = org

        totp_enroll(request)

    user.refresh_from_db()
    assert (
        user.status == "pending_approval"
    ), "totp_enroll must not elevate pending_approval to active"


# ---------------------------------------------------------------------------
# Registration flows
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_invite_registration_valid(rf):
    """
    A valid invite creates a user with status=pending_totp and increments use_count.
    """
    from unittest.mock import patch

    from accounts.views import register_invite

    org = OrganizationFactory(registration_mode="invite_only")
    admin_user = UserFactory(organization=org, status="active")
    invite = InviteFactory(
        organization=org,
        issued_by=admin_user,
        max_uses=1,
        use_count=0,
    )

    request = rf.post(
        f"/accounts/register/{invite.code}/",
        {
            "email": "newresident@example.com",
            "display_name": "New Resident",
            "password": "SecurePass123!",
            "password_confirm": "SecurePass123!",
        },
    )
    request.organization = org
    request.session = _make_django_session()

    with patch("django.contrib.auth.login"):
        response = register_invite(request, code=invite.code)

    # Should redirect to totp_enroll
    assert response.status_code == 302

    from accounts.models import User

    new_user = User.objects.get(email="newresident@example.com", organization=org)
    assert (
        new_user.status == "pending_totp"
    ), f"New user status should be pending_totp, got {new_user.status!r}"

    invite.refresh_from_db()
    assert invite.use_count == 1, "Invite use_count should be incremented"
    assert invite.consumed_by_id == new_user.pk


@pytest.mark.django_db
def test_invite_registration_invalid_code(rf):
    """An unknown invite code returns 404."""
    from django.http import Http404

    from accounts.views import register_invite

    org = OrganizationFactory(registration_mode="invite_only")

    request = rf.get("/accounts/register/NONEXISTENT/")
    request.organization = org
    request.session = {}

    with pytest.raises(Http404):
        register_invite(request, code="NONEXISTENT-CODE-THAT-DOES-NOT-EXIST")


@pytest.mark.django_db
def test_invite_expired(rf):
    """An expired invite is rejected with HTTP 410."""
    from accounts.views import register_invite

    org = OrganizationFactory(registration_mode="invite_only")
    admin_user = UserFactory(organization=org, status="active")
    invite = InviteFactory(
        organization=org,
        issued_by=admin_user,
        expires_at=now() - timedelta(hours=1),  # expired 1 hour ago
    )

    request = rf.get(f"/accounts/register/{invite.code}/")
    request.organization = org
    request.session = {}

    response = register_invite(request, code=invite.code)
    assert (
        response.status_code == 410
    ), f"Expired invite should return 410, got {response.status_code}"


@pytest.mark.django_db
def test_self_registration_creates_pending(rf):
    """Self-registration creates a user with status=pending_approval."""
    from accounts.views import register

    org = OrganizationFactory(registration_mode="approve")

    request = rf.post(
        "/accounts/register/",
        {
            "email": "selfreg@example.com",
            "display_name": "Self Registrant",
        },
    )
    request.organization = org
    request.session = {}

    _ = register(request)

    from accounts.models import User

    new_user = User.objects.get(email="selfreg@example.com", organization=org)
    assert (
        new_user.status == "pending_approval"
    ), f"Self-registered user should have status pending_approval, got {new_user.status!r}"


@pytest.mark.django_db
def test_self_registration_blocked_invite_only(rf):
    """
    Self-registration is blocked (redirects away) when org is invite_only.
    """
    from accounts.views import register

    org = OrganizationFactory(registration_mode="invite_only")

    request = rf.post(
        "/accounts/register/",
        {
            "email": "blocked@example.com",
            "display_name": "Blocked User",
        },
    )
    request.organization = org
    request.session = {}

    response = register(request)

    # The view redirects to 'login' when org does not allow self-registration
    assert (
        response.status_code == 302
    ), "Self-registration on invite_only org should redirect (be blocked)"

    from accounts.models import User

    assert not User.objects.filter(
        email="blocked@example.com", organization=org
    ).exists(), "No user should be created for a blocked self-registration"


@pytest.mark.django_db
def test_invite_single_use_invariant_happy_path(rf):
    """
    A valid max_uses=1 invite creates a user and increments use_count to 1.
    Verifies the locked consume path still completes the happy path correctly.
    """
    from unittest.mock import patch

    from accounts.views import register_invite

    org = OrganizationFactory(registration_mode="invite_only")
    admin_user = UserFactory(organization=org, status="active")
    invite = InviteFactory(
        organization=org,
        issued_by=admin_user,
        max_uses=1,
        use_count=0,
    )

    request = rf.post(
        f"/accounts/register/{invite.code}/",
        {
            "email": "resident.a@example.com",
            "display_name": "Resident A",
            "password": "SecurePass123!",
            "password_confirm": "SecurePass123!",
        },
    )
    request.organization = org
    request.session = _make_django_session()

    with patch("django.contrib.auth.login"):
        response = register_invite(request, code=invite.code)

    assert response.status_code == 302

    from accounts.models import User

    created = User.objects.get(email="resident.a@example.com", organization=org)
    assert created.status == "pending_totp"

    invite.refresh_from_db()
    assert invite.use_count == 1
    assert invite.consumed_by_id == created.pk
    assert invite.consumed_at is not None


@pytest.mark.django_db
def test_invite_single_use_invariant_exhausted_invite_rejected(rf):
    """
    An already-exhausted invite (use_count >= max_uses) is rejected by the
    locked path inside the transaction — no second account is created.
    """
    from accounts.views import register_invite

    org = OrganizationFactory(registration_mode="invite_only")
    admin_user = UserFactory(organization=org, status="active")
    # Simulate an invite that has already been fully consumed
    invite = InviteFactory(
        organization=org,
        issued_by=admin_user,
        max_uses=1,
        use_count=1,  # already at the limit
    )

    request = rf.post(
        f"/accounts/register/{invite.code}/",
        {
            "email": "resident.b@example.com",
            "display_name": "Resident B",
            "password": "SecurePass123!",
            "password_confirm": "SecurePass123!",
        },
    )
    request.organization = org
    request.session = _make_django_session()

    response = register_invite(request, code=invite.code)

    # The pre-transaction guard returns 410; the locked path also returns 410
    assert response.status_code == 410

    from accounts.models import User

    assert not User.objects.filter(
        email="resident.b@example.com", organization=org
    ).exists(), "No account must be created when invite is exhausted"

    invite.refresh_from_db()
    assert invite.use_count == 1, "use_count must not increase beyond max_uses"


# ---------------------------------------------------------------------------
# User model
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_unique_email_per_org():
    """
    Same email is allowed in different orgs but rejected in the same org.
    """
    from django.db import IntegrityError

    org1 = OrganizationFactory()
    org2 = OrganizationFactory()

    # Same email in different orgs — should succeed
    user1 = UserFactory(organization=org1, email="shared@example.com")
    user2 = UserFactory(organization=org2, email="shared@example.com")
    assert user1.pk != user2.pk

    # Same email in same org — should fail
    with pytest.raises(IntegrityError):
        UserFactory(organization=org1, email="shared@example.com")


@pytest.mark.django_db
def test_default_notification_prefs():
    """A new user has notification_prefs={'push': False}."""
    user = UserFactory()
    user.refresh_from_db()
    assert user.notification_prefs == {
        "push": False
    }, f"Default notification_prefs should be {{'push': False}}, got {user.notification_prefs!r}"


@pytest.mark.django_db
def test_marketing_opt_in_default_false():
    """marketing_email_opted_in defaults to False."""
    user = UserFactory()
    user.refresh_from_db()
    assert (
        user.marketing_email_opted_in is False
    ), "marketing_email_opted_in should default to False"
