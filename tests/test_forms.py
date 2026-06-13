"""
Unit tests for parking.forms and accounts.forms — form validation coverage.

Covers the uncovered lines in:
  parking/forms.py lines 34-65, 100, 144, 155
  (BookingRequestForm.clean, EarlyReleaseForm.clean_release_to, AvailabilityWindowForm.clean)
"""

from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

import factory
import pytest
from django.test import override_settings
from freezegun import freeze_time
from psycopg2.extras import DateTimeTZRange


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc(year, month, day, hour, minute=0, second=0):
    return datetime(year, month, day, hour, minute, second, tzinfo=dt_timezone.utc)


def _naive(year, month, day, hour, minute=0):
    """Naive local datetime (matches form input format)."""
    return datetime(year, month, day, hour, minute)


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------


class OrganizationFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "parking.Organization"

    name = factory.Sequence(lambda n: f"FormOrg {n}")
    hostname = factory.Sequence(lambda n: f"formorg{n}.parkshare.test")
    support_email = factory.LazyAttribute(lambda o: f"support@{o.hostname}")
    registration_mode = "invite_only"
    timezone = "America/Los_Angeles"
    booking_horizon_baseline_days = 3
    booking_horizon_max_days = 30
    listing_to_horizon_ratio = 10
    tier_metric_window_days = 180
    launch_grace_days = 14
    launch_grace_horizon_days = 14
    max_booking_hours = 48
    launched_at = None


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "accounts.User"

    organization = factory.SubFactory(OrganizationFactory)
    email = factory.Sequence(lambda n: f"formuser{n}@example.com")
    display_name = factory.Sequence(lambda n: f"Form User {n}")
    status = "active"
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
    spot_number = factory.Sequence(lambda n: f"FM{n:04d}")
    status = "active"


# ---------------------------------------------------------------------------
# BookingRequestForm
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_booking_request_form_valid():
    """BookingRequestForm is valid with a future, hour-aligned start and end."""
    from parking.forms import BookingRequestForm

    org = OrganizationFactory(max_booking_hours=48)

    frozen_now = _naive(2027, 7, 1, 10)
    with freeze_time(frozen_now):
        form = BookingRequestForm(
            data={
                "start": "2027-07-02T10:00",
                "end": "2027-07-02T14:00",
            },
            org=org,
        )
        assert form.is_valid(), f"Form should be valid; errors: {form.errors}"


@pytest.mark.django_db
def test_booking_request_form_start_in_past():
    """BookingRequestForm is invalid when start is in the past."""
    from parking.forms import BookingRequestForm

    org = OrganizationFactory()

    frozen_now = _naive(2027, 7, 1, 10)
    with freeze_time(frozen_now):
        form = BookingRequestForm(
            data={
                "start": "2027-06-30T10:00",  # past
                "end": "2027-06-30T14:00",
            },
            org=org,
        )
        assert not form.is_valid(), "Form should be invalid when start is in the past"
        assert any("future" in str(e).lower() for e in form.non_field_errors()), (
            f"Expected 'future' in error; got {form.non_field_errors()}"
        )


@pytest.mark.django_db
def test_booking_request_form_start_not_on_hour():
    """BookingRequestForm is invalid when start is not on the hour."""
    from parking.forms import BookingRequestForm

    org = OrganizationFactory()

    frozen_now = _naive(2027, 7, 1, 8)
    with freeze_time(frozen_now):
        form = BookingRequestForm(
            data={
                "start": "2027-07-02T10:30",  # not on the hour
                "end": "2027-07-02T14:00",
            },
            org=org,
        )
        assert not form.is_valid(), "Form should be invalid when start is not on the hour"


@pytest.mark.django_db
def test_booking_request_form_end_not_on_hour():
    """BookingRequestForm is invalid when end is not on the hour."""
    from parking.forms import BookingRequestForm

    org = OrganizationFactory()

    frozen_now = _naive(2027, 7, 1, 8)
    with freeze_time(frozen_now):
        form = BookingRequestForm(
            data={
                "start": "2027-07-02T10:00",
                "end": "2027-07-02T14:45",  # not on the hour
            },
            org=org,
        )
        assert not form.is_valid(), "Form should be invalid when end is not on the hour"


@pytest.mark.django_db
def test_booking_request_form_end_before_start():
    """BookingRequestForm is invalid when end is before start."""
    from parking.forms import BookingRequestForm

    org = OrganizationFactory()

    frozen_now = _naive(2027, 7, 1, 8)
    with freeze_time(frozen_now):
        form = BookingRequestForm(
            data={
                "start": "2027-07-02T14:00",
                "end": "2027-07-02T10:00",  # end before start
            },
            org=org,
        )
        assert not form.is_valid(), "Form should be invalid when end is before start"


@pytest.mark.django_db
def test_booking_request_form_exceeds_max_booking_hours():
    """BookingRequestForm is invalid when duration exceeds org.max_booking_hours."""
    from parking.forms import BookingRequestForm

    org = OrganizationFactory(max_booking_hours=4)  # 4 hour max

    frozen_now = _naive(2027, 7, 1, 8)
    with freeze_time(frozen_now):
        form = BookingRequestForm(
            data={
                "start": "2027-07-02T10:00",
                "end": "2027-07-02T18:00",  # 8 hours > 4h max
            },
            org=org,
        )
        assert not form.is_valid(), "Form should be invalid when duration exceeds max_booking_hours"
        assert any("maximum" in str(e).lower() for e in form.non_field_errors()), (
            f"Expected 'maximum' in error; got {form.non_field_errors()}"
        )


@pytest.mark.django_db
def test_booking_request_form_without_org_no_duration_check():
    """BookingRequestForm without org= does not check max_booking_hours."""
    from parking.forms import BookingRequestForm

    frozen_now = _naive(2027, 7, 1, 8)
    with freeze_time(frozen_now):
        form = BookingRequestForm(
            data={
                "start": "2027-07-02T10:00",
                "end": "2027-07-10T10:00",  # 8 days duration
            },
            # no org= argument
        )
        # Without org, max duration is not checked
        assert form.is_valid(), f"Form should be valid without org; errors: {form.errors}"


# ---------------------------------------------------------------------------
# EarlyReleaseForm
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_early_release_form_valid():
    """EarlyReleaseForm is valid with a future, hour-aligned release time before booking end."""
    from parking.forms import EarlyReleaseForm
    from parking.models import Booking

    org = OrganizationFactory()
    owner = UserFactory(organization=org)
    borrower = UserFactory(organization=org)
    spot = ParkingSpotFactory(organization=org, owner=owner)

    booking = Booking.objects.create(
        organization=org,
        spot=spot,
        borrower=borrower,
        time_range=DateTimeTZRange(_utc(2027, 9, 1, 8), _utc(2027, 9, 1, 16)),
        status="confirmed",
    )

    frozen_now = _naive(2027, 9, 1, 10)
    with freeze_time(frozen_now):
        form = EarlyReleaseForm(
            data={"release_to": "2027-09-01T12:00"},
            booking=booking,
        )
        assert form.is_valid(), f"Form should be valid; errors: {form.errors}"


@pytest.mark.django_db
def test_early_release_form_not_on_hour():
    """EarlyReleaseForm is invalid when release_to is not on the hour."""
    from parking.forms import EarlyReleaseForm
    from parking.models import Booking

    org = OrganizationFactory()
    owner = UserFactory(organization=org)
    borrower = UserFactory(organization=org)
    spot = ParkingSpotFactory(organization=org, owner=owner)

    booking = Booking.objects.create(
        organization=org,
        spot=spot,
        borrower=borrower,
        time_range=DateTimeTZRange(_utc(2027, 9, 2, 8), _utc(2027, 9, 2, 16)),
        status="confirmed",
    )

    frozen_now = _naive(2027, 9, 2, 10)
    with freeze_time(frozen_now):
        form = EarlyReleaseForm(
            data={"release_to": "2027-09-02T12:30"},  # not on hour
            booking=booking,
        )
        assert not form.is_valid(), "Form should be invalid when release_to is not on the hour"


@pytest.mark.django_db
def test_early_release_form_in_past():
    """EarlyReleaseForm is invalid when release_to is in the past."""
    from parking.forms import EarlyReleaseForm
    from parking.models import Booking

    org = OrganizationFactory()
    owner = UserFactory(organization=org)
    borrower = UserFactory(organization=org)
    spot = ParkingSpotFactory(organization=org, owner=owner)

    booking = Booking.objects.create(
        organization=org,
        spot=spot,
        borrower=borrower,
        time_range=DateTimeTZRange(_utc(2027, 9, 3, 8), _utc(2027, 9, 3, 16)),
        status="confirmed",
    )

    frozen_now = _naive(2027, 9, 3, 14)
    with freeze_time(frozen_now):
        form = EarlyReleaseForm(
            data={"release_to": "2027-09-03T12:00"},  # in past relative to now=14:00
            booking=booking,
        )
        assert not form.is_valid(), "Form should be invalid when release_to is in the past"


@pytest.mark.django_db
def test_early_release_form_at_or_after_booking_end():
    """EarlyReleaseForm is invalid when release_to >= booking end."""
    from parking.forms import EarlyReleaseForm
    from parking.models import Booking

    org = OrganizationFactory()
    owner = UserFactory(organization=org)
    borrower = UserFactory(organization=org)
    spot = ParkingSpotFactory(organization=org, owner=owner)

    booking = Booking.objects.create(
        organization=org,
        spot=spot,
        borrower=borrower,
        time_range=DateTimeTZRange(_utc(2027, 9, 4, 8), _utc(2027, 9, 4, 16)),
        status="confirmed",
    )

    frozen_now = _naive(2027, 9, 4, 10)
    with freeze_time(frozen_now):
        form = EarlyReleaseForm(
            data={"release_to": "2027-09-04T16:00"},  # equals booking end
            booking=booking,
        )
        assert not form.is_valid(), "Form should be invalid when release_to >= booking end"


# ---------------------------------------------------------------------------
# AvailabilityWindowForm
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_availability_window_form_valid():
    """AvailabilityWindowForm is valid with future, hour-aligned start and end."""
    from parking.forms import AvailabilityWindowForm

    org = OrganizationFactory()
    owner = UserFactory(organization=org)
    spot = ParkingSpotFactory(organization=org, owner=owner, status="active")

    frozen_now = _naive(2027, 10, 1, 8)
    with freeze_time(frozen_now):
        form = AvailabilityWindowForm(
            data={
                "spot": str(spot.pk),
                "start": "2027-10-05T08:00",
                "end": "2027-10-10T20:00",
            },
            owner=owner,
        )
        assert form.is_valid(), f"Form should be valid; errors: {form.errors}"


@pytest.mark.django_db
def test_availability_window_form_start_in_past():
    """AvailabilityWindowForm is invalid when start is in the past."""
    from parking.forms import AvailabilityWindowForm

    org = OrganizationFactory()
    owner = UserFactory(organization=org)
    spot = ParkingSpotFactory(organization=org, owner=owner, status="active")

    frozen_now = _naive(2027, 10, 5, 12)
    with freeze_time(frozen_now):
        form = AvailabilityWindowForm(
            data={
                "spot": str(spot.pk),
                "start": "2027-10-01T08:00",  # in the past
                "end": "2027-10-10T20:00",
            },
            owner=owner,
        )
        assert not form.is_valid(), "Form should be invalid when start is in the past"


@pytest.mark.django_db
def test_availability_window_form_start_not_on_hour():
    """AvailabilityWindowForm is invalid when start is not on the hour."""
    from parking.forms import AvailabilityWindowForm

    org = OrganizationFactory()
    owner = UserFactory(organization=org)
    spot = ParkingSpotFactory(organization=org, owner=owner, status="active")

    frozen_now = _naive(2027, 10, 1, 8)
    with freeze_time(frozen_now):
        form = AvailabilityWindowForm(
            data={
                "spot": str(spot.pk),
                "start": "2027-10-05T08:30",  # not on the hour
                "end": "2027-10-10T20:00",
            },
            owner=owner,
        )
        assert not form.is_valid(), "Form should be invalid when start is not on the hour"


@pytest.mark.django_db
def test_availability_window_form_end_not_on_hour():
    """AvailabilityWindowForm is invalid when end is not on the hour."""
    from parking.forms import AvailabilityWindowForm

    org = OrganizationFactory()
    owner = UserFactory(organization=org)
    spot = ParkingSpotFactory(organization=org, owner=owner, status="active")

    frozen_now = _naive(2027, 10, 1, 8)
    with freeze_time(frozen_now):
        form = AvailabilityWindowForm(
            data={
                "spot": str(spot.pk),
                "start": "2027-10-05T08:00",
                "end": "2027-10-10T20:45",  # not on the hour
            },
            owner=owner,
        )
        assert not form.is_valid(), "Form should be invalid when end is not on the hour"


@pytest.mark.django_db
def test_availability_window_form_end_before_start():
    """AvailabilityWindowForm is invalid when end is before start."""
    from parking.forms import AvailabilityWindowForm

    org = OrganizationFactory()
    owner = UserFactory(organization=org)
    spot = ParkingSpotFactory(organization=org, owner=owner, status="active")

    frozen_now = _naive(2027, 10, 1, 8)
    with freeze_time(frozen_now):
        form = AvailabilityWindowForm(
            data={
                "spot": str(spot.pk),
                "start": "2027-10-10T08:00",
                "end": "2027-10-05T20:00",  # end before start
            },
            owner=owner,
        )
        assert not form.is_valid(), "Form should be invalid when end is before start"


@pytest.mark.django_db
def test_availability_window_form_missing_fields_returns_cleaned():
    """AvailabilityWindowForm.clean returns cleaned data early when start/end missing."""
    from parking.forms import AvailabilityWindowForm

    org = OrganizationFactory()
    owner = UserFactory(organization=org)

    # Only supply spot — no start/end
    form = AvailabilityWindowForm(
        data={},  # empty data
        owner=owner,
    )
    assert not form.is_valid()
    # Errors should be on start/end fields (required), not a non-field error
    assert "start" in form.errors or "end" in form.errors
