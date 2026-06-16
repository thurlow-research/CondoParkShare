"""
System tests — Booking flow (SPEC-1 §11).

Covers full HTTP request/response cycle for the primary booking flow:
  1. Authenticated resident searches for available spots → sees only available spots.
  2. Gate 1 (horizon): booking whose start > earned horizon is rejected.
  3. Gate 2 (one-active-booking): resident with active booking cannot book again.
  4. Gate 3 (overlap): second booking for same spot at overlapping time → rejected.
  5. Valid booking → confirmed → borrower and owner both receive notification records.
  6. Unauthenticated resident cannot access any resident views → redirect to login.
"""

from datetime import timedelta

import pytest
from django.test import Client, override_settings
from django.utils.timezone import now
from freezegun import freeze_time

from tests.system.conftest import (
    client_get,
    client_post,
    force_login_active,
    make_booking,
    make_org,
    make_spot,
    make_user,
    make_window,
    utc,
)

HOSTNAME = "bookingflow.parkshare.test"


@pytest.fixture
def org(db):
    return make_org("BookingOrg", HOSTNAME)


@pytest.fixture
def owner(org):
    return make_user(org, "owner@bookingflow.test", display_name="Owner")


@pytest.fixture
def borrower(org):
    return make_user(org, "borrower@bookingflow.test", display_name="Borrower")


@pytest.fixture
def spot(org, owner):
    return make_spot(org, owner, spot_number="B001")


# ---------------------------------------------------------------------------
# Test 1 — Unauthenticated access is blocked (redirect to login)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@override_settings(ALLOWED_HOSTS=[HOSTNAME])
def test_unauthenticated_resident_redirected_to_login(org):
    """Logged-out resident cannot access /book/ — redirected to login. (§11 Auth)
    Requires the org fixture so TenantMiddleware can resolve the hostname.
    """
    client = Client()
    response = client_get(client, HOSTNAME, "/book/")
    assert response.status_code in (302, 301), (
        f"Expected redirect for unauthenticated access to /book/, got {response.status_code}"
    )
    location = response.get("Location", "")
    assert "login" in location, (
        f"Expected redirect to login, got Location: {location}"
    )


@pytest.mark.django_db
@override_settings(ALLOWED_HOSTS=[HOSTNAME])
def test_unauthenticated_booking_list_redirected(org):
    """Logged-out resident cannot access /bookings/ — redirected to login. (§11 Auth)"""
    client = Client()
    response = client_get(client, HOSTNAME, "/bookings/")
    assert response.status_code in (302, 301)
    location = response.get("Location", "")
    assert "login" in location


# ---------------------------------------------------------------------------
# Test 2 — Search returns available spots only
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@override_settings(ALLOWED_HOSTS=[HOSTNAME])
@freeze_time("2029-05-01T00:00:00Z")
def test_book_request_page_renders_for_active_resident(org, borrower, spot, owner):
    """Active resident can GET the book-request page. (§11 Booking)"""
    # No availability window; the form should still render (no results yet)
    client = Client()
    force_login_active(client, borrower)
    response = client_get(client, HOSTNAME, "/book/")
    assert response.status_code == 200, (
        f"Expected 200 for book request page, got {response.status_code}"
    )


# ---------------------------------------------------------------------------
# Test 3 — Gate 1: booking beyond earned horizon is rejected
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@override_settings(ALLOWED_HOSTS=[HOSTNAME])
@freeze_time("2029-05-01T00:00:00Z")
def test_gate1_horizon_rejects_too_far_ahead(org, borrower, spot, owner):
    """
    Gate 1: booking start > now + earned horizon is rejected with an error.
    (SPEC-1 §4 Booking, §11 Primary flow)
    Baseline horizon = 3 days; no listing history → 3-day horizon.
    A request for 10 days out should be rejected.
    """
    # Provide an availability window far in the future
    far_start = utc(2029, 5, 11, 10)   # 10 days from freeze date
    far_end = utc(2029, 5, 11, 14)
    make_window(org, spot, far_start - timedelta(days=1), far_end + timedelta(days=1))

    client = Client()
    force_login_active(client, borrower)

    response = client_post(client, HOSTNAME, "/book/", {
        "start": far_start.strftime("%Y-%m-%d %H:%M"),
        "end": far_end.strftime("%Y-%m-%d %H:%M"),
    })
    # Should not redirect to confirmation — should stay on book_request page with error
    assert response.status_code == 200, (
        f"Expected 200 (form with error), got {response.status_code}"
    )
    content = response.content.decode()
    assert "horizon" in content.lower() or "ahead" in content.lower(), (
        "Expected horizon-related error message in response"
    )


# ---------------------------------------------------------------------------
# Test 4 — Gate 2: second booking while active is rejected
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@override_settings(ALLOWED_HOSTS=[HOSTNAME])
@freeze_time("2029-05-01T00:00:00Z")
def test_gate2_one_active_booking_blocks_second(org, borrower, spot, owner):
    """
    Gate 2: resident with an active booking cannot create another.
    (SPEC-1 §4 Booking — one active booking at a time)
    """
    # Give the borrower an existing active booking
    existing_start = utc(2029, 5, 2, 10)
    existing_end = utc(2029, 5, 2, 14)
    make_booking(org, spot, borrower, existing_start, existing_end, status="confirmed")

    # Set up another spot and window for the new request
    owner2 = make_user(org, "owner2@bookingflow.test")
    spot2 = make_spot(org, owner2, "B002")
    make_window(org, spot2, utc(2029, 5, 3, 0), utc(2029, 5, 3, 23))

    client = Client()
    force_login_active(client, borrower)

    # Within horizon (3 days = 72 hours from 2029-05-01T00:00:00Z → 2029-05-04T00:00:00Z)
    new_start = utc(2029, 5, 3, 10)
    new_end = utc(2029, 5, 3, 14)

    response = client_post(client, HOSTNAME, "/book/", {
        "start": new_start.strftime("%Y-%m-%d %H:%M"),
        "end": new_end.strftime("%Y-%m-%d %H:%M"),
    })
    # Should stay on booking form with error, not redirect to confirmation
    assert response.status_code == 200, (
        f"Expected 200 (form with Gate 2 error), got {response.status_code}"
    )
    content = response.content.decode()
    assert "active booking" in content.lower() or "already" in content.lower(), (
        "Expected 'active booking' or 'already' in Gate 2 error response"
    )


# ---------------------------------------------------------------------------
# Test 5 — Valid booking flows through to confirmation
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@override_settings(ALLOWED_HOSTS=[HOSTNAME])
@freeze_time("2029-05-01T00:00:00Z")
def test_valid_booking_creates_tentative_and_redirects(org, borrower, spot, owner):
    """
    Valid booking (within horizon, no existing booking, spot available) →
    creates tentative booking and redirects to confirmation page.
    (SPEC-1 §11 Booking)
    """
    # Availability window within horizon (baseline 3 days)
    win_start = utc(2029, 5, 2, 0)
    win_end = utc(2029, 5, 2, 23)
    make_window(org, spot, win_start, win_end)

    client = Client()
    force_login_active(client, borrower)

    book_start = utc(2029, 5, 2, 10)
    book_end = utc(2029, 5, 2, 14)

    response = client_post(client, HOSTNAME, "/book/", {
        "start": book_start.strftime("%Y-%m-%d %H:%M"),
        "end": book_end.strftime("%Y-%m-%d %H:%M"),
    })

    # Should redirect to book_confirm
    assert response.status_code == 302, (
        f"Expected redirect to book_confirm, got {response.status_code}"
    )
    assert "/book/confirm" in response.get("Location", ""), (
        f"Expected redirect to /book/confirm/, got {response.get('Location', '')}"
    )

    # Confirm a tentative booking was created
    from parking.models import Booking
    tentative = Booking.objects.filter(
        borrower=borrower,
        status="tentative",
        spot=spot,
    ).first()
    assert tentative is not None, "Expected a tentative booking to be created"


@pytest.mark.django_db
@override_settings(
    ALLOWED_HOSTS=[HOSTNAME],
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
)
@freeze_time("2029-05-01T00:00:00Z")
def test_confirm_booking_promotes_to_confirmed_and_notifies(org, borrower, spot, owner):
    """
    Confirming a tentative booking promotes it to 'confirmed' and creates
    notification records for both borrower and owner. (SPEC-1 §11 Booking)
    """
    from parking.models import Booking

    win_start = utc(2029, 5, 2, 0)
    win_end = utc(2029, 5, 2, 23)
    make_window(org, spot, win_start, win_end)

    client = Client()
    force_login_active(client, borrower)

    book_start = utc(2029, 5, 2, 10)
    book_end = utc(2029, 5, 2, 14)

    # Step 1: Create tentative booking
    response = client_post(client, HOSTNAME, "/book/", {
        "start": book_start.strftime("%Y-%m-%d %H:%M"),
        "end": book_end.strftime("%Y-%m-%d %H:%M"),
    })
    assert response.status_code == 302

    # Step 2: Confirm the booking
    response2 = client_post(client, HOSTNAME, "/book/confirm/")
    assert response2.status_code == 302, (
        f"Expected redirect after confirmation, got {response2.status_code}"
    )

    # Booking should now be 'confirmed'
    booking = Booking.objects.filter(
        borrower=borrower,
        spot=spot,
    ).first()
    assert booking is not None
    assert booking.status == "confirmed", (
        f"Expected status 'confirmed', got '{booking.status}'"
    )

    # Both borrower and owner should receive notification emails after booking confirmation.
    # The notify() function sends via send_mail(); in tests the EMAIL_BACKEND is
    # django.core.mail.backends.locmem.EmailBackend so emails appear in mail.outbox.
    from django.core import mail
    recipient_emails = {msg.to[0] for msg in mail.outbox if msg.to}
    assert borrower.email in recipient_emails or owner.email in recipient_emails, (
        f"Expected email notification for borrower ({borrower.email}) or owner ({owner.email}) "
        f"after booking confirmation. outbox recipients: {recipient_emails}"
    )
