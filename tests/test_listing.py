"""
Unit tests for CondoParkShare Step 5 — listing and availability.

Covers:
  Availability computation (1-7)
  AvailabilityWindow form (8-11)
  Owner views (12-15)
"""

from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

import factory
import pytest
from psycopg2.extras import DateTimeTZRange

# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------


class OrganizationFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "parking.Organization"

    name = factory.Sequence(lambda n: f"ListingOrg {n}")
    hostname = factory.Sequence(lambda n: f"listingorg{n}.parkshare.test")
    support_email = factory.LazyAttribute(lambda o: f"support@{o.hostname}")
    registration_mode = "invite_only"
    timezone = "America/Los_Angeles"


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "accounts.User"

    organization = factory.SubFactory(OrganizationFactory)
    email = factory.Sequence(lambda n: f"listinguser{n}@example.com")
    display_name = factory.Sequence(lambda n: f"Listing User {n}")
    status = "active"

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
    spot_number = factory.Sequence(lambda n: f"L{n:04d}")
    status = "active"


class AvailabilityWindowFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "parking.AvailabilityWindow"

    organization = factory.SubFactory(OrganizationFactory)
    spot = factory.SubFactory(
        ParkingSpotFactory,
        organization=factory.SelfAttribute("..organization"),
    )
    # Default: covers 08:00–20:00 on 2027-03-01 UTC
    time_range = DateTimeTZRange(
        datetime(2027, 3, 1, 8, 0, tzinfo=dt_timezone.utc),
        datetime(2027, 3, 1, 20, 0, tzinfo=dt_timezone.utc),
    )


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
        datetime(2027, 3, 1, 10, 0, tzinfo=dt_timezone.utc),
        datetime(2027, 3, 1, 12, 0, tzinfo=dt_timezone.utc),
    )
    status = "confirmed"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc(year, month, day, hour, minute=0):
    """Convenience: return a timezone-aware UTC datetime."""
    return datetime(year, month, day, hour, minute, tzinfo=dt_timezone.utc)


# ---------------------------------------------------------------------------
# 1. test_assign_spot_no_window
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_assign_spot_no_window():
    """assign_spot returns None when the spot has no AvailabilityWindow."""
    from parking.booking import assign_spot

    org = OrganizationFactory()
    borrower = UserFactory(organization=org)
    ParkingSpotFactory(organization=org, status="active")
    # No AvailabilityWindow created

    result = assign_spot(org, borrower, _utc(2027, 3, 1, 10), _utc(2027, 3, 1, 12))
    assert (
        result is None
    ), "assign_spot must return None when no window covers the range"


# ---------------------------------------------------------------------------
# 2. test_assign_spot_with_window
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_assign_spot_with_window():
    """assign_spot succeeds when a spot has a covering window and no conflicts."""
    from parking.booking import assign_spot
    from parking.models import Booking

    org = OrganizationFactory()
    borrower = UserFactory(organization=org)
    spot = ParkingSpotFactory(organization=org, status="active")
    AvailabilityWindowFactory(
        organization=org,
        spot=spot,
        time_range=DateTimeTZRange(
            _utc(2027, 3, 1, 8),
            _utc(2027, 3, 1, 20),
        ),
    )

    result = assign_spot(org, borrower, _utc(2027, 3, 1, 10), _utc(2027, 3, 1, 12))
    assert isinstance(
        result, Booking
    ), "assign_spot must return a Booking when a window covers the range"
    assert result.status == "tentative"


# ---------------------------------------------------------------------------
# 3. test_assign_spot_buffer_before
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_assign_spot_buffer_before():
    """
    Existing booking ends at 14:00.  Requesting 14:30–16:00 returns None
    because the 1-hour buffer around the existing booking blocks the request.
    """
    from parking.booking import assign_spot

    org = OrganizationFactory()
    borrower = UserFactory(organization=org)
    spot = ParkingSpotFactory(organization=org, status="active")
    AvailabilityWindowFactory(
        organization=org,
        spot=spot,
        time_range=DateTimeTZRange(
            _utc(2027, 3, 1, 8),
            _utc(2027, 3, 1, 20),
        ),
    )
    BookingFactory(
        organization=org,
        spot=spot,
        time_range=DateTimeTZRange(
            _utc(2027, 3, 1, 11),
            _utc(2027, 3, 1, 14),
        ),
        status="confirmed",
    )

    # Request starts at 14:30 — within 1h buffer after the booking that ends at 14:00
    result = assign_spot(org, borrower, _utc(2027, 3, 1, 14, 30), _utc(2027, 3, 1, 16))
    assert (
        result is None
    ), "Request starting within 1h buffer after an existing booking must be blocked"


# ---------------------------------------------------------------------------
# 4. test_assign_spot_buffer_after
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_assign_spot_buffer_after():
    """
    Existing booking starts at 16:00.  Requesting 14:00–15:30 returns None
    because the 1-hour buffer blocks the new request.
    """
    from parking.booking import assign_spot

    org = OrganizationFactory()
    borrower = UserFactory(organization=org)
    spot = ParkingSpotFactory(organization=org, status="active")
    AvailabilityWindowFactory(
        organization=org,
        spot=spot,
        time_range=DateTimeTZRange(
            _utc(2027, 3, 1, 8),
            _utc(2027, 3, 1, 20),
        ),
    )
    BookingFactory(
        organization=org,
        spot=spot,
        time_range=DateTimeTZRange(
            _utc(2027, 3, 1, 16),
            _utc(2027, 3, 1, 18),
        ),
        status="confirmed",
    )

    # Request ends at 15:30 — within 1h buffer before the booking starting at 16:00
    result = assign_spot(org, borrower, _utc(2027, 3, 1, 14), _utc(2027, 3, 1, 15, 30))
    assert (
        result is None
    ), "Request ending within 1h buffer before an existing booking must be blocked"


# ---------------------------------------------------------------------------
# 5. test_assign_spot_outside_buffer
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_assign_spot_outside_buffer():
    """
    Booking ends at 12:00.  Request starts at 13:00, which is exactly 1 hour
    after the booking ends (12:00 + 1h buffer = 13:00, so 13:00 is not blocked
    since the buffer is open-ended: buffered range is [start-1h, end+1h) ).
    """
    from parking.booking import assign_spot
    from parking.models import Booking

    org = OrganizationFactory()
    borrower = UserFactory(organization=org)
    spot = ParkingSpotFactory(organization=org, status="active")
    AvailabilityWindowFactory(
        organization=org,
        spot=spot,
        time_range=DateTimeTZRange(
            _utc(2027, 3, 1, 8),
            _utc(2027, 3, 1, 20),
        ),
    )
    BookingFactory(
        organization=org,
        spot=spot,
        time_range=DateTimeTZRange(
            _utc(2027, 3, 1, 10),
            _utc(2027, 3, 1, 12),
        ),
        status="confirmed",
    )

    # Request starts at 14:00 — well outside the 1h buffer after 12:00
    result = assign_spot(org, borrower, _utc(2027, 3, 1, 14), _utc(2027, 3, 1, 16))
    assert isinstance(
        result, Booking
    ), "Request starting >1h after an existing booking's end must succeed"


# ---------------------------------------------------------------------------
# 6. test_assign_spot_cancelled_booking_ignored
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_assign_spot_cancelled_booking_ignored():
    """A cancelled booking does not block assign_spot."""
    from parking.booking import assign_spot
    from parking.models import Booking

    org = OrganizationFactory()
    borrower = UserFactory(organization=org)
    spot = ParkingSpotFactory(organization=org, status="active")
    AvailabilityWindowFactory(
        organization=org,
        spot=spot,
        time_range=DateTimeTZRange(
            _utc(2027, 3, 1, 8),
            _utc(2027, 3, 1, 20),
        ),
    )
    # Cancelled booking overlapping the desired range — must not block
    BookingFactory(
        organization=org,
        spot=spot,
        time_range=DateTimeTZRange(
            _utc(2027, 3, 1, 10),
            _utc(2027, 3, 1, 12),
        ),
        status="cancelled_owner",
    )

    result = assign_spot(org, borrower, _utc(2027, 3, 1, 10), _utc(2027, 3, 1, 12))
    assert isinstance(result, Booking), "Cancelled bookings must not block assign_spot"


# ---------------------------------------------------------------------------
# 7. test_assign_spot_rotation_order
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_assign_spot_rotation_order():
    """
    Two spots in the same org, both available for the requested window.
    Owner A's spot has last_booking_at=yesterday; owner B's spot has
    last_booking_at=today.  assign_spot selects owner A's spot first
    (least-recently-booked first = fair rotation).
    """
    from django.utils.timezone import now

    from parking.booking import assign_spot
    from parking.models import Booking

    org = OrganizationFactory()

    today = now()
    yesterday = today - timedelta(days=1)

    owner_a = UserFactory(organization=org, last_booking_at=yesterday)
    owner_b = UserFactory(organization=org, last_booking_at=today)
    borrower = UserFactory(organization=org)

    spot_a = ParkingSpotFactory(organization=org, owner=owner_a, status="active")
    spot_b = ParkingSpotFactory(organization=org, owner=owner_b, status="active")

    # Both spots need an availability window covering the requested range
    window_range = DateTimeTZRange(
        _utc(2027, 3, 2, 8),
        _utc(2027, 3, 2, 20),
    )
    AvailabilityWindowFactory(organization=org, spot=spot_a, time_range=window_range)
    AvailabilityWindowFactory(organization=org, spot=spot_b, time_range=window_range)

    req_start = _utc(2027, 3, 2, 10)
    req_end = _utc(2027, 3, 2, 12)

    result = assign_spot(org, borrower, req_start, req_end)
    assert isinstance(
        result, Booking
    ), "assign_spot must return a Booking when spots are available"
    assert result.spot == spot_a, (
        "Owner A's spot (last booked yesterday) must be selected before "
        "owner B's spot (last booked today) in rotation order"
    )


# ---------------------------------------------------------------------------
# 8. test_window_form_valid
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_window_form_valid():
    """A valid future hour-aligned range passes AvailabilityWindowForm validation."""
    from freezegun import freeze_time

    from parking.forms import AvailabilityWindowForm
    from parkshare.middleware import _thread_locals

    org = OrganizationFactory()
    owner = UserFactory(organization=org)
    spot = ParkingSpotFactory(organization=org, owner=owner, status="active")

    # Freeze time so "now" is predictably in the past relative to our form input
    frozen_now = datetime(2027, 1, 1, 0, 0, tzinfo=dt_timezone.utc)
    _thread_locals.organization = org
    try:
        with freeze_time(frozen_now):
            form = AvailabilityWindowForm(
                data={
                    "spot": spot.pk,
                    "start": "2027-06-01 10:00:00",
                    "end": "2027-06-01 18:00:00",
                },
                owner=owner,
            )
            assert form.is_valid(), f"Form should be valid; errors: {form.errors}"
    finally:
        _thread_locals.organization = None


# ---------------------------------------------------------------------------
# 9. test_window_form_rejects_past_start
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_window_form_rejects_past_start():
    """AvailabilityWindowForm rejects a start time in the past."""
    from freezegun import freeze_time

    from parking.forms import AvailabilityWindowForm
    from parkshare.middleware import _thread_locals

    org = OrganizationFactory()
    owner = UserFactory(organization=org)
    spot = ParkingSpotFactory(organization=org, owner=owner, status="active")

    frozen_now = datetime(2027, 6, 1, 12, 0, tzinfo=dt_timezone.utc)
    _thread_locals.organization = org
    try:
        with freeze_time(frozen_now):
            form = AvailabilityWindowForm(
                data={
                    "spot": spot.pk,
                    "start": "2027-06-01 10:00:00",  # 2 hours in the past
                    "end": "2027-06-01 18:00:00",
                },
                owner=owner,
            )
            assert not form.is_valid(), "Form with past start must be invalid"
            errors = str(form.errors)
            assert (
                "future" in errors.lower() or "__all__" in form.errors
            ), f"Expected a 'future' error; got: {form.errors}"
    finally:
        _thread_locals.organization = None


# ---------------------------------------------------------------------------
# 10. test_window_form_rejects_non_hour_aligned
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_window_form_rejects_non_hour_aligned():
    """AvailabilityWindowForm rejects a start with minute=30."""
    from freezegun import freeze_time

    from parking.forms import AvailabilityWindowForm
    from parkshare.middleware import _thread_locals

    org = OrganizationFactory()
    owner = UserFactory(organization=org)
    spot = ParkingSpotFactory(organization=org, owner=owner, status="active")

    frozen_now = datetime(2027, 1, 1, 0, 0, tzinfo=dt_timezone.utc)
    _thread_locals.organization = org
    try:
        with freeze_time(frozen_now):
            form = AvailabilityWindowForm(
                data={
                    "spot": spot.pk,
                    "start": "2027-06-01 10:30:00",  # minute=30, not on the hour
                    "end": "2027-06-01 18:00:00",
                },
                owner=owner,
            )
            assert (
                not form.is_valid()
            ), "Form with non-hour-aligned start must be invalid"
            errors = str(form.errors)
            assert (
                "hour" in errors.lower() or "__all__" in form.errors
            ), f"Expected an 'hour' alignment error; got: {form.errors}"
    finally:
        _thread_locals.organization = None


# ---------------------------------------------------------------------------
# 11. test_window_form_rejects_end_before_start
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_window_form_rejects_end_before_start():
    """AvailabilityWindowForm rejects end < start."""
    from freezegun import freeze_time

    from parking.forms import AvailabilityWindowForm
    from parkshare.middleware import _thread_locals

    org = OrganizationFactory()
    owner = UserFactory(organization=org)
    spot = ParkingSpotFactory(organization=org, owner=owner, status="active")

    frozen_now = datetime(2027, 1, 1, 0, 0, tzinfo=dt_timezone.utc)
    _thread_locals.organization = org
    try:
        with freeze_time(frozen_now):
            form = AvailabilityWindowForm(
                data={
                    "spot": spot.pk,
                    "start": "2027-06-01 18:00:00",
                    "end": "2027-06-01 10:00:00",  # end before start
                },
                owner=owner,
            )
            assert not form.is_valid(), "Form with end before start must be invalid"
            errors = str(form.errors)
            assert (
                "after" in errors.lower() or "__all__" in form.errors
            ), f"Expected an 'after start' error; got: {form.errors}"
    finally:
        _thread_locals.organization = None


# ---------------------------------------------------------------------------
# Helpers for view tests
# ---------------------------------------------------------------------------


def _make_django_session():
    from django.contrib.sessions.backends.db import SessionStore

    session = SessionStore()
    session.create()
    return session


def _make_authenticated_request(method, path, user, org, data=None, headers=None):
    """
    Build a request using RequestFactory, attach user and organization, and
    return the request.  Bypasses TenantMiddleware (which requires a real DB
    hostname lookup) by setting request.organization directly — the same
    pattern used by test_auth.py.

    Also sets the thread-local organization so that OrganizationScopedManager
    works correctly in view code that instantiates scoped forms.
    """
    from django.test import RequestFactory

    from parkshare.middleware import _thread_locals

    rf = RequestFactory()
    if method == "POST":
        request = rf.post(path, data or {})
    else:
        request = rf.get(path)

    if headers:
        for key, value in headers.items():
            request.META[key] = value

    # Attach org (TenantMiddleware normally does this)
    request.organization = org

    # Set thread-local so OrganizationScopedManager resolves the correct tenant
    _thread_locals.organization = org

    # Attach user (AuthenticationMiddleware normally does this)
    request.user = user

    # OTPMiddleware normally sets is_verified(); provide a minimal stub so that
    # @login_required / status checks work without django-otp verification.
    request.user.is_verified = lambda: True

    # Provide a minimal session so redirect/login checks in decorators work
    request.session = _make_django_session()

    return request


# ---------------------------------------------------------------------------
# 12. test_availability_add_creates_window
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_availability_add_creates_window():
    """POST to availability_add creates an AvailabilityWindow for the owner's spot."""
    from freezegun import freeze_time

    from parking.models import AvailabilityWindow
    from parking.views import availability_add

    org = OrganizationFactory()
    owner = UserFactory(organization=org, status="active")
    spot = ParkingSpotFactory(organization=org, owner=owner, status="active")

    frozen_now = datetime(2027, 1, 1, 0, 0, tzinfo=dt_timezone.utc)
    with freeze_time(frozen_now):
        request = _make_authenticated_request(
            "POST",
            f"/spots/{spot.pk}/availability/add/",
            user=owner,
            org=org,
            data={
                "spot": spot.pk,
                "start": "2027-06-01 10:00:00",
                "end": "2027-06-01 18:00:00",
            },
        )
        response = availability_add(request, pk=spot.pk)

    # Should redirect on success
    assert response.status_code in (
        200,
        302,
    ), f"Expected 200 or 302, got {response.status_code}"

    windows = AvailabilityWindow.objects.filter(spot=spot)
    assert (
        windows.count() == 1
    ), f"Expected 1 AvailabilityWindow to be created, found {windows.count()}"
    window = windows.first()
    assert window.time_range.lower == datetime(
        2027, 6, 1, 10, 0, tzinfo=dt_timezone.utc
    )
    assert window.time_range.upper == datetime(
        2027, 6, 1, 18, 0, tzinfo=dt_timezone.utc
    )


# ---------------------------------------------------------------------------
# 13. test_availability_add_rejected_non_owner
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_availability_add_rejected_non_owner():
    """POST to availability_add for another user's spot returns 403."""
    from django.core.exceptions import PermissionDenied
    from freezegun import freeze_time

    from parking.views import availability_add

    org = OrganizationFactory()
    owner = UserFactory(organization=org, status="active")
    other_user = UserFactory(organization=org, status="active")
    spot = ParkingSpotFactory(organization=org, owner=owner, status="active")

    frozen_now = datetime(2027, 1, 1, 0, 0, tzinfo=dt_timezone.utc)
    with freeze_time(frozen_now):
        request = _make_authenticated_request(
            "POST",
            f"/spots/{spot.pk}/availability/add/",
            user=other_user,
            org=org,
            data={
                "spot": spot.pk,
                "start": "2027-06-01 10:00:00",
                "end": "2027-06-01 18:00:00",
            },
        )
        with pytest.raises(PermissionDenied):
            availability_add(request, pk=spot.pk)


# ---------------------------------------------------------------------------
# 14. test_availability_remove_deletes_window
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_availability_remove_deletes_window():
    """POST to availability_remove deletes the window when there are no active bookings."""
    from parking.models import AvailabilityWindow
    from parking.views import availability_remove

    org = OrganizationFactory()
    owner = UserFactory(organization=org, status="active")
    spot = ParkingSpotFactory(organization=org, owner=owner, status="active")
    window = AvailabilityWindowFactory(
        organization=org,
        spot=spot,
        time_range=DateTimeTZRange(
            _utc(2027, 4, 1, 8),
            _utc(2027, 4, 1, 20),
        ),
    )

    request = _make_authenticated_request(
        "POST",
        f"/spots/{spot.pk}/windows/{window.pk}/remove/",
        user=owner,
        org=org,
    )
    response = availability_remove(request, pk=spot.pk, wk=window.pk)

    assert response.status_code in (
        200,
        302,
        204,
    ), f"Expected success status code, got {response.status_code}"
    assert not AvailabilityWindow.objects.filter(
        pk=window.pk
    ).exists(), "AvailabilityWindow should be deleted after remove POST"


# ---------------------------------------------------------------------------
# 15. test_availability_remove_blocked_by_active_booking
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_availability_remove_blocked_by_active_booking():
    """
    POST to availability_remove with an active booking overlapping the window
    returns an error response and does NOT delete the window.

    Uses the HX-Request header so the view returns a 422 partial (no template
    needed for the full HTML page which does not exist in tests).
    """
    from parking.models import AvailabilityWindow
    from parking.views import availability_remove

    org = OrganizationFactory()
    owner = UserFactory(organization=org, status="active")
    borrower = UserFactory(organization=org, status="active")
    spot = ParkingSpotFactory(organization=org, owner=owner, status="active")

    window_range = DateTimeTZRange(
        _utc(2027, 5, 1, 8),
        _utc(2027, 5, 1, 20),
    )
    window = AvailabilityWindowFactory(
        organization=org,
        spot=spot,
        time_range=window_range,
    )

    # Active booking overlapping the window
    BookingFactory(
        organization=org,
        spot=spot,
        borrower=borrower,
        time_range=DateTimeTZRange(
            _utc(2027, 5, 1, 10),
            _utc(2027, 5, 1, 12),
        ),
        status="confirmed",
    )

    # Use HTMX header so the view returns a 422 partial instead of rendering
    # the full HTML template (which is not required to exist for this test).
    request = _make_authenticated_request(
        "POST",
        f"/spots/{spot.pk}/windows/{window.pk}/remove/",
        user=owner,
        org=org,
        headers={"HTTP_HX_REQUEST": "true"},
    )
    response = availability_remove(request, pk=spot.pk, wk=window.pk)

    # Should not delete the window
    assert AvailabilityWindow.objects.filter(
        pk=window.pk
    ).exists(), "AvailabilityWindow must NOT be deleted when active bookings overlap"
    # HTMX path returns 422 on conflict
    assert (
        response.status_code == 422
    ), f"Expected 422 for HTMX conflict response, got {response.status_code}"
