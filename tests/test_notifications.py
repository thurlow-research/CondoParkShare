"""
Unit tests for CondoParkShare Step 9 — notification dispatch and email relay.

Covers:
  notify() dispatch (1-8)
  send_relay_message() (9-14)
"""

import uuid
from datetime import datetime
from datetime import timezone as dt_timezone

import factory
import pytest
from django.test import override_settings
from psycopg2.extras import DateTimeTZRange

# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------


class OrganizationFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "parking.Organization"

    name = factory.Sequence(lambda n: f"NotifOrg {n}")
    hostname = factory.Sequence(lambda n: f"notiforg{n}.parkshare.test")
    support_email = factory.LazyAttribute(lambda o: f"support@{o.hostname}")
    registration_mode = "invite_only"
    timezone = "America/Los_Angeles"

    booking_horizon_baseline_days = 3
    booking_horizon_max_days = 30
    listing_to_horizon_ratio = 10
    tier_metric_window_days = 180
    launch_grace_days = 14
    launch_grace_horizon_days = 14


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "accounts.User"

    organization = factory.SubFactory(OrganizationFactory)
    email = factory.Sequence(lambda n: f"notifuser{n}@example.com")
    display_name = factory.Sequence(lambda n: f"Notif User {n}")
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
    spot_number = factory.Sequence(lambda n: f"N{n:04d}")
    status = "active"


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
        datetime(2029, 7, 1, 18, 0, tzinfo=dt_timezone.utc),
        datetime(2029, 7, 1, 22, 0, tzinfo=dt_timezone.utc),
    )
    status = "confirmed"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc(year, month, day, hour, minute=0, second=0):
    """Return a timezone-aware UTC datetime."""
    return datetime(year, month, day, hour, minute, second, tzinfo=dt_timezone.utc)


# ---------------------------------------------------------------------------
# 1. test_notify_sends_email_to_owner_on_booking_confirmed
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
def test_notify_sends_email_to_owner_on_booking_confirmed():
    """booking_confirmed event sends an email to the spot owner."""
    from django.core import mail

    from notifications.dispatch import notify

    org = OrganizationFactory()
    owner = UserFactory(organization=org, email="owner1@example.com")
    borrower = UserFactory(organization=org, email="borrower1@example.com")
    spot = ParkingSpotFactory(organization=org, owner=owner)
    booking = BookingFactory(
        organization=org,
        spot=spot,
        borrower=borrower,
        time_range=DateTimeTZRange(_utc(2029, 8, 1, 10), _utc(2029, 8, 1, 14)),
    )

    mail.outbox.clear()
    notify("booking_confirmed", booking)

    owner_emails = [m for m in mail.outbox if owner.email in m.to]
    assert len(owner_emails) >= 1, (
        f"Expected at least one email to owner {owner.email!r}; "
        f"outbox recipients: {[m.to for m in mail.outbox]}"
    )


# ---------------------------------------------------------------------------
# 2. test_notify_sends_email_to_borrower_on_booking_confirmed
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
def test_notify_sends_email_to_borrower_on_booking_confirmed():
    """booking_confirmed event sends an email to the borrower."""
    from django.core import mail

    from notifications.dispatch import notify

    org = OrganizationFactory()
    owner = UserFactory(organization=org, email="owner2@example.com")
    borrower = UserFactory(organization=org, email="borrower2@example.com")
    spot = ParkingSpotFactory(organization=org, owner=owner)
    booking = BookingFactory(
        organization=org,
        spot=spot,
        borrower=borrower,
        time_range=DateTimeTZRange(_utc(2029, 8, 2, 10), _utc(2029, 8, 2, 14)),
    )

    mail.outbox.clear()
    notify("booking_confirmed", booking)

    borrower_emails = [m for m in mail.outbox if borrower.email in m.to]
    assert len(borrower_emails) >= 1, (
        f"Expected at least one email to borrower {borrower.email!r}; "
        f"outbox recipients: {[m.to for m in mail.outbox]}"
    )


# ---------------------------------------------------------------------------
# 3. test_notify_skips_null_borrower
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
def test_notify_skips_null_borrower():
    """booking_confirmed with borrower=None does not raise an error."""
    from django.core import mail

    from notifications.dispatch import notify

    org = OrganizationFactory()
    owner = UserFactory(organization=org, email="owner3@example.com")
    spot = ParkingSpotFactory(organization=org, owner=owner)
    booking = BookingFactory(
        organization=org,
        spot=spot,
        borrower=None,
        time_range=DateTimeTZRange(_utc(2029, 8, 3, 10), _utc(2029, 8, 3, 14)),
    )

    mail.outbox.clear()
    # Should not raise any exception
    notify("booking_confirmed", booking)

    # Only the owner should receive mail (borrower is None)
    owner_emails = [m for m in mail.outbox if owner.email in m.to]
    assert (
        len(owner_emails) >= 1
    ), "Owner should still receive email when borrower is None"

    # No email should go to a null address
    for m in mail.outbox:
        assert None not in m.to, "None should not appear as a recipient"


# ---------------------------------------------------------------------------
# 4. test_notify_skips_null_owner
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
def test_notify_skips_null_owner():
    """booking_confirmed with spot.owner=None does not raise an error."""
    from django.core import mail

    from notifications.dispatch import notify

    org = OrganizationFactory()
    borrower = UserFactory(organization=org, email="borrower4@example.com")
    # Spot with no owner
    spot = ParkingSpotFactory(organization=org, owner=None)
    booking = BookingFactory(
        organization=org,
        spot=spot,
        borrower=borrower,
        time_range=DateTimeTZRange(_utc(2029, 8, 4, 10), _utc(2029, 8, 4, 14)),
    )

    mail.outbox.clear()
    # Should not raise any exception
    notify("booking_confirmed", booking)

    # Only the borrower should receive mail (owner is None)
    borrower_emails = [m for m in mail.outbox if borrower.email in m.to]
    assert (
        len(borrower_emails) >= 1
    ), "Borrower should still receive email when owner is None"


# ---------------------------------------------------------------------------
# 5. test_email_contains_spot_number
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
def test_email_contains_spot_number():
    """Email body for booking_confirmed includes the spot_number."""
    from django.core import mail

    from notifications.dispatch import notify

    org = OrganizationFactory()
    owner = UserFactory(organization=org, email="owner5@example.com")
    borrower = UserFactory(organization=org, email="borrower5@example.com")
    spot = ParkingSpotFactory(organization=org, owner=owner, spot_number="P5555")
    booking = BookingFactory(
        organization=org,
        spot=spot,
        borrower=borrower,
        time_range=DateTimeTZRange(_utc(2029, 8, 5, 10), _utc(2029, 8, 5, 14)),
    )

    mail.outbox.clear()
    notify("booking_confirmed", booking)

    assert mail.outbox, "Expected at least one email in outbox"
    for m in mail.outbox:
        assert "P5555" in m.body or "P5555" in m.subject, (
            f"Expected spot_number 'P5555' in email body or subject; "
            f"subject={m.subject!r}, body={m.body!r}"
        )


# ---------------------------------------------------------------------------
# 6. test_email_contains_local_time
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
def test_email_contains_local_time():
    """Email body shows the booking time in the organization's local timezone, not UTC."""
    from django.core import mail

    from notifications.dispatch import notify

    # Use America/Los_Angeles: UTC-7 in summer (PDT)
    # Booking 18:00 UTC = 11:00 PDT
    org = OrganizationFactory(timezone="America/Los_Angeles")
    owner = UserFactory(organization=org, email="owner6@example.com")
    borrower = UserFactory(organization=org, email="borrower6@example.com")
    spot = ParkingSpotFactory(organization=org, owner=owner)
    booking = BookingFactory(
        organization=org,
        spot=spot,
        borrower=borrower,
        # 18:00-22:00 UTC = 11:00 AM - 03:00 PM PDT
        time_range=DateTimeTZRange(_utc(2029, 8, 6, 18), _utc(2029, 8, 6, 22)),
    )

    mail.outbox.clear()
    notify("booking_confirmed", booking)

    assert mail.outbox, "Expected at least one email in outbox"

    # The local time (PDT, UTC-7) for 18:00 UTC is 11:00 AM
    # The email should contain "11:" (11 AM local) rather than "18:" (18 UTC)
    for m in mail.outbox:
        assert (
            "18:" not in m.body
        ), f"Email body should not contain UTC time '18:'; body={m.body!r}"
        # Should contain local time indicator — either "11:" for 11 AM PDT
        # or a timezone abbreviation like PDT or PST
        has_local_indicator = (
            "11:" in m.body or "PDT" in m.body or "PST" in m.body or "PT" in m.body
        )
        assert (
            has_local_indicator
        ), f"Email body should contain local time indicator (11:xx PDT/PST); body={m.body!r}"


# ---------------------------------------------------------------------------
# 7. test_owner_cancel_email_includes_reason
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
def test_owner_cancel_email_includes_reason():
    """booking_cancelled_by_owner email body contains cancel_reason when provided."""
    from django.core import mail

    from notifications.dispatch import notify

    org = OrganizationFactory()
    owner = UserFactory(organization=org, email="owner7@example.com")
    borrower = UserFactory(organization=org, email="borrower7@example.com")
    spot = ParkingSpotFactory(organization=org, owner=owner)
    cancel_reason = "Plumbing emergency in unit"
    booking = BookingFactory(
        organization=org,
        spot=spot,
        borrower=borrower,
        time_range=DateTimeTZRange(_utc(2029, 8, 7, 10), _utc(2029, 8, 7, 14)),
        status="confirmed",
        cancel_reason=cancel_reason,
    )

    mail.outbox.clear()
    notify("booking_cancelled_by_owner", booking)

    assert mail.outbox, "Expected at least one email in outbox"
    borrower_emails = [m for m in mail.outbox if borrower.email in m.to]
    assert borrower_emails, "Borrower should receive cancellation email"

    for m in borrower_emails:
        assert (
            cancel_reason in m.body
        ), f"cancel_reason {cancel_reason!r} not found in email body: {m.body!r}"


# ---------------------------------------------------------------------------
# 8. test_owner_cancel_email_no_reason_no_label
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
def test_owner_cancel_email_no_reason_no_label():
    """booking_cancelled_by_owner email body does not include 'Reason:' when cancel_reason is empty."""
    from django.core import mail

    from notifications.dispatch import notify

    org = OrganizationFactory()
    owner = UserFactory(organization=org, email="owner8@example.com")
    borrower = UserFactory(organization=org, email="borrower8@example.com")
    spot = ParkingSpotFactory(organization=org, owner=owner)
    booking = BookingFactory(
        organization=org,
        spot=spot,
        borrower=borrower,
        time_range=DateTimeTZRange(_utc(2029, 8, 8, 10), _utc(2029, 8, 8, 14)),
        status="confirmed",
        cancel_reason="",
    )

    mail.outbox.clear()
    notify("booking_cancelled_by_owner", booking)

    assert mail.outbox, "Expected at least one email in outbox"
    borrower_emails = [m for m in mail.outbox if borrower.email in m.to]
    assert borrower_emails, "Borrower should receive cancellation email"

    for m in borrower_emails:
        assert "Reason:" not in m.body, (
            f"'Reason:' label should not appear in email body when cancel_reason is empty; "
            f"body={m.body!r}"
        )


# ---------------------------------------------------------------------------
# 9. test_relay_send_creates_relay_message
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
def test_relay_send_creates_relay_message():
    """send_relay_message creates a RelayMessage record in the database."""
    from notifications.models import RelayMessage
    from notifications.relay import send_relay_message

    org = OrganizationFactory()
    owner = UserFactory(organization=org, email="owner9@example.com")
    borrower = UserFactory(organization=org, email="borrower9@example.com")
    spot = ParkingSpotFactory(organization=org, owner=owner)
    booking = BookingFactory(
        organization=org,
        spot=spot,
        borrower=borrower,
        time_range=DateTimeTZRange(_utc(2029, 9, 1, 10), _utc(2029, 9, 1, 14)),
    )

    msg = send_relay_message(
        from_user=borrower,
        to_user=owner,
        booking=booking,
        body="Hi, is the spot accessible from the main entrance?",
    )

    assert isinstance(
        msg, RelayMessage
    ), f"Expected RelayMessage instance, got {type(msg)}"
    assert RelayMessage.objects.filter(
        pk=msg.pk
    ).exists(), "RelayMessage record should exist in the database"
    assert msg.from_user == borrower
    assert msg.to_user == owner
    assert msg.booking == booking


# ---------------------------------------------------------------------------
# 10. test_relay_send_sends_email
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
def test_relay_send_sends_email():
    """send_relay_message sends an email to to_user."""
    from django.core import mail

    from notifications.relay import send_relay_message

    org = OrganizationFactory()
    owner = UserFactory(organization=org, email="owner10@example.com")
    borrower = UserFactory(organization=org, email="borrower10@example.com")
    spot = ParkingSpotFactory(organization=org, owner=owner)
    booking = BookingFactory(
        organization=org,
        spot=spot,
        borrower=borrower,
        time_range=DateTimeTZRange(_utc(2029, 9, 2, 10), _utc(2029, 9, 2, 14)),
    )

    mail.outbox.clear()
    send_relay_message(
        from_user=borrower,
        to_user=owner,
        booking=booking,
        body="What floor is the parking on?",
    )

    owner_emails = [m for m in mail.outbox if owner.email in m.to]
    assert len(owner_emails) >= 1, (
        f"Expected email sent to owner {owner.email!r}; "
        f"outbox recipients: {[m.to for m in mail.outbox]}"
    )


# ---------------------------------------------------------------------------
# 11. test_relay_rate_limit
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
def test_relay_rate_limit():
    """10 relay messages succeed; the 11th raises ValueError."""
    from notifications.relay import (
        MAX_MESSAGES_PER_USER_PER_BOOKING,
        send_relay_message,
    )

    org = OrganizationFactory()
    owner = UserFactory(organization=org, email="owner11@example.com")
    borrower = UserFactory(organization=org, email="borrower11@example.com")
    spot = ParkingSpotFactory(organization=org, owner=owner)
    booking = BookingFactory(
        organization=org,
        spot=spot,
        borrower=borrower,
        time_range=DateTimeTZRange(_utc(2029, 9, 3, 10), _utc(2029, 9, 3, 14)),
    )

    # Send up to the limit
    for i in range(MAX_MESSAGES_PER_USER_PER_BOOKING):
        send_relay_message(
            from_user=borrower,
            to_user=owner,
            booking=booking,
            body=f"Message {i + 1}",
        )

    # The next one should be rejected
    with pytest.raises(ValueError, match="[Mm]essage limit"):
        send_relay_message(
            from_user=borrower,
            to_user=owner,
            booking=booking,
            body="Over the limit",
        )


# ---------------------------------------------------------------------------
# 12. test_relay_token_expires_at_booking_end
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
def test_relay_token_expires_at_booking_end():
    """RelayMessage.token_expires_at equals booking.time_range.upper."""
    from notifications.relay import send_relay_message

    org = OrganizationFactory()
    owner = UserFactory(organization=org, email="owner12@example.com")
    borrower = UserFactory(organization=org, email="borrower12@example.com")
    spot = ParkingSpotFactory(organization=org, owner=owner)
    booking_end = _utc(2029, 9, 4, 16)
    booking = BookingFactory(
        organization=org,
        spot=spot,
        borrower=borrower,
        time_range=DateTimeTZRange(_utc(2029, 9, 4, 10), booking_end),
    )

    msg = send_relay_message(
        from_user=borrower,
        to_user=owner,
        booking=booking,
        body="Quick question about the spot.",
    )

    assert msg.token_expires_at == booking_end, (
        f"token_expires_at should equal booking end {booking_end}; "
        f"got {msg.token_expires_at}"
    )


# ---------------------------------------------------------------------------
# 13. test_relay_email_hides_real_addresses
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
def test_relay_email_hides_real_addresses():
    """Relay email FROM is DEFAULT_FROM_EMAIL; body does not contain real email addresses."""
    from django.core import mail

    from notifications.relay import send_relay_message

    org = OrganizationFactory(hostname="hidden-test.parkshare.test")
    owner = UserFactory(organization=org, email="realowner13@example.com")
    borrower = UserFactory(organization=org, email="realborrower13@example.com")
    spot = ParkingSpotFactory(organization=org, owner=owner)
    booking = BookingFactory(
        organization=org,
        spot=spot,
        borrower=borrower,
        time_range=DateTimeTZRange(_utc(2029, 9, 5, 10), _utc(2029, 9, 5, 14)),
    )

    mail.outbox.clear()
    send_relay_message(
        from_user=borrower,
        to_user=owner,
        booking=booking,
        body="Can I park a motorcycle here?",
    )

    assert mail.outbox, "Expected relay email in outbox"
    relay_email = mail.outbox[-1]

    # FROM must be Django's DEFAULT_FROM_EMAIL (not the org hostname),
    # so the configurable email sender is used consistently across all mail.
    from django.conf import settings

    expected_from = settings.DEFAULT_FROM_EMAIL
    assert (
        relay_email.from_email == expected_from
    ), f"Expected from_email={expected_from!r}, got {relay_email.from_email!r}"

    # Body must not expose real email addresses of participants
    assert (
        owner.email not in relay_email.body
    ), f"Owner's real email {owner.email!r} must not appear in relay email body"
    assert (
        borrower.email not in relay_email.body
    ), f"Borrower's real email {borrower.email!r} must not appear in relay email body"


# ---------------------------------------------------------------------------
# 14. test_relay_expired_token_returns_404
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
def test_relay_expired_token_returns_404():
    """GET to message_reply with an expired relay token returns HTTP 404 for
    authenticated users, and HTTP 302 (redirect to login) for anonymous users.

    Authentication is required before revealing whether a token is valid or
    expired — the token alone must not expose message content to third parties.
    """
    from django.test import RequestFactory

    from notifications.models import RelayMessage
    from notifications.views import message_reply

    org = OrganizationFactory()
    owner = UserFactory(organization=org, email="owner14@example.com")
    borrower = UserFactory(organization=org, email="borrower14@example.com")
    spot = ParkingSpotFactory(organization=org, owner=owner)
    booking = BookingFactory(
        organization=org,
        spot=spot,
        borrower=borrower,
        # Booking ended in the past — token is expired
        time_range=DateTimeTZRange(_utc(2025, 1, 1, 10), _utc(2025, 1, 1, 14)),
        status="completed",
    )

    token = uuid.uuid4()
    _ = RelayMessage.objects.create(
        organization=org,
        from_user=borrower,
        to_user=owner,
        booking=booking,
        body="Old message",
        reply_token=token,
        token_expires_at=_utc(2025, 1, 1, 14),  # expired
    )

    rf = RequestFactory()

    # Anonymous users are redirected to login before any token check.
    from django.contrib.auth.models import AnonymousUser

    anon_request = rf.get(f"/messages/reply/{token}/")
    anon_request.user = AnonymousUser()
    anon_response = message_reply(anon_request, token=token)
    assert (
        anon_response.status_code == 302
    ), f"Expected 302 (redirect to login) for anonymous user, got {anon_response.status_code}"
    assert "/login/" in anon_response["Location"]

    # Authenticated recipient sees 404 for expired token.
    auth_request = rf.get(f"/messages/reply/{token}/")
    auth_request.user = owner  # owner is to_user (the intended reply recipient)
    auth_response = message_reply(auth_request, token=token)
    assert (
        auth_response.status_code == 404
    ), f"Expected 404 for expired relay token, got {auth_response.status_code}"


# ---------------------------------------------------------------------------
# Regression (#157): outbound web-push must pass a timeout and must not let a
# requests-level network error escape the dispatcher.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_send_push_passes_timeout_to_webpush():
    """_send_push must pass a (connect, read) timeout so a hung endpoint
    cannot block a gunicorn worker indefinitely (#157)."""
    from unittest.mock import patch

    from notifications.dispatch import notify
    from notifications.models import WebPushSubscription

    org = OrganizationFactory()
    owner = UserFactory(
        organization=org,
        email="pushowner@example.com",
        notification_prefs={"push": True},
    )
    borrower = UserFactory(organization=org, email="pushborrower@example.com")
    spot = ParkingSpotFactory(organization=org, owner=owner)
    booking = BookingFactory(
        organization=org,
        spot=spot,
        borrower=borrower,
        time_range=DateTimeTZRange(_utc(2029, 9, 1, 10), _utc(2029, 9, 1, 14)),
    )
    WebPushSubscription.objects.create(
        user=owner,
        endpoint="https://push.example.com/ep/owner",
        p256dh="x" * 32,
        auth="y" * 16,
    )

    with patch("pywebpush.webpush") as mock_webpush:
        notify("booking_confirmed", booking)

    assert mock_webpush.called, "expected webpush to be invoked for a push-enabled user"
    _, kwargs = mock_webpush.call_args
    assert "timeout" in kwargs, "webpush must be called with an explicit timeout (#157)"
    assert kwargs["timeout"] is not None


@pytest.mark.django_db
def test_send_push_swallows_requests_network_error():
    """A requests-level error (e.g. connect timeout) from webpush must be
    caught so dispatch does not propagate it to the request thread (#157)."""
    from unittest.mock import patch

    import requests

    from notifications.dispatch import notify
    from notifications.models import WebPushSubscription

    org = OrganizationFactory()
    owner = UserFactory(
        organization=org,
        email="pushowner2@example.com",
        notification_prefs={"push": True},
    )
    borrower = UserFactory(organization=org, email="pushborrower2@example.com")
    spot = ParkingSpotFactory(organization=org, owner=owner)
    booking = BookingFactory(
        organization=org,
        spot=spot,
        borrower=borrower,
        time_range=DateTimeTZRange(_utc(2029, 9, 2, 10), _utc(2029, 9, 2, 14)),
    )
    WebPushSubscription.objects.create(
        user=owner,
        endpoint="https://push.example.com/ep/owner2",
        p256dh="x" * 32,
        auth="y" * 16,
    )

    with patch("pywebpush.webpush", side_effect=requests.exceptions.ConnectTimeout("boom")):
        # Must not raise.
        notify("booking_confirmed", booking)
