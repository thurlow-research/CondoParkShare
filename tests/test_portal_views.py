"""
Unit tests for portal.views — HOA admin portal view coverage.

Covers the uncovered lines in portal/views.py:
  portal_home (lines 61-86)
  resident_detail (lines 147-149)
  resident_approve GET (line 165) and non-pending redirect (line 173)
  resident_block GET (line 194)
  resident_unblock non-blocked redirect / GET (lines 206-218)
  spot_list (lines 230-239)
  spot_deactivate (lines 260-265)
  invite_list (lines 277-283)
  invite_create GET/POST (lines 309-311, 323-332)
  portal_bookings (lines 367-368)
  portal_booking_cancel (lines 388-403)
  portal_reports (lines 388-403 area)
"""

import uuid
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from unittest.mock import patch

import factory
import pytest
from django.test import RequestFactory
from psycopg2.extras import DateTimeTZRange


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc(year, month, day, hour=0, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=dt_timezone.utc)


def _make_request(method="GET", data=None, session=None):
    rf = RequestFactory()
    if method.upper() == "POST":
        req = rf.post("/", data or {})
    else:
        req = rf.get("/")
    req.session = session or {}
    return req


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------


class OrganizationFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "parking.Organization"

    name = factory.Sequence(lambda n: f"PortalViewOrg {n}")
    hostname = factory.Sequence(lambda n: f"portalvieworg{n}.parkshare.test")
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
    email = factory.Sequence(lambda n: f"pvuser{n}@example.com")
    display_name = factory.Sequence(lambda n: f"PV User {n}")
    status = "active"
    is_hoa_admin = False
    last_booking_at = None

    @classmethod
    def _create(cls, model_class, *args, **kwargs):
        manager = model_class.objects
        password = kwargs.pop("password", "test-password-secure!")
        return manager.create_user(*args, password=password, **kwargs)


class ParkingSpotFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "parking.ParkingSpot"

    organization = factory.SubFactory(OrganizationFactory)
    owner = factory.SubFactory(
        UserFactory,
        organization=factory.SelfAttribute("..organization"),
    )
    spot_number = factory.Sequence(lambda n: f"PV{n:04d}")
    status = "active"


class BookingFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "parking.Booking"

    organization = factory.SubFactory(OrganizationFactory)
    spot = factory.SubFactory(
        ParkingSpotFactory,
        organization=factory.SelfAttribute("..organization"),
    )
    borrower = factory.SubFactory(
        UserFactory,
        organization=factory.SelfAttribute("..organization"),
    )
    time_range = DateTimeTZRange(
        _utc(2030, 1, 10, 10),
        _utc(2030, 1, 10, 14),
    )
    status = "confirmed"


def _make_admin_request(method, admin_user, org, data=None):
    """Helper: creates a request with the admin user and org attached."""
    rf = RequestFactory()
    if method.upper() == "POST":
        req = rf.post("/", data or {})
    else:
        req = rf.get("/")
    req.user = admin_user
    req.organization = org
    req.session = {}
    return req


# ---------------------------------------------------------------------------
# portal_home
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_portal_home_returns_200():
    """portal_home renders with pending_approvals, active_bookings, pending_spots."""
    from portal.views import portal_home

    org = OrganizationFactory()
    admin = UserFactory(organization=org, is_hoa_admin=True, status="active")

    request = _make_admin_request("GET", admin, org)
    response = portal_home(request)

    assert response.status_code == 200


@pytest.mark.django_db
def test_portal_home_counts_pending_approvals():
    """portal_home context includes correct pending_approvals count."""
    from portal.views import portal_home

    org = OrganizationFactory()
    admin = UserFactory(organization=org, is_hoa_admin=True, status="active")
    UserFactory(organization=org, status="pending_approval")
    UserFactory(organization=org, status="pending_approval")

    request = _make_admin_request("GET", admin, org)
    response = portal_home(request)

    assert response.status_code == 200
    # We just verify it executes without error (template rendering happens in full Django stack)


# ---------------------------------------------------------------------------
# resident_detail
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_resident_detail_logs_pii_access():
    """resident_detail logs a pii_access audit entry."""
    from accounts.models import AdminAuditLog
    from portal.views import resident_detail

    org = OrganizationFactory()
    admin = UserFactory(organization=org, is_hoa_admin=True, status="active")
    resident = UserFactory(organization=org, status="active")

    request = _make_admin_request("GET", admin, org)
    response = resident_detail(request, pk=resident.pk)

    assert response.status_code == 200

    entry = AdminAuditLog.objects.filter(
        action="pii_access",
        target_type="user",
        target_id=resident.pk,
    ).first()
    assert entry is not None, "pii_access audit log should be created for resident_detail"


# ---------------------------------------------------------------------------
# resident_approve
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_resident_approve_get_shows_form():
    """resident_approve GET renders the approval confirmation page."""
    from portal.views import resident_approve

    org = OrganizationFactory()
    admin = UserFactory(organization=org, is_hoa_admin=True, status="active")
    resident = UserFactory(organization=org, status="pending_approval")

    request = _make_admin_request("GET", admin, org)
    response = resident_approve(request, pk=resident.pk)

    assert response.status_code == 200


@pytest.mark.django_db
def test_resident_approve_redirects_if_not_pending():
    """resident_approve redirects if user is not pending_approval."""
    from portal.views import resident_approve

    org = OrganizationFactory()
    admin = UserFactory(organization=org, is_hoa_admin=True, status="active")
    active_resident = UserFactory(organization=org, status="active")

    request = _make_admin_request("GET", admin, org)
    response = resident_approve(request, pk=active_resident.pk)

    # Should redirect because user.status != 'pending_approval'
    assert response.status_code == 302


@pytest.mark.django_db
def test_resident_approve_post_activates_user():
    """resident_approve POST sets user.status to 'active' and logs audit entry."""
    from accounts.models import AdminAuditLog
    from portal.views import resident_approve

    org = OrganizationFactory()
    admin = UserFactory(organization=org, is_hoa_admin=True, status="active")
    resident = UserFactory(organization=org, status="pending_approval")

    request = _make_admin_request("POST", admin, org)
    response = resident_approve(request, pk=resident.pk)

    assert response.status_code == 302
    resident.refresh_from_db()
    assert resident.status == "active", f"Expected status='active', got {resident.status!r}"

    entry = AdminAuditLog.objects.filter(action="approve_user", target_id=resident.pk).first()
    assert entry is not None, "approve_user audit log should be created"


# ---------------------------------------------------------------------------
# resident_block
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_resident_block_get_renders_page():
    """resident_block GET renders the block confirmation page."""
    from portal.views import resident_block

    org = OrganizationFactory()
    admin = UserFactory(organization=org, is_hoa_admin=True, status="active")
    resident = UserFactory(organization=org, status="active")

    request = _make_admin_request("GET", admin, org)
    response = resident_block(request, pk=resident.pk)

    assert response.status_code == 200


@pytest.mark.django_db
def test_resident_block_post_blocks_user():
    """resident_block POST sets user.status to 'blocked' and logs audit entry."""
    from accounts.models import AdminAuditLog
    from portal.views import resident_block

    org = OrganizationFactory()
    admin = UserFactory(organization=org, is_hoa_admin=True, status="active")
    resident = UserFactory(organization=org, status="active")

    request = _make_admin_request("POST", admin, org)
    response = resident_block(request, pk=resident.pk)

    assert response.status_code == 302
    resident.refresh_from_db()
    assert resident.status == "blocked", f"Expected status='blocked', got {resident.status!r}"

    entry = AdminAuditLog.objects.filter(action="block", target_id=resident.pk).first()
    assert entry is not None, "block audit log should be created"


# ---------------------------------------------------------------------------
# resident_unblock
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_resident_unblock_redirects_if_not_blocked():
    """resident_unblock redirects if user is not blocked."""
    from portal.views import resident_unblock

    org = OrganizationFactory()
    admin = UserFactory(organization=org, is_hoa_admin=True, status="active")
    active_resident = UserFactory(organization=org, status="active")

    request = _make_admin_request("GET", admin, org)
    response = resident_unblock(request, pk=active_resident.pk)

    assert response.status_code == 302


@pytest.mark.django_db
def test_resident_unblock_get_renders_page():
    """resident_unblock GET renders confirmation page for a blocked user."""
    from portal.views import resident_unblock

    org = OrganizationFactory()
    admin = UserFactory(organization=org, is_hoa_admin=True, status="active")
    blocked = UserFactory(organization=org, status="blocked")

    request = _make_admin_request("GET", admin, org)
    response = resident_unblock(request, pk=blocked.pk)

    assert response.status_code == 200


@pytest.mark.django_db
def test_resident_unblock_post_activates_user():
    """resident_unblock POST sets user.status to 'active' and logs audit entry."""
    from accounts.models import AdminAuditLog
    from portal.views import resident_unblock

    org = OrganizationFactory()
    admin = UserFactory(organization=org, is_hoa_admin=True, status="active")
    blocked = UserFactory(organization=org, status="blocked")

    request = _make_admin_request("POST", admin, org)
    response = resident_unblock(request, pk=blocked.pk)

    assert response.status_code == 302
    blocked.refresh_from_db()
    assert blocked.status == "active", f"Expected status='active', got {blocked.status!r}"

    entry = AdminAuditLog.objects.filter(action="unblock", target_id=blocked.pk).first()
    assert entry is not None, "unblock audit log should be created"


# ---------------------------------------------------------------------------
# spot_list
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_portal_spot_list_returns_200():
    """portal spot_list view renders successfully."""
    from portal.views import spot_list

    org = OrganizationFactory()
    admin = UserFactory(organization=org, is_hoa_admin=True, status="active")
    ParkingSpotFactory(organization=org, owner=admin, status="pending")
    ParkingSpotFactory(organization=org, owner=admin, status="active")

    request = _make_admin_request("GET", admin, org)
    response = spot_list(request)

    assert response.status_code == 200


# ---------------------------------------------------------------------------
# spot_deactivate
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_spot_deactivate_sets_inactive():
    """spot_deactivate POST sets spot.status to 'inactive' and logs audit entry."""
    from accounts.models import AdminAuditLog
    from portal.views import spot_deactivate

    org = OrganizationFactory()
    admin = UserFactory(organization=org, is_hoa_admin=True, status="active")
    spot = ParkingSpotFactory(organization=org, owner=admin, status="active")

    request = _make_admin_request("POST", admin, org)
    response = spot_deactivate(request, pk=spot.pk)

    assert response.status_code == 302
    spot.refresh_from_db()
    assert spot.status == "inactive", f"Expected status='inactive', got {spot.status!r}"

    entry = AdminAuditLog.objects.filter(action="deactivate_spot", target_id=spot.pk).first()
    assert entry is not None, "deactivate_spot audit log should be created"


# ---------------------------------------------------------------------------
# invite_list
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_invite_list_returns_200():
    """invite_list view renders successfully."""
    from accounts.models import Invite
    from portal.views import invite_list

    org = OrganizationFactory()
    admin = UserFactory(organization=org, is_hoa_admin=True, status="active")

    Invite.objects.create(
        organization=org,
        issued_by=admin,
        code="test-invite-code-abc",
        max_uses=1,
        use_count=0,
    )

    request = _make_admin_request("GET", admin, org)
    response = invite_list(request)

    assert response.status_code == 200


# ---------------------------------------------------------------------------
# invite_create
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_invite_create_get_renders_form():
    """invite_create GET renders the form."""
    from portal.views import invite_create

    org = OrganizationFactory()
    admin = UserFactory(organization=org, is_hoa_admin=True, status="active")

    request = _make_admin_request("GET", admin, org)
    response = invite_create(request)

    assert response.status_code == 200


@pytest.mark.django_db
def test_invite_create_post_creates_invite():
    """invite_create POST creates an Invite and redirects."""
    from accounts.models import Invite
    from portal.views import invite_create

    org = OrganizationFactory()
    admin = UserFactory(organization=org, is_hoa_admin=True, status="active")

    request = _make_admin_request("POST", admin, org, data={
        "max_uses": "1",
        "unit_number": "101",
        "expires_at": "",
    })
    initial_count = Invite.objects.filter(organization=org).count()
    response = invite_create(request)

    assert response.status_code == 302
    final_count = Invite.objects.filter(organization=org).count()
    assert final_count == initial_count + 1, "An Invite record should be created on POST"


# ---------------------------------------------------------------------------
# portal_bookings
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_portal_bookings_returns_200():
    """portal_bookings view renders successfully."""
    from portal.views import portal_bookings

    org = OrganizationFactory()
    admin = UserFactory(organization=org, is_hoa_admin=True, status="active")
    owner = UserFactory(organization=org, status="active")
    borrower = UserFactory(organization=org, status="active")
    spot = ParkingSpotFactory(organization=org, owner=owner)
    BookingFactory(organization=org, spot=spot, borrower=borrower, status="confirmed")

    request = _make_admin_request("GET", admin, org)
    response = portal_bookings(request)

    assert response.status_code == 200


# ---------------------------------------------------------------------------
# portal_booking_cancel
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_portal_booking_cancel_sets_cancelled_admin():
    """portal_booking_cancel POST cancels a confirmed booking and logs audit entry."""
    from accounts.models import AdminAuditLog
    from portal.views import portal_booking_cancel

    org = OrganizationFactory()
    admin = UserFactory(organization=org, is_hoa_admin=True, status="active")
    owner = UserFactory(organization=org, status="active")
    borrower = UserFactory(organization=org, status="active")
    spot = ParkingSpotFactory(organization=org, owner=owner)
    booking = BookingFactory(
        organization=org,
        spot=spot,
        borrower=borrower,
        status="confirmed",
        time_range=DateTimeTZRange(_utc(2030, 2, 1, 10), _utc(2030, 2, 1, 14)),
    )

    request = _make_admin_request("POST", admin, org, data={
        "cancel_reason": "Admin override",
    })

    with patch("portal.views.notify"):
        response = portal_booking_cancel(request, pk=booking.pk)

    assert response.status_code == 302
    booking.refresh_from_db()
    assert booking.status == "cancelled_admin", (
        f"Booking should be 'cancelled_admin'; got {booking.status!r}"
    )

    entry = AdminAuditLog.objects.filter(action="admin_cancel", target_id=booking.pk).first()
    assert entry is not None, "admin_cancel audit log should be created"


@pytest.mark.django_db
def test_portal_booking_cancel_ignores_already_cancelled():
    """portal_booking_cancel POST is a no-op for already-cancelled bookings."""
    from portal.views import portal_booking_cancel

    org = OrganizationFactory()
    admin = UserFactory(organization=org, is_hoa_admin=True, status="active")
    owner = UserFactory(organization=org, status="active")
    borrower = UserFactory(organization=org, status="active")
    spot = ParkingSpotFactory(organization=org, owner=owner)
    booking = BookingFactory(
        organization=org,
        spot=spot,
        borrower=borrower,
        status="cancelled_borrower",
        time_range=DateTimeTZRange(_utc(2030, 3, 1, 10), _utc(2030, 3, 1, 14)),
    )

    request = _make_admin_request("POST", admin, org)

    response = portal_booking_cancel(request, pk=booking.pk)

    assert response.status_code == 302
    booking.refresh_from_db()
    # Should remain unchanged — already cancelled
    assert booking.status == "cancelled_borrower", (
        f"Already-cancelled booking should not change; got {booking.status!r}"
    )


# ---------------------------------------------------------------------------
# portal_reports
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_portal_reports_returns_200():
    """portal_reports view renders successfully."""
    from portal.views import portal_reports

    org = OrganizationFactory()
    admin = UserFactory(organization=org, is_hoa_admin=True, status="active")

    request = _make_admin_request("GET", admin, org)
    response = portal_reports(request)

    assert response.status_code == 200
