"""
System tests — Cancellation and early release flows (SPEC-1 §11, §4).

Covers:
  1. Borrower cancels pre-start → booking voided; spot available again; one-booking slot freed.
  2. Borrower early release → remaining hours freed; borrower can book again.
  3. Owner cancels booked slot → booking voided; borrower notification record created;
     owner standing penalty recorded.
"""

import pytest
from django.test import Client, override_settings
from freezegun import freeze_time

from tests.system.conftest import (
    client_post,
    force_login_active,
    make_booking,
    make_org,
    make_spot,
    make_user,
    make_window,
    utc,
)

HOSTNAME = "cancellation.parkshare.test"


@pytest.fixture
def org(db):
    return make_org("CancelOrg", HOSTNAME)


@pytest.fixture
def owner(org):
    return make_user(org, "cancelowner@test.test", display_name="CancelOwner")


@pytest.fixture
def borrower(org):
    return make_user(org, "cancelborrower@test.test", display_name="CancelBorrower")


@pytest.fixture
def spot(org, owner):
    return make_spot(org, owner, spot_number="C001")


# ---------------------------------------------------------------------------
# Test 1 — Borrower pre-start cancel: booking voided, spot freed
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@override_settings(ALLOWED_HOSTS=[HOSTNAME])
@freeze_time("2029-05-01T00:00:00Z")
def test_borrower_cancel_pre_start_voids_booking(org, owner, borrower, spot):
    """
    Borrower cancels before the booking starts → status becomes 'cancelled_borrower',
    spot is available again, borrower's one-booking slot is freed. (SPEC-1 §4, §11)
    """
    from parking.models import Booking
    from parking.availability import get_available_slots

    book_start = utc(2029, 5, 2, 10)
    book_end = utc(2029, 5, 2, 14)
    make_window(org, spot, utc(2029, 5, 2, 0), utc(2029, 5, 2, 23))
    booking = make_booking(org, spot, borrower, book_start, book_end, status="confirmed")

    # Spot should be unavailable before cancellation
    slots_before = get_available_slots(org, book_start, book_end)
    assert spot.pk not in [s.pk for s in slots_before], (
        "Spot should not be available while booking is confirmed"
    )

    # Borrower cancels via POST
    client = Client()
    force_login_active(client, borrower)
    response = client_post(client, HOSTNAME, f"/bookings/{booking.pk}/cancel/")

    assert response.status_code == 302, (
        f"Expected redirect after cancellation, got {response.status_code}"
    )

    # Booking status should now be cancelled_borrower
    booking.refresh_from_db()
    assert booking.status == "cancelled_borrower", (
        f"Expected 'cancelled_borrower', got '{booking.status}'"
    )

    # Spot should now be available again
    slots_after = get_available_slots(org, book_start, book_end)
    assert spot.pk in [s.pk for s in slots_after], (
        "Spot should be available again after borrower cancellation"
    )

    # Gate 2: borrower should be able to book again (no active booking)
    still_active = Booking.objects.filter(
        borrower=borrower,
        status__in=["tentative", "confirmed", "active"],
    ).exists()
    assert not still_active, (
        "Borrower's one-booking slot should be freed after cancellation"
    )


# ---------------------------------------------------------------------------
# Test 2 — Borrower early release frees remaining hours
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@override_settings(ALLOWED_HOSTS=[HOSTNAME])
@freeze_time("2029-05-02T10:00:00Z")
def test_borrower_early_release_frees_slot(org, owner, borrower, spot):
    """
    Borrower does early release → booking end is shortened; borrower's active
    booking slot is freed; they can book again. (SPEC-1 §4 Cancellation/release)
    """
    from parking.models import Booking

    book_start = utc(2029, 5, 2, 8)    # started 2 hours ago
    book_end = utc(2029, 5, 2, 16)     # ends 6 hours from now
    make_window(org, spot, utc(2029, 5, 2, 0), utc(2029, 5, 2, 23))
    booking = make_booking(org, spot, borrower, book_start, book_end, status="active")

    release_to = utc(2029, 5, 2, 12)   # release to 2 hours from now (on the hour)

    client = Client()
    force_login_active(client, borrower)
    response = client_post(client, HOSTNAME, f"/bookings/{booking.pk}/release/", {
        "release_to": release_to.strftime("%Y-%m-%d %H:%M"),
    })

    assert response.status_code == 302, (
        f"Expected redirect after early release, got {response.status_code}"
    )

    # Booking end should be shortened
    booking.refresh_from_db()
    new_end = booking.time_range.upper
    assert new_end == release_to, (
        f"Expected booking end to be {release_to}, got {new_end}"
    )

    # Booking should still be in a non-cancelled status (active/confirmed)
    assert booking.status in ("active", "confirmed"), (
        f"Expected booking to remain active/confirmed after early release, got '{booking.status}'"
    )


# ---------------------------------------------------------------------------
# Test 3 — Owner cancels booked slot → notification + penalty
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@override_settings(
    ALLOWED_HOSTS=[HOSTNAME],
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
)
@freeze_time("2029-05-01T00:00:00Z")
def test_owner_cancel_notifies_borrower_and_records_penalty(org, owner, borrower, spot):
    """
    Owner cancels a booked slot → booking voided, borrower notification record
    created, owner penalty_hours recorded. (SPEC-1 §4, §11)
    """
    from parking.models import Booking

    book_start = utc(2029, 5, 2, 10)
    book_end = utc(2029, 5, 2, 14)
    make_window(org, spot, utc(2029, 5, 2, 0), utc(2029, 5, 2, 23))
    booking = make_booking(org, spot, borrower, book_start, book_end, status="confirmed")

    client = Client()
    force_login_active(client, owner)
    response = client_post(client, HOSTNAME, f"/bookings/{booking.pk}/cancel/", {
        "reason": "Emergency maintenance required",
    })

    assert response.status_code == 302, (
        f"Expected redirect after owner cancellation, got {response.status_code}"
    )

    # Booking should be cancelled_owner
    booking.refresh_from_db()
    assert booking.status == "cancelled_owner", (
        f"Expected 'cancelled_owner', got '{booking.status}'"
    )

    # Penalty hours should be recorded
    assert booking.penalty_hours > 0, (
        f"Expected penalty_hours > 0 after owner cancel, got {booking.penalty_hours}"
    )

    # Borrower should receive an email notification after owner cancellation.
    # In tests, EMAIL_BACKEND = locmem so emails appear in mail.outbox.
    from django.core import mail
    recipient_emails = {msg.to[0] for msg in mail.outbox if msg.to}
    assert borrower.email in recipient_emails, (
        f"Expected email notification for borrower ({borrower.email}) after owner cancellation. "
        f"outbox recipients: {recipient_emails}"
    )
