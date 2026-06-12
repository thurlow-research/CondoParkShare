"""
Unit tests for CondoParkShare Step 6 — booking gates.

Covers:
  Gate 1: Horizon (1-6)
  Gate 2: One active booking per resident (7-10)
  Gate 3: DB overlap + buffer (11-14)
  assign_spot owner-rotation (15-17)
  Cancellation and release (18-22)
  confirm_booking (23-24)
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

    name = factory.Sequence(lambda n: f'GateOrg {n}')
    hostname = factory.Sequence(lambda n: f'gateorg{n}.parkshare.test')
    support_email = factory.LazyAttribute(lambda o: f'support@{o.hostname}')
    registration_mode = 'invite_only'
    timezone = 'America/Los_Angeles'

    # Horizon defaults — keep them explicit so tests can override easily
    booking_horizon_baseline_days = 3       # 72 h baseline
    booking_horizon_max_days = 30           # 720 h max
    listing_to_horizon_ratio = 10           # 10 listed hours -> 1 earned horizon hour
    tier_metric_window_days = 180
    launch_grace_days = 14
    launch_grace_horizon_days = 14          # 336 h grace horizon


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = 'accounts.User'

    organization = factory.SubFactory(OrganizationFactory)
    email = factory.Sequence(lambda n: f'gateuser{n}@example.com')
    display_name = factory.Sequence(lambda n: f'Gate User {n}')
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
    spot_number = factory.Sequence(lambda n: f'G{n:04d}')
    status = 'active'


class AvailabilityWindowFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = 'parking.AvailabilityWindow'

    organization = factory.SubFactory(OrganizationFactory)
    spot = factory.SubFactory(
        ParkingSpotFactory,
        organization=factory.SelfAttribute('..organization'),
    )
    # Default window far in the future so it doesn't interfere with horizon tests
    time_range = DateTimeTZRange(
        datetime(2028, 1, 1, 0, 0, tzinfo=dt_timezone.utc),
        datetime(2028, 12, 31, 23, 0, tzinfo=dt_timezone.utc),
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
        datetime(2028, 1, 10, 10, 0, tzinfo=dt_timezone.utc),
        datetime(2028, 1, 10, 14, 0, tzinfo=dt_timezone.utc),
    )
    status = 'confirmed'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utc(year, month, day, hour, minute=0, second=0):
    """Return a timezone-aware UTC datetime."""
    return datetime(year, month, day, hour, minute, second, tzinfo=dt_timezone.utc)


# ---------------------------------------------------------------------------
# Gate 1 — Horizon
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_gate1_baseline_horizon():
    """New user with no listing history gets baseline_days*24 hours horizon."""
    from parking.horizon import get_earned_horizon_hours

    # Freeze well past any launch grace window
    frozen_now = _utc(2027, 6, 1, 12)
    with freeze_time(frozen_now):
        org = OrganizationFactory(
            launched_at=_utc(2027, 1, 1, 0),   # launched 151 days ago; past grace
            booking_horizon_baseline_days=3,
            booking_horizon_max_days=30,
            listing_to_horizon_ratio=10,
            tier_metric_window_days=180,
            launch_grace_days=14,
        )
        user = UserFactory(organization=org)
        hours = get_earned_horizon_hours(user)

    assert hours == 72, (
        f"New user should get baseline 3*24=72h horizon, got {hours}"
    )


@pytest.mark.django_db
def test_gate1_cold_start_grace():
    """During launch_grace_days, all users get launch_grace_horizon_days * 24 h."""
    from parking.horizon import get_earned_horizon_hours

    frozen_now = _utc(2027, 3, 10, 12)
    with freeze_time(frozen_now):
        # launched 5 days ago — still inside 14-day grace window
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

    assert hours == 14 * 24, (
        f"During grace window all users should get {14*24}h, got {hours}"
    )


@pytest.mark.django_db
def test_gate1_earned_horizon():
    """User with elapsed listed hours gets correct horizon (baseline + floor(elapsed/ratio))."""
    from parking.horizon import get_earned_horizon_hours
    from parking.models import AvailabilityWindow

    frozen_now = _utc(2027, 6, 1, 12)
    with freeze_time(frozen_now):
        org = OrganizationFactory(
            launched_at=_utc(2027, 1, 1, 0),   # 151 days ago; past grace
            booking_horizon_baseline_days=3,
            booking_horizon_max_days=30,
            listing_to_horizon_ratio=10,
            tier_metric_window_days=180,
            launch_grace_days=14,
        )
        owner = UserFactory(organization=org)
        spot = ParkingSpotFactory(organization=org, owner=owner, status='active')

        # Create a completed availability window with 50 elapsed hours
        # The window must be fully in the past and within the 180-day metric window.
        # frozen_now = 2027-06-01 12:00 UTC
        # window: 2027-05-01 08:00 -> 2027-05-03 10:00 = 50 hours elapsed
        window_lower = _utc(2027, 5, 1, 8)
        window_upper = _utc(2027, 5, 3, 10)   # 50 hours
        AvailabilityWindow.objects.create(
            organization=org,
            spot=spot,
            time_range=DateTimeTZRange(window_lower, window_upper),
        )

        hours = get_earned_horizon_hours(owner)

    # baseline=72, earned=floor(50/10)=5, total=77
    expected = 72 + 5
    assert hours == expected, (
        f"Expected horizon of {expected}h (72 baseline + 5 earned), got {hours}"
    )


@pytest.mark.django_db
def test_gate1_clamped_to_max():
    """Horizon never exceeds booking_horizon_max_days * 24 hours."""
    from parking.horizon import get_earned_horizon_hours
    from parking.models import AvailabilityWindow

    frozen_now = _utc(2027, 6, 1, 12)
    with freeze_time(frozen_now):
        org = OrganizationFactory(
            launched_at=_utc(2027, 1, 1, 0),
            booking_horizon_baseline_days=3,
            booking_horizon_max_days=30,       # max = 720 h
            listing_to_horizon_ratio=1,        # ratio=1 so 1 listed hour = 1 earned horizon hour
            tier_metric_window_days=180,
            launch_grace_days=14,
        )
        owner = UserFactory(organization=org)
        spot = ParkingSpotFactory(organization=org, owner=owner, status='active')

        # 900 hours of listed time — baseline(72) + earned(900) = 972 > max(720)
        window_lower = _utc(2027, 4, 1, 0)
        window_upper = _utc(2027, 5, 16, 12)  # 45.5 days = 1092 hours > 900
        # Use exactly 900 hours to be explicit
        window_upper = window_lower + timedelta(hours=900)
        AvailabilityWindow.objects.create(
            organization=org,
            spot=spot,
            time_range=DateTimeTZRange(window_lower, window_upper),
        )

        hours = get_earned_horizon_hours(owner)

    assert hours == 720, (
        f"Horizon should be clamped to max 720h, got {hours}"
    )


@pytest.mark.django_db
def test_gate1_rejects_beyond_horizon():
    """Booking starting beyond earned horizon is rejected by check_horizon_gate."""
    from parking.horizon import check_horizon_gate

    frozen_now = _utc(2027, 6, 1, 12)
    with freeze_time(frozen_now):
        org = OrganizationFactory(
            launched_at=_utc(2027, 1, 1, 0),
            booking_horizon_baseline_days=3,    # 72h horizon
            booking_horizon_max_days=30,
            listing_to_horizon_ratio=10,
            tier_metric_window_days=180,
            launch_grace_days=14,
        )
        borrower = UserFactory(organization=org)

        # Request start is 73 hours from now — beyond the 72h baseline horizon
        beyond_horizon = frozen_now + timedelta(hours=73)
        result = check_horizon_gate(borrower, beyond_horizon)

    assert result is False, (
        "check_horizon_gate must return False when requested start exceeds horizon"
    )


@pytest.mark.django_db
def test_gate1_penalty_reduces_horizon():
    """Owner-cancelled booking applies penalty, reducing earned horizon."""
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
        spot = ParkingSpotFactory(organization=org, owner=owner, status='active')

        # 100 hours of listing history -> earned = floor(100/10) = 10 -> horizon = 82h
        window_lower = _utc(2027, 5, 1, 0)
        window_upper = window_lower + timedelta(hours=100)
        AvailabilityWindow.objects.create(
            organization=org,
            spot=spot,
            time_range=DateTimeTZRange(window_lower, window_upper),
        )

        # Add a penalty booking of 50 hours — net listed = 100 - 50 = 50h
        # earned = floor(50/10) = 5 -> horizon = 77h
        penalty_start = _utc(2027, 5, 20, 0)
        penalty_end = penalty_start + timedelta(hours=50)
        BookingFactory(
            organization=org,
            spot=spot,
            borrower=owner,
            time_range=DateTimeTZRange(penalty_start, penalty_end),
            status='cancelled_owner',
            penalty_hours=50,
        )

        hours = get_earned_horizon_hours(owner)

    assert hours == 77, (
        f"After 50h penalty, horizon should be 77h (baseline 72 + floor(50/10)=5), got {hours}"
    )


# ---------------------------------------------------------------------------
# Gate 2 — One active booking per resident
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_gate2_blocks_second_booking():
    """Resident with active booking cannot create another — assign_spot returns 'already_active'."""
    from parking.booking import assign_spot

    frozen_now = _utc(2027, 7, 1, 0)
    with freeze_time(frozen_now):
        org = OrganizationFactory(
            launched_at=_utc(2027, 6, 1, 0),
            launch_grace_days=14,
        )
        owner = UserFactory(organization=org)
        borrower = UserFactory(organization=org)
        spot = ParkingSpotFactory(organization=org, owner=owner, status='active')

        # Window covers the full test period
        AvailabilityWindowFactory(
            organization=org,
            spot=spot,
            time_range=DateTimeTZRange(
                _utc(2027, 7, 1, 0),
                _utc(2027, 7, 31, 23),
            ),
        )

        # Borrower already has an active booking
        BookingFactory(
            organization=org,
            spot=spot,
            borrower=borrower,
            time_range=DateTimeTZRange(_utc(2027, 7, 5, 10), _utc(2027, 7, 5, 14)),
            status='confirmed',
        )

        # Try to book a non-overlapping slot
        req_start = _utc(2027, 7, 10, 10)
        req_end = _utc(2027, 7, 10, 14)
        result = assign_spot(org, borrower, req_start, req_end)

    assert result == 'already_active', (
        f"assign_spot must return 'already_active' when borrower has an active booking; got {result!r}"
    )


@pytest.mark.django_db
def test_gate2_tentative_counts():
    """Tentative hold counts as active for Gate 2 — assign_spot returns 'already_active'."""
    from parking.booking import assign_spot

    frozen_now = _utc(2027, 7, 2, 0)
    with freeze_time(frozen_now):
        org = OrganizationFactory(
            launched_at=_utc(2027, 6, 1, 0),
            launch_grace_days=14,
        )
        owner = UserFactory(organization=org)
        borrower = UserFactory(organization=org)
        spot = ParkingSpotFactory(organization=org, owner=owner, status='active')

        AvailabilityWindowFactory(
            organization=org,
            spot=spot,
            time_range=DateTimeTZRange(
                _utc(2027, 7, 1, 0),
                _utc(2027, 7, 31, 23),
            ),
        )

        # Borrower has a tentative booking
        BookingFactory(
            organization=org,
            spot=spot,
            borrower=borrower,
            time_range=DateTimeTZRange(_utc(2027, 7, 5, 10), _utc(2027, 7, 5, 14)),
            status='tentative',
        )

        req_start = _utc(2027, 7, 10, 10)
        req_end = _utc(2027, 7, 10, 14)
        result = assign_spot(org, borrower, req_start, req_end)

    assert result == 'already_active', (
        f"A tentative booking must count as active for Gate 2; got {result!r}"
    )


@pytest.mark.django_db
def test_gate2_completed_allows_new():
    """Completed booking does not block a new booking."""
    from parking.booking import assign_spot

    frozen_now = _utc(2027, 7, 3, 0)
    with freeze_time(frozen_now):
        org = OrganizationFactory(
            launched_at=_utc(2027, 6, 1, 0),
            launch_grace_days=14,
        )
        owner = UserFactory(organization=org)
        borrower = UserFactory(organization=org)
        spot = ParkingSpotFactory(organization=org, owner=owner, status='active')

        AvailabilityWindowFactory(
            organization=org,
            spot=spot,
            time_range=DateTimeTZRange(
                _utc(2027, 7, 1, 0),
                _utc(2027, 7, 31, 23),
            ),
        )

        # Borrower has only a completed booking
        BookingFactory(
            organization=org,
            spot=spot,
            borrower=borrower,
            time_range=DateTimeTZRange(_utc(2027, 7, 1, 10), _utc(2027, 7, 1, 14)),
            status='completed',
        )

        # Request a new non-overlapping slot well outside any buffer
        req_start = _utc(2027, 7, 10, 10)
        req_end = _utc(2027, 7, 10, 14)
        result = assign_spot(org, borrower, req_start, req_end)

    assert result != 'already_active', (
        "Completed booking must not block a new booking"
    )
    # Should have gotten a Booking object or None (no spots); not the sentinel
    from parking.models import Booking
    assert result is None or isinstance(result, Booking), (
        f"Expected a Booking or None, got {result!r}"
    )


@pytest.mark.django_db
def test_gate2_cancelled_allows_new():
    """Cancelled booking does not block a new booking."""
    from parking.booking import assign_spot

    frozen_now = _utc(2027, 7, 4, 0)
    with freeze_time(frozen_now):
        org = OrganizationFactory(
            launched_at=_utc(2027, 6, 1, 0),
            launch_grace_days=14,
        )
        owner = UserFactory(organization=org)
        borrower = UserFactory(organization=org)
        spot = ParkingSpotFactory(organization=org, owner=owner, status='active')

        AvailabilityWindowFactory(
            organization=org,
            spot=spot,
            time_range=DateTimeTZRange(
                _utc(2027, 7, 1, 0),
                _utc(2027, 7, 31, 23),
            ),
        )

        # Borrower has only a cancelled booking
        BookingFactory(
            organization=org,
            spot=spot,
            borrower=borrower,
            time_range=DateTimeTZRange(_utc(2027, 7, 1, 10), _utc(2027, 7, 1, 14)),
            status='cancelled_borrower',
        )

        req_start = _utc(2027, 7, 10, 10)
        req_end = _utc(2027, 7, 10, 14)
        result = assign_spot(org, borrower, req_start, req_end)

    assert result != 'already_active', (
        "Cancelled booking must not block a new booking"
    )


# ---------------------------------------------------------------------------
# Gate 3 — DB overlap + buffer
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_gate3_overlap_rejected_by_db():
    """Two bookings for the same spot and overlapping time raise IntegrityError."""
    from django.db import IntegrityError

    org = OrganizationFactory()
    spot = ParkingSpotFactory(organization=org)

    tr1 = DateTimeTZRange(_utc(2028, 2, 1, 10), _utc(2028, 2, 1, 14))
    tr2 = DateTimeTZRange(_utc(2028, 2, 1, 12), _utc(2028, 2, 1, 16))

    BookingFactory(organization=org, spot=spot, time_range=tr1, status='confirmed')

    with pytest.raises(IntegrityError):
        BookingFactory(organization=org, spot=spot, time_range=tr2, status='confirmed')


@pytest.mark.django_db
def test_gate3_buffer_before_blocks():
    """Booking within 1h of another booking's end is rejected by assign_spot."""
    from parking.booking import assign_spot

    frozen_now = _utc(2027, 8, 1, 0)
    with freeze_time(frozen_now):
        org = OrganizationFactory(
            launched_at=_utc(2027, 6, 1, 0),
            launch_grace_days=14,
        )
        owner = UserFactory(organization=org)
        borrower = UserFactory(organization=org)
        spot = ParkingSpotFactory(organization=org, owner=owner, status='active')

        AvailabilityWindowFactory(
            organization=org,
            spot=spot,
            time_range=DateTimeTZRange(
                _utc(2027, 8, 1, 0),
                _utc(2027, 8, 31, 23),
            ),
        )

        # Existing booking ends at 14:00
        BookingFactory(
            organization=org,
            spot=spot,
            borrower=UserFactory(organization=org),
            time_range=DateTimeTZRange(_utc(2027, 8, 10, 10), _utc(2027, 8, 10, 14)),
            status='confirmed',
        )

        # New request starts at 14:30 — within the 1h buffer
        result = assign_spot(org, borrower, _utc(2027, 8, 10, 14, 30), _utc(2027, 8, 10, 17))

    assert result is None, (
        "assign_spot must return None when new booking starts within 1h of existing booking's end"
    )


@pytest.mark.django_db
def test_gate3_buffer_after_blocks():
    """Booking within 1h of another booking's start is rejected by assign_spot."""
    from parking.booking import assign_spot

    frozen_now = _utc(2027, 8, 2, 0)
    with freeze_time(frozen_now):
        org = OrganizationFactory(
            launched_at=_utc(2027, 6, 1, 0),
            launch_grace_days=14,
        )
        owner = UserFactory(organization=org)
        borrower = UserFactory(organization=org)
        spot = ParkingSpotFactory(organization=org, owner=owner, status='active')

        AvailabilityWindowFactory(
            organization=org,
            spot=spot,
            time_range=DateTimeTZRange(
                _utc(2027, 8, 1, 0),
                _utc(2027, 8, 31, 23),
            ),
        )

        # Existing booking starts at 16:00
        BookingFactory(
            organization=org,
            spot=spot,
            borrower=UserFactory(organization=org),
            time_range=DateTimeTZRange(_utc(2027, 8, 10, 16), _utc(2027, 8, 10, 18)),
            status='confirmed',
        )

        # New request ends at 15:30 — within the 1h buffer before 16:00
        result = assign_spot(org, borrower, _utc(2027, 8, 10, 13), _utc(2027, 8, 10, 15, 30))

    assert result is None, (
        "assign_spot must return None when new booking ends within 1h of existing booking's start"
    )


@pytest.mark.django_db
def test_gate3_outside_buffer_succeeds():
    """Booking more than 1h after previous booking's end succeeds."""
    from parking.booking import assign_spot
    from parking.models import Booking

    frozen_now = _utc(2027, 8, 3, 0)
    with freeze_time(frozen_now):
        org = OrganizationFactory(
            launched_at=_utc(2027, 6, 1, 0),
            launch_grace_days=14,
        )
        owner = UserFactory(organization=org)
        borrower = UserFactory(organization=org)
        spot = ParkingSpotFactory(organization=org, owner=owner, status='active')

        AvailabilityWindowFactory(
            organization=org,
            spot=spot,
            time_range=DateTimeTZRange(
                _utc(2027, 8, 1, 0),
                _utc(2027, 8, 31, 23),
            ),
        )

        # Existing booking ends at 12:00
        BookingFactory(
            organization=org,
            spot=spot,
            borrower=UserFactory(organization=org),
            time_range=DateTimeTZRange(_utc(2027, 8, 10, 10), _utc(2027, 8, 10, 12)),
            status='confirmed',
        )

        # New request starts at 13:01 — just outside the 1h buffer
        result = assign_spot(org, borrower, _utc(2027, 8, 10, 13, 1), _utc(2027, 8, 10, 15))

    assert isinstance(result, Booking), (
        f"assign_spot must succeed (return a Booking) when new booking starts >1h after existing end; got {result!r}"
    )
    assert result.status == 'tentative'


# ---------------------------------------------------------------------------
# assign_spot — owner rotation
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_assign_spot_rotation():
    """Owner A last booked today; owner B never booked. Owner B's spot is assigned first."""
    from parking.booking import assign_spot
    from parking.models import Booking

    frozen_now = _utc(2027, 9, 1, 12)
    with freeze_time(frozen_now):
        org = OrganizationFactory(
            launched_at=_utc(2027, 6, 1, 0),
            launch_grace_days=14,
        )

        owner_a = UserFactory(organization=org, last_booking_at=frozen_now)   # booked today
        owner_b = UserFactory(organization=org, last_booking_at=None)          # never booked

        spot_a = ParkingSpotFactory(organization=org, owner=owner_a, status='active')
        spot_b = ParkingSpotFactory(organization=org, owner=owner_b, status='active')

        window_range = DateTimeTZRange(
            _utc(2027, 9, 1, 0),
            _utc(2027, 9, 30, 23),
        )
        AvailabilityWindowFactory(organization=org, spot=spot_a, time_range=window_range)
        AvailabilityWindowFactory(organization=org, spot=spot_b, time_range=window_range)

        borrower = UserFactory(organization=org)
        req_start = _utc(2027, 9, 10, 10)
        req_end = _utc(2027, 9, 10, 14)

        result = assign_spot(org, borrower, req_start, req_end)

    assert isinstance(result, Booking), f"Expected Booking, got {result!r}"
    assert result.spot == spot_b, (
        f"Owner B has never booked (nulls_first) so spot_b should be assigned; "
        f"got spot {result.spot.pk} (spot_a={spot_a.pk}, spot_b={spot_b.pk})"
    )


@pytest.mark.django_db
def test_assign_spot_returns_none_no_availability():
    """When there are no available spots, assign_spot returns None."""
    from parking.booking import assign_spot

    frozen_now = _utc(2027, 9, 2, 0)
    with freeze_time(frozen_now):
        org = OrganizationFactory(
            launched_at=_utc(2027, 6, 1, 0),
            launch_grace_days=14,
        )
        borrower = UserFactory(organization=org)
        # No spots or windows created
        result = assign_spot(org, borrower, _utc(2027, 9, 10, 10), _utc(2027, 9, 10, 14))

    assert result is None, (
        f"assign_spot must return None when no spots are available, got {result!r}"
    )


@pytest.mark.django_db
def test_assign_spot_tentative_status():
    """Assigned spot has status='tentative' and tentative_expires_at set."""
    from parking.booking import assign_spot
    from parking.models import Booking

    frozen_now = _utc(2027, 9, 3, 12)
    with freeze_time(frozen_now):
        org = OrganizationFactory(
            launched_at=_utc(2027, 6, 1, 0),
            launch_grace_days=14,
        )
        owner = UserFactory(organization=org)
        borrower = UserFactory(organization=org)
        spot = ParkingSpotFactory(organization=org, owner=owner, status='active')

        AvailabilityWindowFactory(
            organization=org,
            spot=spot,
            time_range=DateTimeTZRange(
                _utc(2027, 9, 1, 0),
                _utc(2027, 9, 30, 23),
            ),
        )

        result = assign_spot(org, borrower, _utc(2027, 9, 10, 10), _utc(2027, 9, 10, 14))

    assert isinstance(result, Booking), f"Expected Booking, got {result!r}"
    assert result.status == 'tentative', (
        f"Newly assigned booking must have status='tentative', got {result.status!r}"
    )
    assert result.tentative_expires_at is not None, (
        "tentative_expires_at must be set on a freshly assigned booking"
    )
    # Should expire ~5 minutes from frozen_now
    expected_expiry = frozen_now + timedelta(minutes=5)
    delta = abs((result.tentative_expires_at - expected_expiry).total_seconds())
    assert delta < 5, (
        f"tentative_expires_at should be ~5 minutes from now; delta={delta}s"
    )


# ---------------------------------------------------------------------------
# Cancellation and release
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_owner_cancel_sets_penalty_hours():
    """Owner cancel sets penalty_hours equal to the booking duration in hours."""
    from parking.booking import cancel_booking

    org = OrganizationFactory()
    owner = UserFactory(organization=org)
    borrower = UserFactory(organization=org)
    spot = ParkingSpotFactory(organization=org, owner=owner, status='active')

    tr = DateTimeTZRange(_utc(2028, 3, 1, 10), _utc(2028, 3, 1, 14))  # 4 hours
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
        f"Status should be 'cancelled_owner', got {booking.status!r}"
    )
    assert booking.penalty_hours == 4, (
        f"penalty_hours should be 4 (booking duration), got {booking.penalty_hours}"
    )


@pytest.mark.django_db
def test_borrower_cancel_no_penalty():
    """Borrower cancel does not set penalty_hours."""
    from parking.booking import cancel_booking

    org = OrganizationFactory()
    owner = UserFactory(organization=org)
    borrower = UserFactory(organization=org)
    spot = ParkingSpotFactory(organization=org, owner=owner, status='active')

    tr = DateTimeTZRange(_utc(2028, 3, 2, 10), _utc(2028, 3, 2, 14))
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
        f"Status should be 'cancelled_borrower', got {booking.status!r}"
    )
    assert booking.penalty_hours == 0, (
        f"Borrower cancel must not set penalty_hours; got {booking.penalty_hours}"
    )


@pytest.mark.django_db
def test_early_release_shortens_booking():
    """release_booking shortens booking.time_range.upper to release_up_to."""
    from parking.booking import release_booking

    frozen_now = _utc(2028, 4, 1, 10)
    org = OrganizationFactory()
    owner = UserFactory(organization=org)
    borrower = UserFactory(organization=org)
    spot = ParkingSpotFactory(organization=org, owner=owner, status='active')

    tr = DateTimeTZRange(_utc(2028, 4, 1, 8), _utc(2028, 4, 1, 16))
    booking = BookingFactory(
        organization=org,
        spot=spot,
        borrower=borrower,
        time_range=tr,
        status='confirmed',
    )

    release_to = _utc(2028, 4, 1, 12)  # on the hour, in the future, before end

    with freeze_time(frozen_now):
        with patch('notifications.dispatch.notify'):
            result = release_booking(booking, borrower, release_to)

    result.refresh_from_db()
    assert result.time_range.upper == release_to, (
        f"Booking end should be shortened to {release_to}, got {result.time_range.upper}"
    )
    assert result.time_range.lower == _utc(2028, 4, 1, 8), (
        "Booking start should remain unchanged"
    )


@pytest.mark.django_db
def test_early_release_must_be_on_hour():
    """release_booking raises ValueError if release_up_to is not on the hour."""
    from parking.booking import release_booking

    frozen_now = _utc(2028, 4, 2, 10)
    org = OrganizationFactory()
    owner = UserFactory(organization=org)
    borrower = UserFactory(organization=org)
    spot = ParkingSpotFactory(organization=org, owner=owner, status='active')

    tr = DateTimeTZRange(_utc(2028, 4, 2, 8), _utc(2028, 4, 2, 16))
    booking = BookingFactory(
        organization=org,
        spot=spot,
        borrower=borrower,
        time_range=tr,
        status='confirmed',
    )

    non_hour_aligned = _utc(2028, 4, 2, 12, 30)  # minute=30

    with freeze_time(frozen_now):
        with pytest.raises(ValueError, match='hour'):
            release_booking(booking, borrower, non_hour_aligned)


@pytest.mark.django_db
def test_early_release_must_be_future():
    """release_booking raises ValueError if release_up_to is in the past."""
    from parking.booking import release_booking

    frozen_now = _utc(2028, 4, 3, 14)
    org = OrganizationFactory()
    owner = UserFactory(organization=org)
    borrower = UserFactory(organization=org)
    spot = ParkingSpotFactory(organization=org, owner=owner, status='active')

    tr = DateTimeTZRange(_utc(2028, 4, 3, 8), _utc(2028, 4, 3, 18))
    booking = BookingFactory(
        organization=org,
        spot=spot,
        borrower=borrower,
        time_range=tr,
        status='confirmed',
    )

    past_time = _utc(2028, 4, 3, 13)  # 1 hour in the past (frozen_now=14:00)

    with freeze_time(frozen_now):
        with pytest.raises(ValueError, match='future'):
            release_booking(booking, borrower, past_time)


# ---------------------------------------------------------------------------
# confirm_booking
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_confirm_unexpired_tentative():
    """confirm_booking changes a valid tentative booking's status to 'confirmed'."""
    from parking.booking import confirm_booking

    frozen_now = _utc(2028, 5, 1, 10)
    org = OrganizationFactory()
    owner = UserFactory(organization=org)
    borrower = UserFactory(organization=org)
    spot = ParkingSpotFactory(organization=org, owner=owner, status='active')

    tr = DateTimeTZRange(_utc(2028, 5, 10, 10), _utc(2028, 5, 10, 14))
    booking = BookingFactory(
        organization=org,
        spot=spot,
        borrower=borrower,
        time_range=tr,
        status='tentative',
        tentative_expires_at=frozen_now + timedelta(minutes=5),
    )

    with freeze_time(frozen_now):
        result = confirm_booking(booking, borrower)

    result.refresh_from_db()
    assert result.status == 'confirmed', (
        f"confirm_booking should set status='confirmed', got {result.status!r}"
    )


@pytest.mark.django_db
def test_confirm_expired_tentative():
    """Expired tentative raises ValueError and sets status to cancelled_admin."""
    from parking.booking import confirm_booking

    frozen_now = _utc(2028, 5, 2, 10)
    org = OrganizationFactory()
    owner = UserFactory(organization=org)
    borrower = UserFactory(organization=org)
    spot = ParkingSpotFactory(organization=org, owner=owner, status='active')

    tr = DateTimeTZRange(_utc(2028, 5, 10, 10), _utc(2028, 5, 10, 14))
    # Tentative expired 10 minutes ago
    booking = BookingFactory(
        organization=org,
        spot=spot,
        borrower=borrower,
        time_range=tr,
        status='tentative',
        tentative_expires_at=frozen_now - timedelta(minutes=10),
    )

    with freeze_time(frozen_now):
        with pytest.raises(ValueError, match='[Ee]xpired|expired'):
            confirm_booking(booking, borrower)

    booking.refresh_from_db()
    assert booking.status == 'cancelled_admin', (
        f"Expired tentative should be set to 'cancelled_admin', got {booking.status!r}"
    )
