"""
Regression tests for account-status enforcement across auth / session / erasure.

Covers issue #156 — blocked & erased users must not retain access, and a blocked
HOA admin must not be able to self-unblock:

  1. login_view rejects blocked / pending_approval users (generic error).
  2. totp_verify drops the pre-auth session for blocked / pending_approval users.
  3. hoa_admin_required denies an admin whose status is not 'active'.
  4. resident_unblock denies a self-targeted unblock.
  5. message_reply denies a non-active user holding a valid reply token.
  6. erase_user_pii deactivates the account (is_active=False).
"""

import uuid
from unittest.mock import patch

import factory
import pytest
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.test import RequestFactory

# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------


class OrganizationFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "parking.Organization"

    name = factory.Sequence(lambda n: f"StatusOrg {n}")
    hostname = factory.Sequence(lambda n: f"statusorg{n}.parkshare.test")
    support_email = factory.LazyAttribute(lambda o: f"support@{o.hostname}")
    registration_mode = "invite_only"
    timezone = "America/Los_Angeles"
    booking_horizon_baseline_days = 3
    booking_horizon_max_days = 30
    listing_to_horizon_ratio = 10
    tier_metric_window_days = 180
    launch_grace_days = 14
    launch_grace_horizon_days = 14


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "accounts.User"

    organization = factory.SubFactory(OrganizationFactory)
    email = factory.Sequence(lambda n: f"statususer{n}@example.com")
    display_name = factory.Sequence(lambda n: f"Status User {n}")
    status = "active"
    is_hoa_admin = False
    last_booking_at = None

    @classmethod
    def _create(cls, model_class, *args, **kwargs):
        manager = model_class.objects
        password = kwargs.pop("password", "test-password-secure!")
        return manager.create_user(*args, password=password, **kwargs)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request(method="GET", data=None, session=None, user=None, org=None):
    """Build a test request with session / user / organisation attached."""
    rf = RequestFactory()
    if method.upper() == "POST":
        req = rf.post("/", data or {})
    else:
        req = rf.get("/")
    req.session = session if session is not None else {}
    if user is not None:
        req.user = user
    if org is not None:
        req.organization = org
    return req


# ---------------------------------------------------------------------------
# 1. login_view — status gate at the first factor
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("status", ["blocked", "pending_approval"])
def test_login_view_rejects_non_loginable_status(status):
    """A blocked / not-yet-approved user with a valid password is rejected with a
    generic error and no pre-auth session is established."""
    from accounts.views import login_view

    org = OrganizationFactory()
    user = UserFactory(organization=org, status=status, password="correct-password-123!")

    # authenticate() succeeds (the password is correct) — the status gate must reject.
    with patch("accounts.views.authenticate", return_value=user):
        request = _make_request(
            "POST",
            data={"email": user.email, "password": "correct-password-123!"},
            session={},
            org=org,
        )
        response = login_view(request)

    assert response.status_code == 200, "must re-render the form, not redirect to totp_verify"
    assert "_pre_auth_user_id" not in request.session


@pytest.mark.django_db
def test_login_view_allows_active_user():
    """An active user with a valid password proceeds to the second factor."""
    from accounts.views import login_view

    org = OrganizationFactory()
    user = UserFactory(organization=org, status="active", password="correct-password-123!")

    with patch("accounts.views.authenticate", return_value=user):
        request = _make_request(
            "POST",
            data={"email": user.email, "password": "correct-password-123!"},
            session={},
            org=org,
        )
        response = login_view(request)

    assert response.status_code == 302
    assert request.session.get("_pre_auth_user_id") == user.pk


# ---------------------------------------------------------------------------
# 2. totp_verify — status gate at the second factor
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("status", ["blocked", "pending_approval"])
def test_totp_verify_rejects_non_loginable_status(status):
    """If a blocked / unapproved user reaches the second factor (e.g. status changed
    mid-flow), totp_verify drops the pre-auth session and redirects to login."""
    from accounts.views import totp_verify

    org = OrganizationFactory()
    user = UserFactory(organization=org, status=status)

    request = _make_request(
        "POST",
        data={"token": "123456"},
        session={"_pre_auth_user_id": user.pk},
        org=org,
    )
    response = totp_verify(request)

    assert response.status_code == 302
    assert "login" in response.url
    assert "_pre_auth_user_id" not in request.session


# ---------------------------------------------------------------------------
# 3. hoa_admin_required — status gate on the portal
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_hoa_admin_required_denies_non_active_admin():
    """hoa_admin_required must reject an HOA admin whose status is not 'active'."""
    from accounts.decorators import hoa_admin_required

    @hoa_admin_required
    def _view(request):
        return HttpResponse("ok")

    org = OrganizationFactory()
    blocked_admin = UserFactory(organization=org, is_hoa_admin=True, status="blocked")

    request = _make_request("GET", user=blocked_admin, org=org)
    with pytest.raises(PermissionDenied):
        _view(request)


@pytest.mark.django_db
def test_hoa_admin_required_allows_active_admin():
    """An active HOA admin of the org is allowed through the decorator."""
    from accounts.decorators import hoa_admin_required

    @hoa_admin_required
    def _view(request):
        return HttpResponse("ok")

    org = OrganizationFactory()
    admin = UserFactory(organization=org, is_hoa_admin=True, status="active")

    request = _make_request("GET", user=admin, org=org)
    response = _view(request)

    assert response.status_code == 200


# ---------------------------------------------------------------------------
# 4. resident_unblock — self-target guard
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_resident_unblock_self_target_denied():
    """An admin must not be able to unblock their own pk (defence against a blocked
    HOA admin restoring their own privileges)."""
    from portal.views import resident_unblock

    org = OrganizationFactory()
    admin = UserFactory(organization=org, is_hoa_admin=True, status="active")

    request = _make_request("POST", user=admin, org=org)
    with pytest.raises(PermissionDenied):
        resident_unblock(request, pk=admin.pk)


# ---------------------------------------------------------------------------
# 5. message_reply — relay status gate
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_message_reply_denies_non_active_user():
    """A blocked / erased user holding a still-valid reply token must not be able to
    keep using the relay. The status gate fires before any token lookup."""
    from notifications.views import message_reply

    org = OrganizationFactory()
    blocked = UserFactory(organization=org, status="blocked")

    request = _make_request("GET", user=blocked, org=org)
    with pytest.raises(PermissionDenied):
        message_reply(request, token=uuid.uuid4())


# ---------------------------------------------------------------------------
# 6. erase_user_pii — deactivation
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_erasure_deactivates_user():
    """erase_user_pii must set is_active=False so an erased user cannot
    re-authenticate (ModelBackend.user_can_authenticate checks is_active)."""
    from accounts.erasure import erase_user_pii

    org = OrganizationFactory()
    user = UserFactory(organization=org)
    admin = UserFactory(organization=org)
    assert user.is_active is True

    erase_user_pii(user, erased_by=admin)
    user.refresh_from_db()

    assert user.is_active is False, "erased user must be deactivated (is_active=False)"
    assert user.status == "blocked"
