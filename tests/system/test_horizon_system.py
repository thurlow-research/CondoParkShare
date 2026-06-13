"""
System tests — Earned-horizon advancement (SPEC-1 §4, §10).

Covers:
  1. New resident gets baseline horizon (3 days default = 72h).
  2. During cold-start grace period, resident gets launch_grace_horizon_days regardless of listing.
  3. Resident with sufficient elapsed listed hours gets elevated horizon (formula check).
"""

import pytest
from freezegun import freeze_time

from tests.system.conftest import (
    make_org,
    make_spot,
    make_user,
    make_window,
    utc,
)


@pytest.fixture
def org_no_grace(db):
    """Organization that has been live for 30 days — grace period has passed."""
    return make_org(
        "HorizonOrgNoGrace",
        "horizon-nograce.parkshare.test",
        booking_horizon_baseline_days=3,
        booking_horizon_max_days=30,
        listing_to_horizon_ratio=10,
        launch_grace_days=14,
        launch_grace_horizon_days=14,
        launched_at=utc(2029, 4, 1),  # 30 days before May 1
    )


@pytest.fixture
def org_in_grace(db):
    """Organization launched 3 days ago — still in cold-start grace."""
    return make_org(
        "HorizonOrgInGrace",
        "horizon-grace.parkshare.test",
        booking_horizon_baseline_days=3,
        booking_horizon_max_days=30,
        listing_to_horizon_ratio=10,
        launch_grace_days=14,
        launch_grace_horizon_days=14,
        launched_at=utc(2029, 4, 28),  # 3 days before May 1
    )


# ---------------------------------------------------------------------------
# Test 1 — New resident gets baseline horizon
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@freeze_time("2029-05-01T00:00:00Z")
def test_new_resident_gets_baseline_horizon(org_no_grace):
    """
    New resident with no listing history gets baseline_days * 24 hours.
    (SPEC-1 §4, §10: baseline 3 days = 72 hours)
    """
    from parking.horizon import get_earned_horizon_hours

    resident = make_user(org_no_grace, "newresident@horizon.test")
    earned = get_earned_horizon_hours(resident)

    expected = org_no_grace.booking_horizon_baseline_days * 24  # 72
    assert earned == expected, (
        f"Expected baseline horizon {expected}h, got {earned}h"
    )


# ---------------------------------------------------------------------------
# Test 2 — Cold-start grace period gives launch_grace_horizon_days
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@freeze_time("2029-05-01T00:00:00Z")
def test_cold_start_grace_gives_grace_horizon(org_in_grace):
    """
    During launch_grace_days window, all residents get launch_grace_horizon_days
    regardless of listing history. (SPEC-1 §4 Cold-start grace)
    """
    from parking.horizon import get_earned_horizon_hours

    resident = make_user(org_in_grace, "graceresident@horizon.test")
    earned = get_earned_horizon_hours(resident)

    grace_hours = org_in_grace.launch_grace_horizon_days * 24  # 14 * 24 = 336
    assert earned == grace_hours, (
        f"Expected grace horizon {grace_hours}h during cold-start, got {earned}h"
    )


# ---------------------------------------------------------------------------
# Test 3 — Sufficient elapsed listed hours elevate horizon
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@freeze_time("2029-05-01T00:00:00Z")
def test_elapsed_listed_hours_elevate_horizon(org_no_grace):
    """
    Formula: horizon = baseline + floor(elapsed / ratio), capped at max.
    With baseline=3d=72h, ratio=10, 40 elapsed hours → floor(40/10)=4 bonus hours → 76h.
    (SPEC-1 §4 Alignment incentive)
    """
    from parking.horizon import get_earned_horizon_hours

    owner = make_user(org_no_grace, "listing_owner@horizon.test")
    spot = make_spot(org_no_grace, owner, "H001")

    # 40 hours of past windows (4 × 10h, all fully elapsed before 2029-05-01)
    make_window(org_no_grace, spot, utc(2029, 4, 20, 0), utc(2029, 4, 20, 10))  # 10h
    make_window(org_no_grace, spot, utc(2029, 4, 21, 0), utc(2029, 4, 21, 10))  # 10h
    make_window(org_no_grace, spot, utc(2029, 4, 22, 0), utc(2029, 4, 22, 10))  # 10h
    make_window(org_no_grace, spot, utc(2029, 4, 23, 0), utc(2029, 4, 23, 10))  # 10h

    earned = get_earned_horizon_hours(owner)

    baseline = org_no_grace.booking_horizon_baseline_days * 24  # 72
    bonus = 40 // org_no_grace.listing_to_horizon_ratio  # floor(40/10) = 4
    expected = baseline + bonus  # 76

    assert earned == expected, (
        f"Expected {expected}h horizon after 40 elapsed listed hours, got {earned}h"
    )


# ---------------------------------------------------------------------------
# Test 4 — Horizon is capped at booking_horizon_max_days
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@freeze_time("2029-05-01T00:00:00Z")
def test_horizon_capped_at_maximum():
    """
    Horizon is capped at booking_horizon_max_days * 24 hours even with excess listings.
    (SPEC-1 §4 clamped to booking_horizon_max_days)

    To trigger the cap we need: baseline + floor(elapsed/ratio) > max_days * 24
    With baseline=3d=72h, ratio=10, max=30d=720h:
      Need floor(elapsed/10) > 720 - 72 = 648 → elapsed > 6480 hours.
    We use a custom org with max=5d=120h so a smaller data set reaches the cap:
      ratio=10, baseline=3d=72h → cap=5d*24=120h; need floor(elapsed/10) >= 120-72=48 → elapsed>=480h.
    """
    from parking.horizon import get_earned_horizon_hours

    # Custom org with a smaller max so we can trigger the cap easily
    org_small_max = make_org(
        "HorizonCapOrg",
        "horizon-cap.parkshare.test",
        booking_horizon_baseline_days=3,
        booking_horizon_max_days=5,     # small cap = 120h
        listing_to_horizon_ratio=10,
        launch_grace_days=0,            # no grace
        launch_grace_horizon_days=14,
        launched_at=utc(2028, 1, 1),    # well in the past
        tier_metric_window_days=180,
    )

    owner = make_user(org_small_max, "heavy_lister@horizon.test")
    spot = make_spot(org_small_max, owner, "H002")

    # 500 elapsed hours within the 180-day window (all windows in Nov-Apr range)
    # Use 5 × 100-hour windows, each within last 180 days
    # 180 days before 2029-05-01 = 2028-11-02
    make_window(org_small_max, spot, utc(2028, 11, 5, 0), utc(2028, 11, 9, 4))    # 100h
    make_window(org_small_max, spot, utc(2028, 11, 10, 0), utc(2028, 11, 14, 4))  # 100h
    make_window(org_small_max, spot, utc(2028, 11, 15, 0), utc(2028, 11, 19, 4))  # 100h
    make_window(org_small_max, spot, utc(2028, 11, 20, 0), utc(2028, 11, 24, 4))  # 100h
    make_window(org_small_max, spot, utc(2028, 11, 25, 0), utc(2028, 11, 29, 4))  # 100h

    earned = get_earned_horizon_hours(owner)
    maximum = org_small_max.booking_horizon_max_days * 24  # 120

    assert earned <= maximum, (
        f"Expected horizon to be capped at {maximum}h, got {earned}h"
    )
    assert earned == maximum, (
        f"Expected horizon to reach the cap of {maximum}h with excess listings, got {earned}h"
    )
