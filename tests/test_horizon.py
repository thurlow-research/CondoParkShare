"""
Unit tests for CondoParkShare Step 7 — Earned Horizon & notify_bookings.

Covers:
  Horizon calculation (1-10)
  notify_bookings management command (11-14)
"""

from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from math import floor
from unittest.mock import patch

import factory
import pytest
from freezegun import freeze_time
from psycopg2.extras import DateTimeTZRange

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc(year, month, day, hour, minute=0, second=0):
    """Return a timezone-aware UTC datetime."""
    return datetime(year, month, day, hour, minute, second, tzinfo=dt_timezone.utc)


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------


class OrganizationFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "parking.Organization"

    name = factory.Sequence(lambda n: f"HorizonOrg {n}")
    hostname = factory.Sequence(lambda n: f"horizonorg{n}.parkshare.test")
    support_email = factory.LazyAttribute(lambda o: f"support@{o.hostname}")
    registration_mode = "invite_only"
    timezone = "America/Los_Angeles"

    # Horizon defaults
    booking_horizon_baseline_days = 3  # 72 h baseline
    booking_horizon_max_days = 30  # 720 h max
    listing_to_horizon_ratio = 10  # 10 listed hours -> 1 earned horizon hour
    tier_metric_window_days = 180
    launch_grace_days = 14
    launch_grace_horizon_days = 14  # 336 h grace horizon

    # No launched_at by default (not launched)
    launched_at = None


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "accounts.User"

    organization = factory.SubFactory(OrganizationFactory)
    email = factory.Sequence(lambda n: f"horizonuser{n}@example.com")
    display_name = factory.Sequence(lambda n: f"Horizon User {n}")
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
    spot_number = factory.Sequence(lambda n: f"H{n:04d}")
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
        datetime(2028, 1, 1, 0, 0, tzinfo=dt_timezone.utc),
        datetime(2028, 12, 31, 23, 0, tzinfo=dt_timezone.utc),
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
        datetime(2028, 1, 10, 10, 0, tzinfo=dt_timezone.utc),
        datetime(2028, 1, 10, 14, 0, tzinfo=dt_timezone.utc),
    )
    status = "confirmed"
    penalty_hours = 0


# ---------------------------------------------------------------------------
# 1. test_horizon_no_listing_history
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_horizon_no_listing_history():
    """User with no availability windows gets baseline only (3 days = 72 h)."""
    from parking.horizon import get_earned_horizon_hours

    frozen_now = _utc(2027, 6, 1, 12)
    with freeze_time(frozen_now):
        org = OrganizationFactory(
            launched_at=_utc(2027, 1, 1, 0),  # 151 days ago; well past 14-day grace
            booking_horizon_baseline_days=3,
            booking_horizon_max_days=30,
            listing_to_horizon_ratio=10,
            tier_metric_window_days=180,
            launch_grace_days=14,
        )
        user = UserFactory(organization=org)
        hours = get_earned_horizon_hours(user)

    assert (
        hours == 72
    ), f"User with no listing history should get baseline 3*24=72h, got {hours}"


# ---------------------------------------------------------------------------
# 2. test_horizon_elapsed_hours_accumulate
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_horizon_elapsed_hours_accumulate():
    """Availability window fully in the past adds elapsed hours to the horizon."""
    from parking.horizon import get_earned_horizon_hours
    from parking.models import AvailabilityWindow

    frozen_now = _utc(2027, 6, 1, 12)
    with freeze_time(frozen_now):
        org = OrganizationFactory(
            launched_at=_utc(2027, 1, 1, 0),
            booking_horizon_baseline_days=3,
            booking_horizon_max_days=30,
            listing_to_horizon_ratio=10,
            tier_metric_window_days=180,
            launch_grace_days=14,
        )
        owner = UserFactory(organization=org)
        spot = ParkingSpotFactory(organization=org, owner=owner, status="active")

        # 100 hours fully in the past, within the 180-day window
        window_start = _utc(2027, 5, 1, 0)
        window_end = window_start + timedelta(hours=100)
        AvailabilityWindow.objects.create(
            organization=org,
            spot=spot,
            time_range=DateTimeTZRange(window_start, window_end),
        )

        hours = get_earned_horizon_hours(owner)

    # baseline=72, earned=floor(100/10)=10, total=82
    expected = 72 + floor(100 / 10)
    assert (
        hours == expected
    ), f"Expected {expected}h (baseline 72 + floor(100/10)=10), got {hours}"


# ---------------------------------------------------------------------------
# 3. test_horizon_future_hours_excluded
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_horizon_future_hours_excluded():
    """Availability window ending in the future does not contribute elapsed hours."""
    from parking.horizon import get_earned_horizon_hours
    from parking.models import AvailabilityWindow

    frozen_now = _utc(2027, 6, 1, 12)
    with freeze_time(frozen_now):
        org = OrganizationFactory(
            launched_at=_utc(2027, 1, 1, 0),
            booking_horizon_baseline_days=3,
            booking_horizon_max_days=30,
            listing_to_horizon_ratio=10,
            tier_metric_window_days=180,
            launch_grace_days=14,
        )
        owner = UserFactory(organization=org)
        spot = ParkingSpotFactory(organization=org, owner=owner, status="active")

        # Window starts in the past but ends in the future — not fully elapsed
        window_start = _utc(2027, 5, 20, 0)
        window_end = _utc(2027, 6, 10, 0)  # upper bound is after frozen_now
        AvailabilityWindow.objects.create(
            organization=org,
            spot=spot,
            time_range=DateTimeTZRange(window_start, window_end),
        )

        hours = get_earned_horizon_hours(owner)

    # No elapsed hours should be counted; user gets baseline only
    assert (
        hours == 72
    ), f"Window ending in the future must not contribute elapsed hours; expected 72h, got {hours}"


# ---------------------------------------------------------------------------
# 4. test_horizon_rolling_window
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_horizon_rolling_window():
    """Availability window starting before the 180-day rolling window is excluded."""
    from parking.horizon import get_earned_horizon_hours
    from parking.models import AvailabilityWindow

    # frozen_now = 2027-06-01 12:00
    # window_start = 180-day window = 2026-12-03 12:00
    # Put the window entirely before the rolling window boundary
    frozen_now = _utc(2027, 6, 1, 12)
    with freeze_time(frozen_now):
        org = OrganizationFactory(
            launched_at=_utc(2026, 1, 1, 0),  # launched 1.5 years ago; well past grace
            booking_horizon_baseline_days=3,
            booking_horizon_max_days=30,
            listing_to_horizon_ratio=10,
            tier_metric_window_days=180,
            launch_grace_days=14,
        )
        owner = UserFactory(organization=org)
        spot = ParkingSpotFactory(organization=org, owner=owner, status="active")

        # Window is 200 days before frozen_now — outside the 180-day metric window
        window_end = frozen_now - timedelta(days=185)
        window_start = window_end - timedelta(hours=100)
        AvailabilityWindow.objects.create(
            organization=org,
            spot=spot,
            time_range=DateTimeTZRange(window_start, window_end),
        )

        hours = get_earned_horizon_hours(owner)

    # Old window outside rolling window should be excluded; user gets baseline only
    assert (
        hours == 72
    ), f"Window outside the 180-day metric window must be excluded; expected 72h, got {hours}"


# ---------------------------------------------------------------------------
# 5. test_horizon_formula
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_horizon_formula():
    """Correct: baseline_hours + floor(elapsed/ratio), clamped to max."""
    from parking.horizon import get_earned_horizon_hours
    from parking.models import AvailabilityWindow

    frozen_now = _utc(2027, 6, 1, 12)
    with freeze_time(frozen_now):
        org = OrganizationFactory(
            launched_at=_utc(2027, 1, 1, 0),
            booking_horizon_baseline_days=2,  # 48 h baseline
            booking_horizon_max_days=10,  # 240 h max
            listing_to_horizon_ratio=7,  # unusual ratio for specificity
            tier_metric_window_days=180,
            launch_grace_days=14,
        )
        owner = UserFactory(organization=org)
        spot = ParkingSpotFactory(organization=org, owner=owner, status="active")

        # 77 hours of elapsed listing time
        elapsed_hours = 77
        window_start = _utc(2027, 5, 1, 0)
        window_end = window_start + timedelta(hours=elapsed_hours)
        AvailabilityWindow.objects.create(
            organization=org,
            spot=spot,
            time_range=DateTimeTZRange(window_start, window_end),
        )

        hours = get_earned_horizon_hours(owner)

    baseline = 2 * 24  # 48
    earned = floor(77 / 7)  # 11
    maximum = 10 * 24  # 240
    expected = min(baseline + earned, maximum)  # min(59, 240) = 59

    assert (
        hours == expected
    ), f"Expected formula result {expected}h (baseline={baseline} + floor(77/7)={earned}), got {hours}"


# ---------------------------------------------------------------------------
# 6. test_horizon_cold_start_grace_active
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_horizon_cold_start_grace_active():
    """Within launch_grace_days, all users get launch_grace_horizon_days * 24 hours."""
    from parking.horizon import get_earned_horizon_hours

    frozen_now = _utc(2027, 3, 10, 12)
    with freeze_time(frozen_now):
        # Launched 5 days ago; inside the 14-day grace window
        org = OrganizationFactory(
            launched_at=_utc(2027, 3, 5, 0),
            booking_horizon_baseline_days=3,
            booking_horizon_max_days=30,
            listing_to_horizon_ratio=10,
            tier_metric_window_days=180,
            launch_grace_days=14,
            launch_grace_horizon_days=14,
        )
        user = UserFactory(organization=org)
        hours = get_earned_horizon_hours(user)

    assert (
        hours == 14 * 24
    ), f"During grace window all users should get {14*24}h, got {hours}"


# ---------------------------------------------------------------------------
# 7. test_horizon_cold_start_grace_expired
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_horizon_cold_start_grace_expired():
    """After launch_grace_days have passed, grace no longer applies."""
    from parking.horizon import get_earned_horizon_hours

    frozen_now = _utc(2027, 3, 25, 12)
    with freeze_time(frozen_now):
        # Launched 20 days ago; outside the 14-day grace window
        org = OrganizationFactory(
            launched_at=_utc(2027, 3, 5, 0),
            booking_horizon_baseline_days=3,
            booking_horizon_max_days=30,
            listing_to_horizon_ratio=10,
            tier_metric_window_days=180,
            launch_grace_days=14,
            launch_grace_horizon_days=14,
        )
        user = UserFactory(organization=org)
        hours = get_earned_horizon_hours(user)

    # Grace no longer applies; user has no listing history, so gets baseline only
    assert (
        hours == 72
    ), f"After grace window expires, user should fall back to baseline 72h, got {hours}"
    # Explicitly confirm it's NOT the grace value
    assert (
        hours != 14 * 24
    ), "Grace value should NOT be returned after grace window expires"


# ---------------------------------------------------------------------------
# 8. test_horizon_cold_start_no_launched_at
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_horizon_cold_start_no_launched_at():
    """Org with launched_at=None gets no grace — not yet launched."""
    from parking.horizon import get_earned_horizon_hours

    frozen_now = _utc(2027, 3, 10, 12)
    with freeze_time(frozen_now):
        org = OrganizationFactory(
            launched_at=None,  # not launched
            booking_horizon_baseline_days=3,
            booking_horizon_max_days=30,
            listing_to_horizon_ratio=10,
            tier_metric_window_days=180,
            launch_grace_days=14,
            launch_grace_horizon_days=14,
        )
        user = UserFactory(organization=org)
        hours = get_earned_horizon_hours(user)

    # Should get baseline only, not the grace value
    assert (
        hours == 72
    ), f"Org with launched_at=None should get baseline 72h (no grace), got {hours}"
    assert hours != 14 * 24, "Grace value must not be returned when launched_at is None"


# ---------------------------------------------------------------------------
# 9. test_horizon_penalty_deduction
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_horizon_penalty_deduction():
    """Owner-cancelled booking reduces elapsed hours via penalty_hours."""
    from parking.horizon import get_earned_horizon_hours
    from parking.models import AvailabilityWindow

    frozen_now = _utc(2027, 6, 1, 12)
    with freeze_time(frozen_now):
        org = OrganizationFactory(
            launched_at=_utc(2027, 1, 1, 0),
            booking_horizon_baseline_days=3,
            booking_horizon_max_days=30,
            listing_to_horizon_ratio=10,
            tier_metric_window_days=180,
            launch_grace_days=14,
        )
        owner = UserFactory(organization=org)
        spot = ParkingSpotFactory(organization=org, owner=owner, status="active")

        # 100 hours of listing history -> earned = floor(100/10) = 10 -> horizon = 82h without penalty
        window_start = _utc(2027, 5, 1, 0)
        window_end = window_start + timedelta(hours=100)
        AvailabilityWindow.objects.create(
            organization=org,
            spot=spot,
            time_range=DateTimeTZRange(window_start, window_end),
        )

        # Owner-cancel penalty of 40 hours — net = 100 - 40 = 60
        # earned = floor(60/10) = 6 -> horizon = 72 + 6 = 78h
        penalty_start = _utc(2027, 5, 20, 0)
        penalty_end = penalty_start + timedelta(hours=8)
        BookingFactory(
            organization=org,
            spot=spot,
            borrower=UserFactory(organization=org),
            time_range=DateTimeTZRange(penalty_start, penalty_end),
            status="cancelled_owner",
            penalty_hours=40,
        )

        hours = get_earned_horizon_hours(owner)

    # net_hours = max(0, 100 - 40) = 60; earned = floor(60/10) = 6; total = 72 + 6 = 78
    expected = 72 + floor(60 / 10)
    assert (
        hours == expected
    ), f"After 40h penalty on 100h listing, expected {expected}h, got {hours}"


# ---------------------------------------------------------------------------
# 10. test_horizon_penalty_uses_time_range_not_updated_at
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_horizon_penalty_uses_time_range_not_updated_at():
    """Penalty is filtered by booking time_range.startswith, not updated_at/cancellation time."""
    from parking.horizon import get_earned_horizon_hours
    from parking.models import AvailabilityWindow

    frozen_now = _utc(2027, 6, 1, 12)
    with freeze_time(frozen_now):
        org = OrganizationFactory(
            launched_at=_utc(2026, 1, 1, 0),  # launched long ago; well past grace
            booking_horizon_baseline_days=3,
            booking_horizon_max_days=30,
            listing_to_horizon_ratio=10,
            tier_metric_window_days=180,
            launch_grace_days=14,
        )
        owner = UserFactory(organization=org)
        spot = ParkingSpotFactory(organization=org, owner=owner, status="active")

        # Add listing history within the window
        window_start = _utc(2027, 5, 1, 0)
        window_end = window_start + timedelta(hours=100)
        AvailabilityWindow.objects.create(
            organization=org,
            spot=spot,
            time_range=DateTimeTZRange(window_start, window_end),
        )

        # Penalty booking whose time_range starts BEFORE the 180-day rolling window
        # (should be excluded from penalty count even though it was cancelled recently)
        # Rolling window start = frozen_now - 180 days = 2026-12-03 12:00
        before_window = frozen_now - timedelta(days=185)  # outside the rolling window
        old_penalty_end = before_window + timedelta(hours=50)
        BookingFactory(
            organization=org,
            spot=spot,
            borrower=UserFactory(organization=org),
            time_range=DateTimeTZRange(before_window, old_penalty_end),
            status="cancelled_owner",
            penalty_hours=50,
        )

        hours = get_earned_horizon_hours(owner)

    # The old penalty booking's time_range.start is outside the rolling window,
    # so it should be excluded. User gets full listing benefit.
    # elapsed=100, penalties=0, earned=floor(100/10)=10, total=82
    expected = 72 + 10
    assert (
        hours == expected
    ), f"Old penalty booking outside rolling window must be excluded; expected {expected}h, got {hours}"


# ---------------------------------------------------------------------------
# Management command tests — notify_bookings
# ---------------------------------------------------------------------------


def _run_command(*events):
    """Run the notify_bookings management command with the given events."""
    from io import StringIO

    from django.core.management import call_command

    out = StringIO()
    call_command("notify_bookings", event=",".join(events), verbosity=2, stdout=out)
    return out.getvalue()


# ---------------------------------------------------------------------------
# 11. test_notify_bookings_starts_transitions_confirmed_to_active
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_notify_bookings_starts_transitions_confirmed_to_active():
    """Booking confirmed within the last hour has its status transitioned to active."""
    frozen_now = _utc(2027, 7, 15, 10, 0)
    with freeze_time(frozen_now):
        org = OrganizationFactory()
        owner = UserFactory(organization=org)
        borrower = UserFactory(organization=org)
        spot = ParkingSpotFactory(organization=org, owner=owner, status="active")

        # Booking whose start was 30 minutes ago — confirmed, should be activated
        booking_start = frozen_now - timedelta(minutes=30)
        booking_end = frozen_now + timedelta(hours=4)
        booking = BookingFactory(
            organization=org,
            spot=spot,
            borrower=borrower,
            time_range=DateTimeTZRange(booking_start, booking_end),
            status="confirmed",
        )

        with patch("parking.management.commands.notify_bookings.notify") as mock_notify:
            _run_command("starts")

    booking.refresh_from_db()
    assert (
        booking.status == "active"
    ), f"Confirmed booking with start in last hour should be 'active', got {booking.status!r}"
    # Check that the booking_starts notification was fired
    events_fired = [c.args[0] for c in mock_notify.call_args_list]
    assert (
        "booking_starts" in events_fired
    ), f"booking_starts notification should have been sent; events fired: {events_fired}"


# ---------------------------------------------------------------------------
# 12. test_notify_bookings_completions_updates_last_booking_at
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_notify_bookings_completions_updates_last_booking_at():
    """Completed booking updates owner.last_booking_at to the booking end time."""
    frozen_now = _utc(2027, 7, 16, 14, 0)
    with freeze_time(frozen_now):
        org = OrganizationFactory()
        owner = UserFactory(organization=org, last_booking_at=None)
        borrower = UserFactory(organization=org)
        spot = ParkingSpotFactory(organization=org, owner=owner, status="active")

        # Active booking that ended 30 minutes ago
        booking_start = frozen_now - timedelta(hours=4, minutes=30)
        booking_end = frozen_now - timedelta(minutes=30)
        booking = BookingFactory(
            organization=org,
            spot=spot,
            borrower=borrower,
            time_range=DateTimeTZRange(booking_start, booking_end),
            status="active",
        )

        with patch("parking.management.commands.notify_bookings.notify"):
            _run_command("completions")

    booking.refresh_from_db()
    assert (
        booking.status == "completed"
    ), f"Active booking with end in last hour should be 'completed', got {booking.status!r}"

    owner.refresh_from_db()
    assert (
        owner.last_booking_at == booking_end
    ), f"owner.last_booking_at should be set to booking end {booking_end}, got {owner.last_booking_at}"


# ---------------------------------------------------------------------------
# 13. test_notify_bookings_cleans_expired_tentative
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_notify_bookings_cleans_expired_tentative():
    """Expired tentative booking is set to cancelled_admin by tentative_cleanup."""
    frozen_now = _utc(2027, 7, 17, 10, 0)
    with freeze_time(frozen_now):
        org = OrganizationFactory()
        owner = UserFactory(organization=org)
        borrower = UserFactory(organization=org)
        spot = ParkingSpotFactory(organization=org, owner=owner, status="active")

        # Tentative booking that expired 10 minutes ago
        booking_start = frozen_now + timedelta(hours=2)
        booking_end = frozen_now + timedelta(hours=6)
        expired_at = frozen_now - timedelta(minutes=10)
        booking = BookingFactory(
            organization=org,
            spot=spot,
            borrower=borrower,
            time_range=DateTimeTZRange(booking_start, booking_end),
            status="tentative",
            tentative_expires_at=expired_at,
        )

        _run_command("tentative_cleanup")

    booking.refresh_from_db()
    assert (
        booking.status == "cancelled_admin"
    ), f"Expired tentative booking should be 'cancelled_admin', got {booking.status!r}"


# ---------------------------------------------------------------------------
# 14. test_warning_30_fires_for_booking_ending_soon
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_warning_30_fires_for_booking_ending_soon():
    """Active booking ending in ~30 minutes triggers a warning_30 notification."""
    frozen_now = _utc(2027, 7, 18, 10, 0)
    with freeze_time(frozen_now):
        org = OrganizationFactory()
        owner = UserFactory(organization=org)
        borrower = UserFactory(organization=org)
        spot = ParkingSpotFactory(organization=org, owner=owner, status="active")

        # Active booking ending in exactly 30 minutes (within the ±5-min window)
        booking_start = frozen_now - timedelta(hours=3)
        booking_end = frozen_now + timedelta(minutes=30)
        booking = BookingFactory(
            organization=org,
            spot=spot,
            borrower=borrower,
            time_range=DateTimeTZRange(booking_start, booking_end),
            status="active",
        )

        with patch("parking.management.commands.notify_bookings.notify") as mock_notify:
            _run_command("warning_30")

    # The warning_30 event should have been dispatched for our booking
    assert (
        mock_notify.called
    ), "notify() should have been called for the warning_30 event"
    events_and_bookings = [(c.args[0], c.args[1]) for c in mock_notify.call_args_list]
    matching = [
        (evt, b)
        for evt, b in events_and_bookings
        if evt == "warning_30" and b.pk == booking.pk
    ]
    assert matching, (
        f"warning_30 notification should have been fired for booking {booking.pk}; "
        f"got calls: {events_and_bookings}"
    )
