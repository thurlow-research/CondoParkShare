"""
System tests — Listing flow (SPEC-1 §11).

Covers:
  1. Spot list page shows owner's spots.
  2. Elapsed listed hours accumulate as time passes (freezegun).
  3. Future listed hours do not accumulate yet.
"""

import pytest
from django.test import Client, override_settings
from freezegun import freeze_time

from tests.system.conftest import (
    client_get,
    force_login_active,
    make_org,
    make_spot,
    make_user,
    make_window,
    utc,
)

HOSTNAME = "listingflow.parkshare.test"


@pytest.fixture
def org(db):
    return make_org("ListingOrg", HOSTNAME, launched_at=utc(2029, 1, 1))


@pytest.fixture
def owner(org):
    return make_user(org, "owner@listingflow.test", display_name="OwnerUser")


@pytest.fixture
def spot(org, owner):
    return make_spot(org, owner, spot_number="L001")


# ---------------------------------------------------------------------------
# Test 1 — Spot list page shows owner's spots
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@override_settings(ALLOWED_HOSTS=[HOSTNAME])
@freeze_time("2029-05-01T00:00:00Z")
def test_spot_list_page_shows_owner_spots(org, owner, spot):
    """
    Owner can access /spots/ and sees their own spot. (SPEC-1 §11 Listing)
    """
    client = Client()
    force_login_active(client, owner)
    response = client_get(client, HOSTNAME, "/spots/")
    assert response.status_code == 200, (
        f"Expected 200 for spot list page, got {response.status_code}"
    )
    content = response.content.decode()
    assert spot.spot_number in content, (
        f"Expected spot number {spot.spot_number} in spot list page"
    )


# ---------------------------------------------------------------------------
# Test 2 — Elapsed listed hours accumulate as time passes → raises horizon
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@freeze_time("2029-05-10T00:00:00Z")
def test_elapsed_listed_hours_accumulate_to_raise_horizon(org, owner, spot):
    """
    Hours from past availability windows count toward earned horizon.
    With ratio=10, 10 past hours → +1 bonus hour above baseline.
    (SPEC-1 §4 Alignment incentive — counts only elapsed listed hours)
    """
    from parking.horizon import get_earned_horizon_hours

    # Baseline (no listing) = 3 days = 72 hours
    baseline = get_earned_horizon_hours(owner)
    assert baseline == 72, f"Expected baseline 72h, got {baseline}"

    # Add a 10-hour past window (enough for +1 bonus hour at ratio=10)
    make_window(org, spot, utc(2029, 5, 5, 0), utc(2029, 5, 5, 10))  # 10 hours past

    elevated = get_earned_horizon_hours(owner)
    # baseline (72) + floor(10/10) * 1 = 73 hours
    assert elevated > baseline, (
        f"Expected horizon to increase after elapsed listing; got {elevated} <= {baseline}"
    )
    assert elevated == 73, f"Expected 73h horizon after 10 elapsed hours, got {elevated}"


# ---------------------------------------------------------------------------
# Test 3 — Future listed hours do not accumulate yet
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@freeze_time("2029-05-01T00:00:00Z")
def test_future_listed_hours_do_not_accumulate(org, owner, spot):
    """
    Future availability windows contribute 0 elapsed hours → horizon stays at baseline.
    (SPEC-1 §4 — prevents list-then-bail gaming)
    """
    from parking.horizon import get_earned_horizon_hours

    # Baseline
    baseline = get_earned_horizon_hours(owner)

    # Window entirely in the future
    make_window(org, spot, utc(2029, 5, 10, 0), utc(2029, 5, 10, 20))  # 20 future hours

    future_horizon = get_earned_horizon_hours(owner)
    assert future_horizon == baseline, (
        f"Expected horizon {baseline}h unchanged with future window; got {future_horizon}"
    )
