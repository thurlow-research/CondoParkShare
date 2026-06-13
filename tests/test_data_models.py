"""
Unit tests for CondoParkShare Step 4 — data models.

Covers:
  ParkingSpot (1-4)
  AvailabilityWindow (5-6)
  Booking (7-13)
"""

from datetime import datetime
from datetime import timezone as dt_timezone

import factory
import pytest
from django.contrib.postgres.indexes import GistIndex
from psycopg2.extras import DateTimeTZRange

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
        manager = model_class.objects
        password = kwargs.pop("password", "test-password-secure!")
        return manager.create_user(*args, password=password, **kwargs)


class ParkingSpotFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "parking.ParkingSpot"

    organization = factory.SubFactory(OrganizationFactory)
    owner = None
    spot_number = factory.Sequence(lambda n: f"P{n:04d}")
    status = "active"


class AvailabilityWindowFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "parking.AvailabilityWindow"

    organization = factory.SubFactory(OrganizationFactory)
    spot = factory.SubFactory(
        ParkingSpotFactory,
        organization=factory.SelfAttribute("..organization"),
    )
    time_range = DateTimeTZRange(
        datetime(2026, 7, 1, 8, 0, tzinfo=dt_timezone.utc),
        datetime(2026, 7, 1, 18, 0, tzinfo=dt_timezone.utc),
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
        datetime(2026, 7, 1, 10, 0, tzinfo=dt_timezone.utc),
        datetime(2026, 7, 1, 12, 0, tzinfo=dt_timezone.utc),
    )
    status = "confirmed"


# ---------------------------------------------------------------------------
# ParkingSpot tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_spot_number_is_string():
    """spot_number accepts an alphanumeric string like 'P3076'."""
    spot = ParkingSpotFactory(spot_number="P3076")
    spot.refresh_from_db()

    assert (
        spot.spot_number == "P3076"
    ), f"Expected spot_number='P3076', got {spot.spot_number!r}"
    assert isinstance(
        spot.spot_number, str
    ), f"spot_number should be a str, got {type(spot.spot_number)}"


@pytest.mark.django_db
def test_spot_unique_per_org():
    """
    The same spot_number in different orgs is allowed.
    The same spot_number in the same org raises IntegrityError.
    """
    from django.db import IntegrityError

    org1 = OrganizationFactory()
    org2 = OrganizationFactory()

    # Different orgs — must succeed
    spot1 = ParkingSpotFactory(organization=org1, spot_number="A100")
    spot2 = ParkingSpotFactory(organization=org2, spot_number="A100")
    assert spot1.pk != spot2.pk

    # Same org — must fail
    with pytest.raises(IntegrityError):
        ParkingSpotFactory(organization=org1, spot_number="A100")


@pytest.mark.django_db
def test_spot_scoped_manager():
    """ParkingSpot.scoped.all() only returns spots for the current org."""
    from parking.models import ParkingSpot
    from parkshare.middleware import _thread_locals

    org1 = OrganizationFactory()
    org2 = OrganizationFactory()

    spot1 = ParkingSpotFactory(organization=org1, spot_number="S001")
    ParkingSpotFactory(organization=org2, spot_number="S002")

    _thread_locals.organization = org1
    try:
        qs = ParkingSpot.scoped.all()
        pks = list(qs.values_list("pk", flat=True))
        assert spot1.pk in pks, "org1's spot must appear in scoped queryset"
        assert (
            len(pks) == 1
        ), f"Expected 1 spot for org1, got {len(pks)}. scoped manager must not leak other orgs."
    finally:
        _thread_locals.organization = None


@pytest.mark.django_db
def test_spot_owner_nullable():
    """ParkingSpot can be created with owner=None."""
    spot = ParkingSpotFactory(owner=None)
    spot.refresh_from_db()

    assert spot.owner is None, f"owner should be None, got {spot.owner!r}"


# ---------------------------------------------------------------------------
# AvailabilityWindow tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_window_time_range_is_range_field():
    """time_range accepts a DateTimeTZRange and stores it correctly."""
    lower = datetime(2026, 8, 1, 9, 0, tzinfo=dt_timezone.utc)
    upper = datetime(2026, 8, 1, 17, 0, tzinfo=dt_timezone.utc)
    expected_range = DateTimeTZRange(lower, upper)

    window = AvailabilityWindowFactory(time_range=expected_range)
    window.refresh_from_db()

    assert (
        window.time_range.lower == lower
    ), f"time_range.lower mismatch: {window.time_range.lower!r} != {lower!r}"
    assert (
        window.time_range.upper == upper
    ), f"time_range.upper mismatch: {window.time_range.upper!r} != {upper!r}"


def test_window_gist_index_exists():
    """AvailabilityWindow._meta.indexes contains a GistIndex on time_range."""
    from parking.models import AvailabilityWindow

    gist_indexes = [
        idx for idx in AvailabilityWindow._meta.indexes if isinstance(idx, GistIndex)
    ]
    assert (
        len(gist_indexes) >= 1
    ), "AvailabilityWindow._meta.indexes must contain at least one GistIndex"

    gist_fields = [field for idx in gist_indexes for field in idx.fields]
    assert (
        "time_range" in gist_fields
    ), f"GistIndex must include 'time_range', found fields: {gist_fields}"


# ---------------------------------------------------------------------------
# Booking tests
# ---------------------------------------------------------------------------


def test_booking_exclusion_constraint_in_migration():
    """The migration file contains the 'booking_no_overlap' exclusion constraint."""
    import os

    migration_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "parking",
        "migrations",
        "0001_initial.py",
    )
    migration_path = os.path.normpath(migration_path)

    with open(migration_path, "r") as f:
        content = f.read()

    assert (
        "booking_no_overlap" in content
    ), f"Expected 'booking_no_overlap' in migration file {migration_path}"


@pytest.mark.django_db
def test_booking_overlap_rejected_by_db():
    """Two confirmed bookings on the same spot with overlapping time_range raise IntegrityError."""
    from django.db import IntegrityError

    org = OrganizationFactory()
    spot = ParkingSpotFactory(organization=org)

    tr1 = DateTimeTZRange(
        datetime(2026, 9, 1, 10, 0, tzinfo=dt_timezone.utc),
        datetime(2026, 9, 1, 14, 0, tzinfo=dt_timezone.utc),
    )
    tr2 = DateTimeTZRange(
        datetime(2026, 9, 1, 12, 0, tzinfo=dt_timezone.utc),
        datetime(2026, 9, 1, 16, 0, tzinfo=dt_timezone.utc),
    )

    BookingFactory(organization=org, spot=spot, time_range=tr1, status="confirmed")

    with pytest.raises(IntegrityError):
        BookingFactory(organization=org, spot=spot, time_range=tr2, status="confirmed")


@pytest.mark.django_db
def test_booking_adjacent_allowed():
    """
    A booking ending at 14:00 and a new booking starting at 14:00 (adjacent,
    not overlapping) must NOT be rejected by the DB exclusion constraint.
    Buffer enforcement is app-layer only.
    """
    org = OrganizationFactory()
    spot = ParkingSpotFactory(organization=org)

    tr1 = DateTimeTZRange(
        datetime(2026, 9, 2, 10, 0, tzinfo=dt_timezone.utc),
        datetime(2026, 9, 2, 14, 0, tzinfo=dt_timezone.utc),
    )
    tr2 = DateTimeTZRange(
        datetime(2026, 9, 2, 14, 0, tzinfo=dt_timezone.utc),
        datetime(2026, 9, 2, 18, 0, tzinfo=dt_timezone.utc),
    )

    b1 = BookingFactory(organization=org, spot=spot, time_range=tr1, status="confirmed")
    b2 = BookingFactory(organization=org, spot=spot, time_range=tr2, status="confirmed")

    assert b1.pk is not None
    assert b2.pk is not None


@pytest.mark.django_db
def test_cancelled_booking_does_not_block_rebooking():
    """
    A cancelled booking must not block a new booking in the same time slot,
    because the exclusion constraint condition excludes cancelled statuses.
    """
    org = OrganizationFactory()
    spot = ParkingSpotFactory(organization=org)

    tr = DateTimeTZRange(
        datetime(2026, 9, 3, 10, 0, tzinfo=dt_timezone.utc),
        datetime(2026, 9, 3, 12, 0, tzinfo=dt_timezone.utc),
    )

    # Create a booking then cancel it
    cancelled = BookingFactory(
        organization=org, spot=spot, time_range=tr, status="confirmed"
    )
    cancelled.status = "cancelled_owner"
    cancelled.save(update_fields=["status"])

    # New booking in same slot must succeed
    new_booking = BookingFactory(
        organization=org, spot=spot, time_range=tr, status="confirmed"
    )
    assert new_booking.pk is not None


@pytest.mark.django_db
def test_booking_borrower_nullable():
    """Booking can be created with borrower=None (erasure support)."""
    org = OrganizationFactory()
    spot = ParkingSpotFactory(organization=org)

    tr = DateTimeTZRange(
        datetime(2026, 9, 4, 10, 0, tzinfo=dt_timezone.utc),
        datetime(2026, 9, 4, 12, 0, tzinfo=dt_timezone.utc),
    )

    booking = BookingFactory(organization=org, spot=spot, borrower=None, time_range=tr)
    booking.refresh_from_db()

    assert (
        booking.borrower is None
    ), f"borrower should be None, got {booking.borrower!r}"


@pytest.mark.django_db
def test_booking_penalty_hours_default_zero():
    """A new Booking has penalty_hours=0."""
    booking = BookingFactory()
    booking.refresh_from_db()

    assert (
        booking.penalty_hours == 0
    ), f"penalty_hours should default to 0, got {booking.penalty_hours!r}"


@pytest.mark.django_db
def test_booking_is_anonymized_default_false():
    """A new Booking has is_anonymized=False."""
    booking = BookingFactory()
    booking.refresh_from_db()

    assert (
        booking.is_anonymized is False
    ), f"is_anonymized should default to False, got {booking.is_anonymized!r}"
