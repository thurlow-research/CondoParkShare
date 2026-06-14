"""
Unit tests for parking.leaderboard — get_leaderboard().

Covers all 9 executable lines of parking/leaderboard.py.
"""

from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

import factory
import pytest
from psycopg2.extras import DateTimeTZRange

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=dt_timezone.utc)


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------


class OrganizationFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "parking.Organization"

    name = factory.Sequence(lambda n: f"LeaderOrg {n}")
    hostname = factory.Sequence(lambda n: f"leaderorg{n}.parkshare.test")
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
    email = factory.Sequence(lambda n: f"leaderuser{n}@example.com")
    display_name = factory.Sequence(lambda n: f"Leader User {n}")
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
    time_range = DateTimeTZRange(
        _utc(2027, 1, 1, 0),
        _utc(2027, 1, 2, 0),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_leaderboard_returns_queryset():
    """get_leaderboard returns a non-empty queryset when active users exist."""
    from parking.leaderboard import get_leaderboard

    org = OrganizationFactory()
    UserFactory(organization=org, status="active")

    result = get_leaderboard(org, limit=20)
    # Just confirm the queryset is iterable and returns results
    users = list(result)
    assert len(users) >= 1, "Leaderboard should return at least one active user"


@pytest.mark.django_db
def test_leaderboard_empty_org():
    """get_leaderboard returns empty queryset for an org with no active users."""
    from parking.leaderboard import get_leaderboard

    org = OrganizationFactory()
    # No users created

    result = list(get_leaderboard(org, limit=20))
    assert (
        result == []
    ), f"Leaderboard should be empty for an org with no users, got {result}"


@pytest.mark.django_db
def test_leaderboard_orders_by_elapsed_hours_descending():
    """User with more elapsed listed hours appears higher in the leaderboard."""
    from freezegun import freeze_time

    from parking.leaderboard import get_leaderboard
    from parking.models import AvailabilityWindow

    frozen_now = _utc(2027, 6, 1, 12)
    with freeze_time(frozen_now):
        org = OrganizationFactory(tier_metric_window_days=180)

        owner_a = UserFactory(organization=org)
        owner_b = UserFactory(organization=org)

        spot_a = ParkingSpotFactory(organization=org, owner=owner_a, status="active")
        spot_b = ParkingSpotFactory(organization=org, owner=owner_b, status="active")

        # owner_a: 50 hours in the past (within the 180-day window)
        window_start_a = _utc(2027, 5, 1, 0)
        AvailabilityWindow.objects.create(
            organization=org,
            spot=spot_a,
            time_range=DateTimeTZRange(
                window_start_a, window_start_a + timedelta(hours=50)
            ),
        )

        # owner_b: 10 hours in the past (within the 180-day window)
        window_start_b = _utc(2027, 5, 5, 0)
        AvailabilityWindow.objects.create(
            organization=org,
            spot=spot_b,
            time_range=DateTimeTZRange(
                window_start_b, window_start_b + timedelta(hours=10)
            ),
        )

        result = list(get_leaderboard(org, limit=20))

    # owner_a has 50h, owner_b has 10h — owner_a should be first
    pks = [u.pk for u in result if u.pk in (owner_a.pk, owner_b.pk)]
    assert pks[0] == owner_a.pk, (
        f"owner_a (50h) should rank above owner_b (10h); "
        f"order was: {pks} (owner_a={owner_a.pk}, owner_b={owner_b.pk})"
    )


@pytest.mark.django_db
def test_leaderboard_excludes_future_windows():
    """Windows that have not yet ended do not count toward elapsed hours."""
    from freezegun import freeze_time

    from parking.leaderboard import get_leaderboard
    from parking.models import AvailabilityWindow

    frozen_now = _utc(2027, 6, 1, 12)
    with freeze_time(frozen_now):
        org = OrganizationFactory(tier_metric_window_days=180)

        owner = UserFactory(organization=org)
        spot = ParkingSpotFactory(organization=org, owner=owner, status="active")

        # Window ends in the future — should not count
        AvailabilityWindow.objects.create(
            organization=org,
            spot=spot,
            time_range=DateTimeTZRange(
                _utc(2027, 5, 25, 0),
                _utc(2027, 6, 10, 0),  # upper bound is after frozen_now
            ),
        )

        result = list(get_leaderboard(org, limit=20))

    # owner should appear but elapsed_hours should be None or zero
    assert len(result) >= 1
    owner_entry = next((u for u in result if u.pk == owner.pk), None)
    assert owner_entry is not None
    assert owner_entry.elapsed_hours is None or owner_entry.elapsed_hours == timedelta(
        0
    ), f"Future window must not count toward elapsed hours; got {owner_entry.elapsed_hours}"


@pytest.mark.django_db
def test_leaderboard_excludes_windows_outside_metric_window():
    """Windows that *end* before the rolling metric window are excluded.

    Exclusion is end-based (``endswith > window_start``), matching the
    authoritative horizon metric: a window whose upper bound predates the
    rolling window contributes nothing.
    """
    from freezegun import freeze_time

    from parking.leaderboard import get_leaderboard
    from parking.models import AvailabilityWindow

    frozen_now = _utc(2027, 6, 1, 12)
    with freeze_time(frozen_now):
        org = OrganizationFactory(tier_metric_window_days=180)

        owner = UserFactory(organization=org)
        spot = ParkingSpotFactory(organization=org, owner=owner, status="active")

        # Window entirely before the 180-day rolling window (ends at -200 days)
        old_end = frozen_now - timedelta(days=200)
        old_start = old_end - timedelta(hours=100)
        AvailabilityWindow.objects.create(
            organization=org,
            spot=spot,
            time_range=DateTimeTZRange(old_start, old_end),
        )

        result = list(get_leaderboard(org, limit=20))

    owner_entry = next((u for u in result if u.pk == owner.pk), None)
    assert owner_entry is not None
    assert owner_entry.elapsed_hours is None or owner_entry.elapsed_hours == timedelta(
        0
    ), f"Window outside metric window must not be counted; got {owner_entry.elapsed_hours}"


@pytest.mark.django_db
def test_leaderboard_clamps_window_straddling_metric_start():
    """A window that begins before window_start but elapses within it is clamped.

    Regression test for SPEC-1 §78 ("Leaderboard: same elapsed-listed-hours
    basis"): the leaderboard must use the same clamped basis as
    parking.horizon.get_earned_horizon_hours. A window straddling the rolling
    window start must contribute only its in-window portion — not be dropped
    (the prior bug) and not be counted in full.
    """
    from freezegun import freeze_time

    from parking.leaderboard import get_leaderboard
    from parking.models import AvailabilityWindow

    frozen_now = _utc(2027, 6, 1, 12)
    with freeze_time(frozen_now):
        org = OrganizationFactory(tier_metric_window_days=180)
        # window_start = frozen_now - 180 days = 2026-12-03 12:00
        window_start = frozen_now - timedelta(days=180)

        owner = UserFactory(organization=org)
        spot = ParkingSpotFactory(organization=org, owner=owner, status="active")

        # Window starts 10h BEFORE window_start and ends 40h AFTER it (fully past).
        # In-window (clamped) portion = 40h; the 10h before window_start is excluded.
        straddle_start = window_start - timedelta(hours=10)
        straddle_end = window_start + timedelta(hours=40)
        AvailabilityWindow.objects.create(
            organization=org,
            spot=spot,
            time_range=DateTimeTZRange(straddle_start, straddle_end),
        )

        result = list(get_leaderboard(org, limit=20))

    owner_entry = next((u for u in result if u.pk == owner.pk), None)
    assert owner_entry is not None
    assert owner_entry.elapsed_hours == timedelta(hours=40), (
        "Straddling window must be clamped to window_start: expected 40h "
        f"in-window portion, got {owner_entry.elapsed_hours}"
    )


@pytest.mark.django_db
def test_leaderboard_limit_honored():
    """get_leaderboard respects the limit parameter."""
    from parking.leaderboard import get_leaderboard

    org = OrganizationFactory()
    for _ in range(5):
        UserFactory(organization=org, status="active")

    result = list(get_leaderboard(org, limit=3))
    assert (
        len(result) <= 3
    ), f"Leaderboard should return at most 3 results; got {len(result)}"


@pytest.mark.django_db
def test_leaderboard_excludes_blocked_users():
    """Blocked users are not included in the leaderboard."""
    from parking.leaderboard import get_leaderboard

    org = OrganizationFactory()
    active_user = UserFactory(organization=org, status="active")
    blocked_user = UserFactory(organization=org, status="blocked")

    result = list(get_leaderboard(org, limit=20))

    pks = [u.pk for u in result]
    assert active_user.pk in pks, "Active user should appear in leaderboard"
    assert blocked_user.pk not in pks, "Blocked user must not appear in leaderboard"


@pytest.mark.django_db
def test_leaderboard_annotates_elapsed_hours():
    """Users in the result have the elapsed_hours annotation."""
    from freezegun import freeze_time

    from parking.leaderboard import get_leaderboard
    from parking.models import AvailabilityWindow

    frozen_now = _utc(2027, 6, 1, 12)
    with freeze_time(frozen_now):
        org = OrganizationFactory(tier_metric_window_days=180)

        owner = UserFactory(organization=org)
        spot = ParkingSpotFactory(organization=org, owner=owner, status="active")

        # 24 hours of elapsed listing time
        window_start = _utc(2027, 5, 1, 0)
        AvailabilityWindow.objects.create(
            organization=org,
            spot=spot,
            time_range=DateTimeTZRange(
                window_start, window_start + timedelta(hours=24)
            ),
        )

        result = list(get_leaderboard(org, limit=20))

    owner_entry = next((u for u in result if u.pk == owner.pk), None)
    assert owner_entry is not None, "Owner should appear in leaderboard"
    assert hasattr(
        owner_entry, "elapsed_hours"
    ), "User entry should have elapsed_hours annotation"
    assert owner_entry.elapsed_hours == timedelta(
        hours=24
    ), f"elapsed_hours should be timedelta(24h); got {owner_entry.elapsed_hours}"


@pytest.mark.django_db
def test_leaderboard_org_guard_excludes_cross_org_windows():
    """Org guard: hours from a spot belonging to a different org must not count.

    Bypasses the spot-owner-same-org invariant via QuerySet.update() to exercise
    the defense-in-depth Q(owned_spots__organization=organization) clause.
    Removing that clause lets the 24h window count for user_a; test asserts 0/None.
    """
    from freezegun import freeze_time

    from parking.leaderboard import get_leaderboard
    from parking.models import AvailabilityWindow, ParkingSpot

    frozen_now = _utc(2027, 6, 1, 12)
    with freeze_time(frozen_now):
        org_a = OrganizationFactory()
        org_b = OrganizationFactory()
        user_a = UserFactory(organization=org_a)

        # Create spot in org_a (satisfies FK), then reassign org to org_b via update()
        spot = ParkingSpotFactory(organization=org_a, owner=user_a, status="active")
        ParkingSpot.objects.filter(pk=spot.pk).update(organization=org_b)

        # Fully-elapsed 24h window in the metric window, on the now-org_b spot
        window_end = frozen_now - timedelta(hours=1)
        window_start_w = window_end - timedelta(hours=24)
        AvailabilityWindow.objects.create(
            organization=org_b,
            spot=spot,
            time_range=DateTimeTZRange(window_start_w, window_end),
        )

        result = list(get_leaderboard(org_a, limit=20))

    user_entry = next((u for u in result if u.pk == user_a.pk), None)
    assert user_entry is not None
    assert user_entry.elapsed_hours is None or user_entry.elapsed_hours == timedelta(
        0
    ), (
        "Hours from a spot belonging to org_b must not count toward org_a leaderboard; "
        f"got {user_entry.elapsed_hours}"
    )
