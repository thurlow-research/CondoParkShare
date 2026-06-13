"""
System tests — Onboarding flows (SPEC-1 §6, §11).

Covers:
  Mode A (invite_only):
    1. Admin generates invite link → link is single-use.
    2. New resident registers via link → user created with status='pending_totp'.
    3. Second use of same invite link → rejected (use count exhausted).
    4. Expired invite → rejected.

  Mode B (approve):
    1. Resident self-registers → account status = 'pending_approval'.
    2. HOA admin approves → account status = 'active'.
    3. HOA admin blocks → account status = 'blocked'; login fails.
"""

from datetime import timedelta

import pytest
from django.test import Client, override_settings
from django.utils.timezone import now

from tests.system.conftest import (
    client_get,
    client_post,
    force_login_active,
    make_org,
    make_user,
    utc,
)

HOSTNAME_A = "onboarding-a.parkshare.test"
HOSTNAME_B = "onboarding-b.parkshare.test"


@pytest.fixture
def org_a(db):
    return make_org("OnboardOrgA", HOSTNAME_A, registration_mode="invite_only")


@pytest.fixture
def org_b(db):
    return make_org("OnboardOrgB", HOSTNAME_B, registration_mode="approve")


@pytest.fixture
def hoa_admin_a(org_a):
    return make_user(org_a, "hoa@onboarding-a.test", is_hoa_admin=True)


@pytest.fixture
def hoa_admin_b(org_b):
    return make_user(org_b, "hoa@onboarding-b.test", is_hoa_admin=True)


# ---------------------------------------------------------------------------
# Mode A — Invite-only registration
# ---------------------------------------------------------------------------


def _make_invite(org, issued_by, max_uses=1, expires_at=None):
    import secrets as _secrets
    from accounts.models import Invite

    if expires_at is None:
        expires_at = now() + timedelta(days=7)

    code = _secrets.token_urlsafe(20)
    return Invite.objects.create(
        organization=org,
        issued_by=issued_by,
        code=code,
        max_uses=max_uses,
        expires_at=expires_at,
    )


@pytest.mark.django_db
@override_settings(ALLOWED_HOSTS=[HOSTNAME_A])
def test_invite_link_allows_new_resident_to_register(org_a, hoa_admin_a):
    """
    Valid invite link renders registration form and creates a user with
    status='pending_totp'. (SPEC-1 §6, §11 Onboarding A)
    """
    invite = _make_invite(org_a, hoa_admin_a)

    client = Client()
    # GET the invite registration page
    response = client_get(client, HOSTNAME_A, f"/accounts/register/{invite.code}/")
    assert response.status_code == 200, (
        f"Expected 200 for valid invite page, got {response.status_code}"
    )

    # POST to register
    response = client_post(client, HOSTNAME_A, f"/accounts/register/{invite.code}/", {
        "email": "newresident@test.test",
        "display_name": "New Resident",
        "password": "Secure-Pass-123!",
        "password_confirm": "Secure-Pass-123!",
    })

    # Should redirect to TOTP enrollment
    assert response.status_code == 302, (
        f"Expected redirect to totp_enroll after invite registration, got {response.status_code}"
    )

    from accounts.models import User
    user = User.objects.filter(email="newresident@test.test", organization=org_a).first()
    assert user is not None, "User should be created after invite registration"
    assert user.status == "pending_totp", (
        f"Expected status 'pending_totp', got '{user.status}'"
    )


@pytest.mark.django_db
@override_settings(ALLOWED_HOSTS=[HOSTNAME_A])
def test_invite_is_single_use_second_use_rejected(org_a, hoa_admin_a):
    """
    After the first use, the same invite code is rejected. (SPEC-1 §6)
    """
    invite = _make_invite(org_a, hoa_admin_a, max_uses=1)

    # First use
    client = Client()
    client_post(client, HOSTNAME_A, f"/accounts/register/{invite.code}/", {
        "email": "first@test.test",
        "display_name": "First User",
        "password": "Secure-Pass-123!",
        "password_confirm": "Secure-Pass-123!",
    })

    # Reload invite to verify it's been consumed
    invite.refresh_from_db()
    assert invite.use_count == 1, f"Expected use_count=1, got {invite.use_count}"

    # Second use should be rejected
    client2 = Client()
    response = client_get(client2, HOSTNAME_A, f"/accounts/register/{invite.code}/")
    assert response.status_code == 410, (
        f"Expected 410 Gone for exhausted invite, got {response.status_code}"
    )


@pytest.mark.django_db
@override_settings(ALLOWED_HOSTS=[HOSTNAME_A])
def test_expired_invite_is_rejected(org_a, hoa_admin_a):
    """
    Expired invite returns 410. (SPEC-1 §6)
    """
    expired_invite = _make_invite(
        org_a, hoa_admin_a,
        expires_at=now() - timedelta(hours=1),  # already expired
    )

    client = Client()
    response = client_get(client, HOSTNAME_A, f"/accounts/register/{expired_invite.code}/")
    assert response.status_code == 410, (
        f"Expected 410 Gone for expired invite, got {response.status_code}"
    )


# ---------------------------------------------------------------------------
# Mode B — Self-register then approve
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@override_settings(ALLOWED_HOSTS=[HOSTNAME_B])
def test_self_register_creates_pending_approval_account(org_b):
    """
    Resident self-registers in 'approve' mode → status='pending_approval'. (SPEC-1 §6, §11)
    """
    client = Client()
    response = client_post(client, HOSTNAME_B, "/accounts/register/", {
        "email": "pending@test.test",
        "display_name": "Pending User",
        "password": "Secure-Pass-123!",
        "password_confirm": "Secure-Pass-123!",
    })

    # Should render confirmation page (not redirect to TOTP — must be approved first)
    assert response.status_code in (200, 302), (
        f"Expected 200 or 302 after self-registration, got {response.status_code}"
    )

    from accounts.models import User
    user = User.objects.filter(email="pending@test.test", organization=org_b).first()
    assert user is not None, "User should be created after self-registration"
    assert user.status == "pending_approval", (
        f"Expected status 'pending_approval', got '{user.status}'"
    )


@pytest.mark.django_db
@override_settings(ALLOWED_HOSTS=[HOSTNAME_B])
def test_hoa_admin_approve_activates_account(org_b, hoa_admin_b):
    """
    HOA admin approves pending account → status becomes 'active'. (SPEC-1 §8, §11)
    """
    pending_user = make_user(org_b, "toapprov@test.test", status="pending_approval")

    client = Client()
    force_login_active(client, hoa_admin_b)

    response = client_post(client, HOSTNAME_B, f"/portal/residents/{pending_user.pk}/approve/")
    assert response.status_code == 302, (
        f"Expected redirect after approve, got {response.status_code}"
    )

    pending_user.refresh_from_db()
    assert pending_user.status == "active", (
        f"Expected status 'active' after admin approval, got '{pending_user.status}'"
    )


@pytest.mark.django_db
@override_settings(ALLOWED_HOSTS=[HOSTNAME_B])
def test_hoa_admin_block_prevents_active_access(org_b, hoa_admin_b):
    """
    HOA admin blocks a resident → status='blocked'; blocked user cannot access
    protected resident views (active_required decorator redirects them). (SPEC-1 §8, §11)

    Note: The TOTP flow itself does not re-check blocked status (the check happens
    in active_required on every protected view). A blocked user may reach the TOTP
    page but cannot proceed to any active-resident view.
    """
    active_user = make_user(
        org_b, "toblocked@test.test",
        password="Active-Pass-456!",
        status="active",
    )

    # Admin blocks the user
    client = Client()
    force_login_active(client, hoa_admin_b)
    response = client_post(client, HOSTNAME_B, f"/portal/residents/{active_user.pk}/block/")
    assert response.status_code == 302, (
        f"Expected redirect after block, got {response.status_code}"
    )

    active_user.refresh_from_db()
    assert active_user.status == "blocked", (
        f"Expected status 'blocked', got '{active_user.status}'"
    )

    # Blocked user force-logged in still cannot access protected views
    # (active_required checks status on every view invocation)
    client2 = Client()
    force_login_active(client2, active_user)
    response2 = client_get(client2, HOSTNAME_B, "/book/")

    # Should redirect away (not 200) because status != 'active'
    assert response2.status_code in (302, 301), (
        f"Blocked user should be redirected from protected view, got {response2.status_code}"
    )
    location = response2.get("Location", "")
    assert "book" not in location, (
        f"Blocked user should not stay on /book/, got redirect to: {location}"
    )
