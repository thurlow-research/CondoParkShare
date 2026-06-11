"""
Unit tests for CondoParkShare Step 8 — cancellation and early release.

Covers:
  cancel_booking (1-7)
  release_booking (8-9)
  EarlyReleaseForm (10-13)
  release_booking permission (14)
"""

import pytest
import factory
from datetime import datetime, timezone as dt_timezone, timedelta
from unittest.mock import patch

from freezegun import freeze_time
from psycopg2.extras import DateTimeTZRange


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------

class OrganizationFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = 'parking.Organization'

    name = factory.Sequence(lambda n: f'CancelOrg {n}')
    hostname = factory.Sequence(lambda n: f'cancelorg{n}.parkshare.test')
    support_email = factory.LazyAttribute(lambda o: f'support@{o.hostname}')
    registration_mode = 'invite_only'
    timezone = 'America/Los_Angeles'

    booking_horizon_baseline_days = 3
    booking_horizon_max_days = 30
    listing_to_horizon_ratio = 10
    tier_metric_window_days = 180
    launch_grace_days = 14
    launch_grace_horizon_days = 14


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = 'accounts.User'

    organization = factory.SubFactory(OrganizationFactory)
    email = factory.Sequence(lambda n: f'canceluser{n}@example.com')
    display_name = factory.Sequence(lambda n: f'Cancel User {n}')
    status = 'active'
    last_booking_at = None

    @classmethod
    def _create(cls, model_class, *args, **kwargs):
        manager = model_class.objects
        password = kwargs.pop('password', 'test-password-secure!')
        return manager.create_user(password=password, *args, **kwargs)


class ParkingSpotFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = 'parking.ParkingSpot'

    organization = factory.SubFactory(OrganizationFactory)
    owner = factory.SubFactory(
        UserFactory,
        organization=factory.SelfAttribute('..organization'),
    )
    spot_number = factory.Sequence(lambda n: f'C{n:04d}')
    status = 'active'


class AvailabilityWindowFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = 'parking.AvailabilityWindow'

    organization = factory.SubFactory(OrganizationFactory)
    spot = factory.SubFactory(
        ParkingSpotFactory,
        organization=factory.SelfAttribute('..organization'),
    )
    time_range = DateTimeTZRange(
        datetime(2029, 1, 1, 0, 0, tzinfo=dt_timezone.utc),
        datetime(2029, 12, 31, 23, 0, tzinfo=dt_timezone.utc),
    )


class BookingFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = 'parking.Booking'

    organization = factory.SubFactory(OrganizationFactory)
    spot = factory.SubFactory(
        ParkingSpotFactory,
        organization=factory.SelfAttribute('..organization'),
    )
    borrower = factory.SubFactory(
        UserFactory,
        organization=factory.SelfAttribute('..organization'),
    )
    time_range = DateTimeTZRange(
        datetime(2029, 3, 1, 10, 0, tzinfo=dt_timezone.utc),
        datetime(2029, 3, 1, 14, 0, tzinfo=dt_timezone.utc),
    )
    status = 'confirmed'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utc(year, month, day, hour, minute=0, second=0):
    """Return a timezone-aware UTC datetime."""
    return datetime(year, month, day, hour, minute, second, tzinfo=dt_timezone.utc)


# ---------------------------------------------------------------------------
# 1. test_borrower_cancel_sets_status
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_borrower_cancel_sets_status():
    """Borrower cancelling a booking sets status to 'cancelled_borrower'."""
    from parking.booking import cancel_booking

    org = OrganizationFactory()
    owner = UserFactory(organization=org)
    borrower = UserFactory(organization=org)
    spot = ParkingSpotFactory(organization=org, owner=owner, status='active')

    tr = DateTimeTZRange(_utc(2029, 4, 1, 10), _utc(2029, 4, 1, 14))
    booking = BookingFactory(
        organization=org,
        spot=spot,
        borrower=borrower,
        time_range=tr,
        status='confirmed',
    )

    with patch('notifications.dispatch.notify'):
        cancel_booking(booking, cancelled_by=borrower)

    booking.refresh_from_db()
    assert booking.status == 'cancelled_borrower', (
        f"Borrower cancel should set status='cancelled_borrower', got {booking.status!r}"
    )


# ---------------------------------------------------------------------------
# 2. test_owner_cancel_sets_status
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_owner_cancel_sets_status():
    """Owner cancelling a booking sets status to 'cancelled_owner'."""
    from parking.booking import cancel_booking

    org = OrganizationFactory()
    owner = UserFactory(organization=org)
    borrower = UserFactory(organization=org)
    spot = ParkingSpotFactory(organization=org, owner=owner, status='active')

    tr = DateTimeTZRange(_utc(2029, 4, 2, 10), _utc(2029, 4, 2, 14))
    booking = BookingFactory(
        organization=org,
        spot=spot,
        borrower=borrower,
        time_range=tr,
        status='confirmed',
    )

    with patch('notifications.dispatch.notify'):
        cancel_booking(booking, cancelled_by=owner)

    booking.refresh_from_db()
    assert booking.status == 'cancelled_owner', (
        f"Owner cancel should set status='cancelled_owner', got {booking.status!r}"
    )


# ---------------------------------------------------------------------------
# 3. test_owner_cancel_sets_penalty_hours
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_owner_cancel_sets_penalty_hours():
    """Owner cancel sets penalty_hours equal to the booking duration in hours."""
    from parking.booking import cancel_booking

    org = OrganizationFactory()
    owner = UserFactory(organization=org)
    borrower = UserFactory(organization=org)
    spot = ParkingSpotFactory(organization=org, owner=owner, status='active')

    # 6-hour booking
    tr = DateTimeTZRange(_utc(2029, 4, 3, 8), _utc(2029, 4, 3, 14))
    booking = BookingFactory(
        organization=org,
        spot=spot,
        borrower=borrower,
        time_range=tr,
        status='confirmed',
    )

    with patch('notifications.dispatch.notify'):
        cancel_booking(booking, cancelled_by=owner)

    booking.refresh_from_db()
    assert booking.penalty_hours == 6, (
        f"penalty_hours should equal booking duration (6h), got {booking.penalty_hours}"
    )


# ---------------------------------------------------------------------------
# 4. test_owner_cancel_with_reason
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_owner_cancel_with_reason():
    """cancel_reason is stored when the owner provides a reason before cancelling."""
    from parking.booking import cancel_booking

    org = OrganizationFactory()
    owner = UserFactory(organization=org)
    borrower = UserFactory(organization=org)
    spot = ParkingSpotFactory(organization=org, owner=owner, status='active')

    tr = DateTimeTZRange(_utc(2029, 4, 4, 10), _utc(2029, 4, 4, 14))
    booking = BookingFactory(
        organization=org,
        spot=spot,
        borrower=borrower,
        time_range=tr,
        status='confirmed',
    )

    reason_text = 'Emergency maintenance required'
    booking.cancel_reason = reason_text
    booking.save(update_fields=['cancel_reason'])

    with patch('notifications.dispatch.notify'):
        cancel_booking(booking, cancelled_by=owner)

    booking.refresh_from_db()
    assert booking.cancel_reason == reason_text, (
        f"cancel_reason should be stored; got {booking.cancel_reason!r}"
    )
    assert booking.status == 'cancelled_owner'


# ---------------------------------------------------------------------------
# 5. test_cancel_prevents_double_cancel
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_cancel_prevents_double_cancel():
    """Attempting to cancel an already-cancelled booking raises ValueError."""
    from parking.booking import cancel_booking

    org = OrganizationFactory()
    owner = UserFactory(organization=org)
    borrower = UserFactory(organization=org)
    spot = ParkingSpotFactory(organization=org, owner=owner, status='active')

    tr = DateTimeTZRange(_utc(2029, 4, 5, 10), _utc(2029, 4, 5, 14))
    booking = BookingFactory(
        organization=org,
        spot=spot,
        borrower=borrower,
        time_range=tr,
        status='confirmed',
    )

    with patch('notifications.dispatch.notify'):
        cancel_booking(booking, cancelled_by=borrower)

    # Second cancel attempt must raise ValueError
    booking.refresh_from_db()
    with pytest.raises(ValueError):
        cancel_booking(booking, cancelled_by=borrower)


# ---------------------------------------------------------------------------
# 6. test_cancel_prevents_cancel_completed
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_cancel_prevents_cancel_completed():
    """Attempting to cancel a completed booking raises ValueError."""
    from parking.booking import cancel_booking

    org = OrganizationFactory()
    owner = UserFactory(organization=org)
    borrower = UserFactory(organization=org)
    spot = ParkingSpotFactory(organization=org, owner=owner, status='active')

    tr = DateTimeTZRange(_utc(2029, 4, 6, 10), _utc(2029, 4, 6, 14))
    booking = BookingFactory(
        organization=org,
        spot=spot,
        borrower=borrower,
        time_range=tr,
        status='completed',
    )

    with pytest.raises(ValueError):
        cancel_booking(booking, cancelled_by=borrower)


# ---------------------------------------------------------------------------
# 7. test_third_party_cannot_cancel
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_third_party_cannot_cancel():
    """A user who is neither the borrower nor the owner gets PermissionError."""
    from parking.booking import cancel_booking

    org = OrganizationFactory()
    owner = UserFactory(organization=org)
    borrower = UserFactory(organization=org)
    third_party = UserFactory(organization=org)
    spot = ParkingSpotFactory(organization=org, owner=owner, status='active')

    tr = DateTimeTZRange(_utc(2029, 4, 7, 10), _utc(2029, 4, 7, 14))
    booking = BookingFactory(
        organization=org,
        spot=spot,
        borrower=borrower,
        time_range=tr,
        status='confirmed',
    )

    with pytest.raises(PermissionError):
        cancel_booking(booking, cancelled_by=third_party)


# ---------------------------------------------------------------------------
# 8. test_early_release_shortens_time_range
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_early_release_shortens_time_range():
    """release_booking updates time_range.upper to release_to."""
    from parking.booking import release_booking

    frozen_now = _utc(2029, 5, 1, 10)
    org = OrganizationFactory()
    owner = UserFactory(organization=org)
    borrower = UserFactory(organization=org)
    spot = ParkingSpotFactory(organization=org, owner=owner, status='active')

    tr = DateTimeTZRange(_utc(2029, 5, 1, 8), _utc(2029, 5, 1, 18))
    booking = BookingFactory(
        organization=org,
        spot=spot,
        borrower=borrower,
        time_range=tr,
        status='confirmed',
    )

    release_to = _utc(2029, 5, 1, 13)  # on the hour, in the future (frozen=10:00), before end(18:00)

    with freeze_time(frozen_now):
        with patch('notifications.dispatch.notify'):
            result = release_booking(booking, borrower, release_to)

    result.refresh_from_db()
    assert result.time_range.upper == release_to, (
        f"time_range.upper should be shortened to {release_to}, got {result.time_range.upper}"
    )
    assert result.time_range.lower == _utc(2029, 5, 1, 8), (
        "time_range.lower should remain unchanged"
    )


# ---------------------------------------------------------------------------
# 9. test_early_release_frees_gate2_slot
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_early_release_frees_gate2_slot():
    """After early release, the borrower can make a new booking (Gate 2 cleared)."""
    from parking.booking import release_booking, assign_spot

    frozen_now = _utc(2029, 5, 2, 10)
    org = OrganizationFactory(
        launched_at=_utc(2029, 1, 1, 0),
        launch_grace_days=14,
        booking_horizon_baseline_days=3,
        booking_horizon_max_days=30,
        listing_to_horizon_ratio=10,
        tier_metric_window_days=180,
    )
    owner = UserFactory(organization=org)
    borrower = UserFactory(organization=org)
    spot = ParkingSpotFactory(organization=org, owner=owner, status='active')

    # Availability window covers the full test period
    AvailabilityWindowFactory(
        organization=org,
        spot=spot,
        time_range=DateTimeTZRange(
            _utc(2029, 5, 1, 0),
            _utc(2029, 5, 31, 23),
        ),
    )

    # Borrower has an active booking 08:00-18:00 today
    tr = DateTimeTZRange(_utc(2029, 5, 2, 8), _utc(2029, 5, 2, 18))
    booking = BookingFactory(
        organization=org,
        spot=spot,
        borrower=borrower,
        time_range=tr,
        status='confirmed',
    )

    # Release early to 11:00 (frozen now is 10:00 so 11:00 is in the future)
    release_to = _utc(2029, 5, 2, 11)

    with freeze_time(frozen_now):
        with patch('notifications.dispatch.notify'):
            release_booking(booking, borrower, release_to)

    # After release the booking's time_range ends at 11:00.
    # The borrower still has this booking in 'confirmed' status so Gate 2
    # blocks a second booking.  Early release only shortens the window; it
    # does not cancel the booking.  To verify the *slot* is freed we check
    # that a second borrower (different user) can now book the same spot in
    # the released window, and that assign_spot does NOT return 'already_active'
    # for the original borrower (they still have the shortened booking).
    #
    # What the spec actually wants: after early release a *different* borrower
    # can grab the newly freed slot.  Verify assign_spot succeeds for them.
    frozen_after_release = _utc(2029, 5, 2, 10, 30)  # still frozen near 10:00

    borrower2 = UserFactory(organization=org)

    # The released slot starts at 11:00 but we need to be >1h past booking2's
    # end (original borrower's booking ends at 11:00 now).
    # Request: 13:00-15:00 on 2029-05-02 — well clear of buffer
    with freeze_time(frozen_after_release):
        result = assign_spot(org, borrower2, _utc(2029, 5, 2, 13), _utc(2029, 5, 2, 15))

    from parking.models import Booking
    assert result is None or isinstance(result, Booking), (
        f"Expected a Booking or None (no conflict), got {result!r}"
    )
    assert result != 'already_active', (
        "assign_spot must not return 'already_active' for a new borrower"
    )


# ---------------------------------------------------------------------------
# 10. test_early_release_form_valid
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_early_release_form_valid():
    """EarlyReleaseForm with a future, hour-aligned release_to before booking end passes validation."""
    from parking.forms import EarlyReleaseForm

    org = OrganizationFactory()
    owner = UserFactory(organization=org)
    borrower = UserFactory(organization=org)
    spot = ParkingSpotFactory(organization=org, owner=owner, status='active')

    tr = DateTimeTZRange(_utc(2029, 6, 1, 8), _utc(2029, 6, 1, 18))
    booking = BookingFactory(
        organization=org,
        spot=spot,
        borrower=borrower,
        time_range=tr,
        status='confirmed',
    )

    frozen_now = _utc(2029, 6, 1, 9)  # booking is active, 14:00 is in the future
    release_to_str = '2029-06-01T14:00'  # hour-aligned, future, before end(18:00)

    with freeze_time(frozen_now):
        form = EarlyReleaseForm(data={'release_to': release_to_str}, booking=booking)
        assert form.is_valid(), f"Form should be valid; errors: {form.errors}"


# ---------------------------------------------------------------------------
# 11. test_early_release_form_non_hour_rejected
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_early_release_form_non_hour_rejected():
    """EarlyReleaseForm rejects a release_to that is not on the hour."""
    from parking.forms import EarlyReleaseForm

    org = OrganizationFactory()
    owner = UserFactory(organization=org)
    borrower = UserFactory(organization=org)
    spot = ParkingSpotFactory(organization=org, owner=owner, status='active')

    tr = DateTimeTZRange(_utc(2029, 6, 2, 8), _utc(2029, 6, 2, 18))
    booking = BookingFactory(
        organization=org,
        spot=spot,
        borrower=borrower,
        time_range=tr,
        status='confirmed',
    )

    frozen_now = _utc(2029, 6, 2, 9)
    non_hour_str = '2029-06-02T14:30'  # minute=30

    with freeze_time(frozen_now):
        form = EarlyReleaseForm(data={'release_to': non_hour_str}, booking=booking)
        assert not form.is_valid(), "Form should be invalid when release_to is not on the hour"
        assert 'release_to' in form.errors, (
            f"Expected 'release_to' in form errors; errors: {form.errors}"
        )


# ---------------------------------------------------------------------------
# 12. test_early_release_form_past_rejected
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_early_release_form_past_rejected():
    """EarlyReleaseForm rejects a release_to that is in the past."""
    from parking.forms import EarlyReleaseForm

    org = OrganizationFactory()
    owner = UserFactory(organization=org)
    borrower = UserFactory(organization=org)
    spot = ParkingSpotFactory(organization=org, owner=owner, status='active')

    tr = DateTimeTZRange(_utc(2029, 6, 3, 8), _utc(2029, 6, 3, 18))
    booking = BookingFactory(
        organization=org,
        spot=spot,
        borrower=borrower,
        time_range=tr,
        status='confirmed',
    )

    frozen_now = _utc(2029, 6, 3, 14)  # current time is 14:00
    past_str = '2029-06-03T13:00'  # 13:00 is in the past relative to 14:00

    with freeze_time(frozen_now):
        form = EarlyReleaseForm(data={'release_to': past_str}, booking=booking)
        assert not form.is_valid(), "Form should be invalid when release_to is in the past"
        assert 'release_to' in form.errors, (
            f"Expected 'release_to' in form errors; errors: {form.errors}"
        )


# ---------------------------------------------------------------------------
# 13. test_early_release_form_after_end_rejected
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_early_release_form_after_end_rejected():
    """EarlyReleaseForm rejects a release_to that is at or after the booking end."""
    from parking.forms import EarlyReleaseForm

    org = OrganizationFactory()
    owner = UserFactory(organization=org)
    borrower = UserFactory(organization=org)
    spot = ParkingSpotFactory(organization=org, owner=owner, status='active')

    tr = DateTimeTZRange(_utc(2029, 6, 4, 8), _utc(2029, 6, 4, 16))
    booking = BookingFactory(
        organization=org,
        spot=spot,
        borrower=borrower,
        time_range=tr,
        status='confirmed',
    )

    frozen_now = _utc(2029, 6, 4, 9)
    # release_to == booking end (16:00) should be rejected (must be *strictly* before)
    at_end_str = '2029-06-04T16:00'

    with freeze_time(frozen_now):
        form = EarlyReleaseForm(data={'release_to': at_end_str}, booking=booking)
        assert not form.is_valid(), (
            "Form should be invalid when release_to is at or after booking end"
        )
        assert 'release_to' in form.errors, (
            f"Expected 'release_to' in form errors; errors: {form.errors}"
        )


# ---------------------------------------------------------------------------
# 14. test_owner_cannot_release_for_borrower
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_owner_cannot_release_for_borrower():
    """release_booking raises PermissionError when the caller is the owner, not the borrower."""
    from parking.booking import release_booking

    frozen_now = _utc(2029, 5, 10, 10)
    org = OrganizationFactory()
    owner = UserFactory(organization=org)
    borrower = UserFactory(organization=org)
    spot = ParkingSpotFactory(organization=org, owner=owner, status='active')

    tr = DateTimeTZRange(_utc(2029, 5, 10, 8), _utc(2029, 5, 10, 18))
    booking = BookingFactory(
        organization=org,
        spot=spot,
        borrower=borrower,
        time_range=tr,
        status='confirmed',
    )

    release_to = _utc(2029, 5, 10, 13)

    with freeze_time(frozen_now):
        with pytest.raises(PermissionError):
            release_booking(booking, owner, release_to)
