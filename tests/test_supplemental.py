"""
Supplemental unit tests — Phase 3e gate.

Covers gaps in:
  - parking/booking.py: lines 115 (confirm non-tentative), 191 (release wrong state),
      200 (release_up_to >= end)
  - parking/management/commands/clean_tentative_bookings.py: all 9 lines
  - notifications/dispatch.py: _send_push branch (lines 64-85)
  - parking/leaderboard.py: already in test_leaderboard.py
  - accounts/models.py: Invite.is_valid() paths, AdminAuditLog.log()
  - notifications/dispatch.py: _render_notification unknown event (default subject/body)
  - parking/availability.py: is_spot_available() (both True and False paths)
"""

from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from unittest.mock import MagicMock, patch

import factory
import pytest
from psycopg2.extras import DateTimeTZRange


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc(year, month, day, hour, minute=0, second=0):
    return datetime(year, month, day, hour, minute, second, tzinfo=dt_timezone.utc)


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------


class OrganizationFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "parking.Organization"

    name = factory.Sequence(lambda n: f"SuppOrg {n}")
    hostname = factory.Sequence(lambda n: f"supporg{n}.parkshare.test")
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
    email = factory.Sequence(lambda n: f"suppuser{n}@example.com")
    display_name = factory.Sequence(lambda n: f"Supp User {n}")
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
    spot_number = factory.Sequence(lambda n: f"SP{n:04d}")
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
        _utc(2029, 5, 10, 10),
        _utc(2029, 5, 10, 14),
    )
    status = "confirmed"
    penalty_hours = 0


# ---------------------------------------------------------------------------
# parking/booking.py — uncovered lines 115, 191, 200
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_confirm_non_tentative_raises_value_error():
    """confirm_booking raises ValueError when booking.status != 'tentative' (line 115)."""
    from parking.booking import confirm_booking

    org = OrganizationFactory()
    owner = UserFactory(organization=org)
    borrower = UserFactory(organization=org)
    spot = ParkingSpotFactory(organization=org, owner=owner)

    booking = BookingFactory(
        organization=org,
        spot=spot,
        borrower=borrower,
        time_range=DateTimeTZRange(_utc(2029, 5, 1, 10), _utc(2029, 5, 1, 14)),
        status="confirmed",  # not tentative
    )

    with pytest.raises(ValueError, match="not tentative"):
        confirm_booking(booking, borrower)


@pytest.mark.django_db
def test_release_booking_wrong_state_raises_value_error():
    """release_booking raises ValueError when booking is not confirmed or active (line 191)."""
    from freezegun import freeze_time

    from parking.booking import release_booking

    frozen_now = _utc(2029, 6, 1, 10)
    org = OrganizationFactory()
    owner = UserFactory(organization=org)
    borrower = UserFactory(organization=org)
    spot = ParkingSpotFactory(organization=org, owner=owner)

    booking = BookingFactory(
        organization=org,
        spot=spot,
        borrower=borrower,
        time_range=DateTimeTZRange(_utc(2029, 6, 1, 8), _utc(2029, 6, 1, 16)),
        status="tentative",  # invalid state for release
    )

    release_to = _utc(2029, 6, 1, 12)  # valid hour-aligned, future time
    with freeze_time(frozen_now):
        with pytest.raises(ValueError, match="Cannot release"):
            release_booking(booking, borrower, release_to)


@pytest.mark.django_db
def test_release_booking_at_or_after_end_raises_value_error():
    """release_booking raises ValueError when release_up_to >= booking end (line 200)."""
    from freezegun import freeze_time

    from parking.booking import release_booking

    frozen_now = _utc(2029, 6, 2, 10)
    org = OrganizationFactory()
    owner = UserFactory(organization=org)
    borrower = UserFactory(organization=org)
    spot = ParkingSpotFactory(organization=org, owner=owner)

    booking = BookingFactory(
        organization=org,
        spot=spot,
        borrower=borrower,
        time_range=DateTimeTZRange(_utc(2029, 6, 2, 8), _utc(2029, 6, 2, 16)),
        status="confirmed",
    )

    # release_up_to == booking end (not strictly before)
    release_at_end = _utc(2029, 6, 2, 16)
    with freeze_time(frozen_now):
        with pytest.raises(ValueError, match="before booking end"):
            release_booking(booking, borrower, release_at_end)


@pytest.mark.django_db
def test_release_booking_wrong_borrower_raises_permission_error():
    """release_booking raises PermissionError when borrower is not the booking's borrower."""
    from freezegun import freeze_time

    from parking.booking import release_booking

    frozen_now = _utc(2029, 6, 3, 10)
    org = OrganizationFactory()
    owner = UserFactory(organization=org)
    borrower = UserFactory(organization=org)
    other = UserFactory(organization=org)
    spot = ParkingSpotFactory(organization=org, owner=owner)

    booking = BookingFactory(
        organization=org,
        spot=spot,
        borrower=borrower,
        time_range=DateTimeTZRange(_utc(2029, 6, 3, 8), _utc(2029, 6, 3, 16)),
        status="confirmed",
    )

    release_to = _utc(2029, 6, 3, 12)
    with freeze_time(frozen_now):
        with pytest.raises(PermissionError, match="Not your booking"):
            release_booking(booking, other, release_to)


@pytest.mark.django_db
def test_cancel_booking_unauthorized_raises_permission_error():
    """cancel_booking raises PermissionError when cancelled_by is neither owner nor borrower."""
    from parking.booking import cancel_booking

    org = OrganizationFactory()
    owner = UserFactory(organization=org)
    borrower = UserFactory(organization=org)
    stranger = UserFactory(organization=org)
    spot = ParkingSpotFactory(organization=org, owner=owner)

    booking = BookingFactory(
        organization=org,
        spot=spot,
        borrower=borrower,
        time_range=DateTimeTZRange(_utc(2029, 7, 1, 10), _utc(2029, 7, 1, 14)),
        status="confirmed",
    )

    with pytest.raises(PermissionError, match="Not authorized"):
        cancel_booking(booking, cancelled_by=stranger)


@pytest.mark.django_db
def test_cancel_booking_already_finalised_raises_value_error():
    """cancel_booking raises ValueError when booking is already in a terminal state."""
    from parking.booking import cancel_booking

    org = OrganizationFactory()
    owner = UserFactory(organization=org)
    borrower = UserFactory(organization=org)
    spot = ParkingSpotFactory(organization=org, owner=owner)

    # Already cancelled by borrower — terminal state
    booking = BookingFactory(
        organization=org,
        spot=spot,
        borrower=borrower,
        time_range=DateTimeTZRange(_utc(2029, 7, 2, 10), _utc(2029, 7, 2, 14)),
        status="cancelled_borrower",
    )

    with pytest.raises(ValueError, match="already finalised"):
        cancel_booking(booking, cancelled_by=borrower)


# ---------------------------------------------------------------------------
# clean_tentative_bookings management command
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_clean_tentative_bookings_cancels_expired():
    """clean_tentative_bookings marks expired tentative bookings as cancelled_admin."""
    from django.core.management import call_command
    from freezegun import freeze_time

    from parking.models import Booking

    frozen_now = _utc(2027, 8, 15, 10)
    with freeze_time(frozen_now):
        org = OrganizationFactory()
        owner = UserFactory(organization=org)
        borrower = UserFactory(organization=org)
        spot = ParkingSpotFactory(organization=org, owner=owner)

        expired_at = frozen_now - timedelta(minutes=10)
        booking = BookingFactory(
            organization=org,
            spot=spot,
            borrower=borrower,
            time_range=DateTimeTZRange(
                frozen_now + timedelta(hours=2),
                frozen_now + timedelta(hours=6),
            ),
            status="tentative",
            tentative_expires_at=expired_at,
        )

        call_command("clean_tentative_bookings", verbosity=1)

    booking.refresh_from_db()
    assert booking.status == "cancelled_admin", (
        f"Expired tentative booking should be 'cancelled_admin'; got {booking.status!r}"
    )


@pytest.mark.django_db
def test_clean_tentative_bookings_leaves_unexpired():
    """clean_tentative_bookings does not touch tentative bookings that have not expired."""
    from django.core.management import call_command
    from freezegun import freeze_time

    from parking.models import Booking

    frozen_now = _utc(2027, 8, 16, 10)
    with freeze_time(frozen_now):
        org = OrganizationFactory()
        owner = UserFactory(organization=org)
        borrower = UserFactory(organization=org)
        spot = ParkingSpotFactory(organization=org, owner=owner)

        # Expires in 4 minutes — not yet expired
        not_expired_at = frozen_now + timedelta(minutes=4)
        booking = BookingFactory(
            organization=org,
            spot=spot,
            borrower=borrower,
            time_range=DateTimeTZRange(
                frozen_now + timedelta(hours=2),
                frozen_now + timedelta(hours=6),
            ),
            status="tentative",
            tentative_expires_at=not_expired_at,
        )

        call_command("clean_tentative_bookings", verbosity=0)

    booking.refresh_from_db()
    assert booking.status == "tentative", (
        f"Non-expired tentative booking should remain 'tentative'; got {booking.status!r}"
    )


@pytest.mark.django_db
def test_clean_tentative_bookings_verbosity_output():
    """clean_tentative_bookings with verbosity>=1 writes success output."""
    from io import StringIO

    from django.core.management import call_command
    from freezegun import freeze_time

    from parking.models import Booking

    frozen_now = _utc(2027, 8, 17, 10)
    with freeze_time(frozen_now):
        org = OrganizationFactory()
        owner = UserFactory(organization=org)
        borrower = UserFactory(organization=org)
        spot = ParkingSpotFactory(organization=org, owner=owner)

        expired_at = frozen_now - timedelta(minutes=1)
        BookingFactory(
            organization=org,
            spot=spot,
            borrower=borrower,
            time_range=DateTimeTZRange(
                frozen_now + timedelta(hours=2),
                frozen_now + timedelta(hours=6),
            ),
            status="tentative",
            tentative_expires_at=expired_at,
        )

        out = StringIO()
        call_command("clean_tentative_bookings", verbosity=1, stdout=out)

    output = out.getvalue()
    assert "clean_tentative_bookings" in output or "cancelled" in output.lower(), (
        f"Expected success message in output; got: {output!r}"
    )


# ---------------------------------------------------------------------------
# notifications/dispatch.py — push branch (lines 64-85)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_notify_sends_push_when_prefs_enabled():
    """_send calls _send_push when user has push=True and has push subscriptions."""
    from notifications.dispatch import _send

    org = OrganizationFactory()
    owner = UserFactory(organization=org, notification_prefs={"push": True})
    borrower = UserFactory(organization=org)
    spot = ParkingSpotFactory(organization=org, owner=owner)

    booking = BookingFactory(
        organization=org,
        spot=spot,
        borrower=borrower,
        time_range=DateTimeTZRange(_utc(2029, 8, 1, 10), _utc(2029, 8, 1, 14)),
    )

    # Patch push_subscriptions to simulate existing subscriptions
    mock_sub = MagicMock()
    mock_sub.endpoint = "https://push.example.com/sub/1"
    mock_sub.p256dh = "BNEzGrYOQuqxhLiVwH6YKp1234567890"
    mock_sub.auth = "auth1234567890"

    with patch("notifications.dispatch._send_email"), \
         patch("notifications.dispatch._send_push") as mock_push, \
         patch.object(type(owner), "push_subscriptions", create=True) as mock_subs_prop:
        # Make push_subscriptions.exists() return True
        mock_subs = MagicMock()
        mock_subs.exists.return_value = True
        mock_subs_prop.__get__ = MagicMock(return_value=mock_subs)

        _send(owner, "booking_confirmed", booking)
        mock_push.assert_called_once()


@pytest.mark.django_db
def test_notify_no_push_when_prefs_disabled():
    """_send does not call _send_push when user has push=False."""
    from notifications.dispatch import _send

    org = OrganizationFactory()
    owner = UserFactory(organization=org, notification_prefs={"push": False})
    borrower = UserFactory(organization=org)
    spot = ParkingSpotFactory(organization=org, owner=owner)

    booking = BookingFactory(
        organization=org,
        spot=spot,
        borrower=borrower,
        time_range=DateTimeTZRange(_utc(2029, 8, 2, 10), _utc(2029, 8, 2, 14)),
    )

    with patch("notifications.dispatch._send_email"), \
         patch("notifications.dispatch._send_push") as mock_push:
        _send(owner, "booking_confirmed", booking)
        mock_push.assert_not_called()


@pytest.mark.django_db
def test_send_push_importerror_handled_gracefully():
    """_send_push handles ImportError (no pywebpush) without raising."""
    from notifications.dispatch import _send_push

    org = OrganizationFactory()
    owner = UserFactory(organization=org)
    borrower = UserFactory(organization=org)
    spot = ParkingSpotFactory(organization=org, owner=owner)

    booking = BookingFactory(
        organization=org,
        spot=spot,
        borrower=borrower,
        time_range=DateTimeTZRange(_utc(2029, 8, 3, 10), _utc(2029, 8, 3, 14)),
    )

    # Simulate pywebpush not being installed
    with patch.dict("sys.modules", {"pywebpush": None}):
        # Should not raise any exception
        _send_push(owner, "booking_confirmed", booking)


@pytest.mark.django_db
def test_render_notification_unknown_event_returns_defaults():
    """_render_notification returns default subject/body for unknown events."""
    from notifications.dispatch import _render_notification

    org = OrganizationFactory()
    owner = UserFactory(organization=org)
    borrower = UserFactory(organization=org)
    spot = ParkingSpotFactory(organization=org, owner=owner)

    booking = BookingFactory(
        organization=org,
        spot=spot,
        borrower=borrower,
        time_range=DateTimeTZRange(_utc(2029, 8, 4, 10), _utc(2029, 8, 4, 14)),
    )

    subject, body = _render_notification("unknown_event_xyz", booking, owner)

    assert subject == "CondoParkShare notification", (
        f"Unknown event should return default subject; got {subject!r}"
    )
    assert body == "", (
        f"Unknown event should return empty body; got {body!r}"
    )


@pytest.mark.django_db
def test_notify_unknown_event_does_not_send():
    """notify() with an event not in OWNER_EVENTS or BORROWER_EVENTS sends nothing."""
    from notifications.dispatch import notify

    org = OrganizationFactory()
    owner = UserFactory(organization=org)
    borrower = UserFactory(organization=org)
    spot = ParkingSpotFactory(organization=org, owner=owner)

    booking = BookingFactory(
        organization=org,
        spot=spot,
        borrower=borrower,
        time_range=DateTimeTZRange(_utc(2029, 8, 5, 10), _utc(2029, 8, 5, 14)),
    )

    with patch("notifications.dispatch._send") as mock_send:
        notify("not_a_real_event", booking)
        mock_send.assert_not_called()


# ---------------------------------------------------------------------------
# accounts/models.py — Invite.is_valid() and AdminAuditLog.log()
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_invite_is_valid_when_uses_not_exhausted():
    """Invite is valid when use_count < max_uses and not expired."""
    from accounts.models import Invite

    org = OrganizationFactory()
    admin = UserFactory(organization=org)

    invite = Invite.objects.create(
        organization=org,
        issued_by=admin,
        code="valid-code-abc",
        max_uses=3,
        use_count=1,
        expires_at=None,
    )

    assert invite.is_valid() is True, "Invite should be valid when use_count < max_uses"


@pytest.mark.django_db
def test_invite_is_invalid_when_uses_exhausted():
    """Invite is invalid when use_count >= max_uses."""
    from accounts.models import Invite

    org = OrganizationFactory()
    admin = UserFactory(organization=org)

    invite = Invite.objects.create(
        organization=org,
        issued_by=admin,
        code="exhausted-code-abc",
        max_uses=1,
        use_count=1,
    )

    assert invite.is_valid() is False, "Invite should be invalid when use_count >= max_uses"


@pytest.mark.django_db
def test_invite_is_invalid_when_expired():
    """Invite is invalid when expires_at is in the past."""
    from django.utils.timezone import now

    from accounts.models import Invite

    org = OrganizationFactory()
    admin = UserFactory(organization=org)

    invite = Invite.objects.create(
        organization=org,
        issued_by=admin,
        code="expired-code-abc",
        max_uses=10,
        use_count=0,
        expires_at=now() - timedelta(days=1),
    )

    assert invite.is_valid() is False, "Invite should be invalid when expires_at is in the past"


@pytest.mark.django_db
def test_admin_audit_log_log_classmethod():
    """AdminAuditLog.log() creates an entry with all specified fields."""
    from accounts.models import AdminAuditLog

    org = OrganizationFactory()
    actor = UserFactory(organization=org)

    entry = AdminAuditLog.log(
        actor=actor,
        action="test_action",
        organization=org,
        target_type="user",
        target_id=actor.pk,
        notes="test note",
    )

    assert entry.pk is not None, "Log entry should be saved to DB"
    assert entry.action == "test_action"
    assert entry.target_type == "user"
    assert entry.target_id == actor.pk
    assert entry.notes == "test note"
    assert entry.actor == actor
    assert entry.organization == org


@pytest.mark.django_db
def test_admin_audit_log_log_falls_back_to_actor_org():
    """AdminAuditLog.log() falls back to actor.organization when org is not supplied."""
    from accounts.models import AdminAuditLog

    org = OrganizationFactory()
    actor = UserFactory(organization=org)

    entry = AdminAuditLog.log(
        actor=actor,
        action="test_fallback_action",
        # no organization= argument
    )

    assert entry.organization == org, (
        f"Should fall back to actor.organization; got {entry.organization!r}"
    )


# ---------------------------------------------------------------------------
# parking/availability.py — is_spot_available() paths
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_is_spot_available_returns_true_when_no_conflicts():
    """is_spot_available returns True when availability window covers the range and no conflicts."""
    from parking.availability import is_spot_available
    from parking.models import AvailabilityWindow

    org = OrganizationFactory()
    owner = UserFactory(organization=org)
    spot = ParkingSpotFactory(organization=org, owner=owner, status="active")

    # Create a covering availability window
    AvailabilityWindow.objects.create(
        organization=org,
        spot=spot,
        time_range=DateTimeTZRange(_utc(2029, 9, 1, 0), _utc(2029, 9, 30, 0)),
    )

    result = is_spot_available(spot, _utc(2029, 9, 10, 10), _utc(2029, 9, 10, 14))
    assert result is True, "Spot should be available when window covers range and no bookings"


@pytest.mark.django_db
def test_is_spot_available_returns_false_when_no_window():
    """is_spot_available returns False when no availability window covers the range."""
    from parking.availability import is_spot_available

    org = OrganizationFactory()
    owner = UserFactory(organization=org)
    spot = ParkingSpotFactory(organization=org, owner=owner, status="active")

    # No availability windows created
    result = is_spot_available(spot, _utc(2029, 9, 10, 10), _utc(2029, 9, 10, 14))
    assert result is False, "Spot should not be available when no window covers the range"


@pytest.mark.django_db
def test_is_spot_available_returns_false_with_conflicting_booking():
    """is_spot_available returns False when a conflicting active booking exists."""
    from parking.availability import is_spot_available
    from parking.models import AvailabilityWindow

    org = OrganizationFactory()
    owner = UserFactory(organization=org)
    borrower = UserFactory(organization=org)
    spot = ParkingSpotFactory(organization=org, owner=owner, status="active")

    AvailabilityWindow.objects.create(
        organization=org,
        spot=spot,
        time_range=DateTimeTZRange(_utc(2029, 9, 1, 0), _utc(2029, 9, 30, 0)),
    )

    # Book the spot for an overlapping time
    BookingFactory(
        organization=org,
        spot=spot,
        borrower=borrower,
        time_range=DateTimeTZRange(_utc(2029, 9, 10, 10), _utc(2029, 9, 10, 14)),
        status="confirmed",
    )

    # Request overlaps the booking (within the buffer window)
    result = is_spot_available(spot, _utc(2029, 9, 10, 10), _utc(2029, 9, 10, 14))
    assert result is False, "Spot should not be available when a booking conflicts"


@pytest.mark.django_db
def test_get_available_slots_returns_spot_with_no_conflicts():
    """get_available_slots returns spots that are available."""
    from parking.availability import get_available_slots
    from parking.models import AvailabilityWindow

    org = OrganizationFactory()
    owner = UserFactory(organization=org)
    spot = ParkingSpotFactory(organization=org, owner=owner, status="active")

    AvailabilityWindow.objects.create(
        organization=org,
        spot=spot,
        time_range=DateTimeTZRange(_utc(2029, 10, 1, 0), _utc(2029, 10, 31, 0)),
    )

    result = list(get_available_slots(org, _utc(2029, 10, 10, 10), _utc(2029, 10, 10, 14)))
    pks = [s.pk for s in result]
    assert spot.pk in pks, "Available spot should be returned by get_available_slots"


@pytest.mark.django_db
def test_get_available_slots_excludes_conflicting_spots():
    """get_available_slots excludes spots with conflicting bookings."""
    from parking.availability import get_available_slots
    from parking.models import AvailabilityWindow

    org = OrganizationFactory()
    owner = UserFactory(organization=org)
    borrower = UserFactory(organization=org)
    spot = ParkingSpotFactory(organization=org, owner=owner, status="active")

    AvailabilityWindow.objects.create(
        organization=org,
        spot=spot,
        time_range=DateTimeTZRange(_utc(2029, 10, 1, 0), _utc(2029, 10, 31, 0)),
    )

    # Book the spot
    BookingFactory(
        organization=org,
        spot=spot,
        borrower=borrower,
        time_range=DateTimeTZRange(_utc(2029, 10, 10, 10), _utc(2029, 10, 10, 14)),
        status="confirmed",
    )

    result = list(get_available_slots(org, _utc(2029, 10, 10, 10), _utc(2029, 10, 10, 14)))
    pks = [s.pk for s in result]
    assert spot.pk not in pks, "Spot with conflicting booking should be excluded"


# ---------------------------------------------------------------------------
# parking/booking.py — cancel_booking active state notification routing
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_cancel_booking_owner_sends_notification():
    """cancel_booking by owner sends 'booking_cancelled_by_owner' notification."""
    from parking.booking import cancel_booking

    org = OrganizationFactory()
    owner = UserFactory(organization=org)
    borrower = UserFactory(organization=org)
    spot = ParkingSpotFactory(organization=org, owner=owner)

    booking = BookingFactory(
        organization=org,
        spot=spot,
        borrower=borrower,
        time_range=DateTimeTZRange(_utc(2029, 11, 1, 10), _utc(2029, 11, 1, 14)),
        status="confirmed",
    )

    with patch("notifications.dispatch.notify") as mock_notify:
        cancel_booking(booking, cancelled_by=owner)

    events_fired = [c.args[0] for c in mock_notify.call_args_list]
    assert "booking_cancelled_by_owner" in events_fired, (
        f"booking_cancelled_by_owner should be dispatched; got {events_fired}"
    )


@pytest.mark.django_db
def test_cancel_booking_borrower_sends_notification():
    """cancel_booking by borrower sends 'booking_cancelled_by_borrower' notification."""
    from parking.booking import cancel_booking

    org = OrganizationFactory()
    owner = UserFactory(organization=org)
    borrower = UserFactory(organization=org)
    spot = ParkingSpotFactory(organization=org, owner=owner)

    booking = BookingFactory(
        organization=org,
        spot=spot,
        borrower=borrower,
        time_range=DateTimeTZRange(_utc(2029, 11, 2, 10), _utc(2029, 11, 2, 14)),
        status="confirmed",
    )

    with patch("notifications.dispatch.notify") as mock_notify:
        cancel_booking(booking, cancelled_by=borrower)

    events_fired = [c.args[0] for c in mock_notify.call_args_list]
    assert "booking_cancelled_by_borrower" in events_fired, (
        f"booking_cancelled_by_borrower should be dispatched; got {events_fired}"
    )


# ---------------------------------------------------------------------------
# parking/horizon.py — check_horizon_gate success path (boundary test)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_check_horizon_gate_within_horizon_returns_true():
    """check_horizon_gate returns True when requested start is within earned horizon."""
    from freezegun import freeze_time

    from parking.horizon import check_horizon_gate

    frozen_now = _utc(2027, 6, 1, 12)
    with freeze_time(frozen_now):
        org = OrganizationFactory(
            launched_at=_utc(2027, 1, 1, 0),
            booking_horizon_baseline_days=3,  # 72h horizon
            booking_horizon_max_days=30,
            listing_to_horizon_ratio=10,
            tier_metric_window_days=180,
            launch_grace_days=14,
        )
        borrower = UserFactory(organization=org)

        # Request start is 71 hours from now — within the 72h baseline horizon
        within_horizon = frozen_now + timedelta(hours=71)
        result = check_horizon_gate(borrower, within_horizon)

    assert result is True, (
        "check_horizon_gate must return True when requested start is within the horizon"
    )


# ---------------------------------------------------------------------------
# notifications/dispatch.py — booking_starts event (owner-only)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_notify_booking_starts_only_goes_to_borrower():
    """booking_starts event routes to borrower only (not in OWNER_EVENTS)."""
    import django.test

    from notifications.dispatch import BORROWER_EVENTS, OWNER_EVENTS

    # Verify the routing tables are correct
    assert "booking_starts" in BORROWER_EVENTS, "booking_starts must be in BORROWER_EVENTS"
    assert "booking_starts" not in OWNER_EVENTS, "booking_starts must not be in OWNER_EVENTS"


@pytest.mark.django_db
def test_notify_early_release_goes_to_both():
    """early_release_confirmed routes to both owner and borrower."""
    from notifications.dispatch import BORROWER_EVENTS, OWNER_EVENTS

    assert "early_release_confirmed" in OWNER_EVENTS, (
        "early_release_confirmed must be in OWNER_EVENTS"
    )
    assert "early_release_confirmed" in BORROWER_EVENTS, (
        "early_release_confirmed must be in BORROWER_EVENTS"
    )


# ---------------------------------------------------------------------------
# accounts/models.py — User.__str__ and edge cases
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_user_str_representation():
    """User.__str__() returns display_name <email>."""
    org = OrganizationFactory()
    user = UserFactory(organization=org, display_name="Jane Smith", email="jane@example.com")

    assert str(user) == "Jane Smith <jane@example.com>", (
        f"Unexpected __str__: {str(user)!r}"
    )


@pytest.mark.django_db
def test_organization_str_representation():
    """Organization.__str__() returns name."""
    from parking.models import Organization

    org = Organization.objects.create(
        name="Sunset Towers",
        hostname="sunsettowers.test",
        support_email="support@sunsettowers.test",
    )
    assert str(org) == "Sunset Towers"


@pytest.mark.django_db
def test_booking_str_representation():
    """Booking.__str__() includes pk, spot_id, and status."""
    org = OrganizationFactory()
    owner = UserFactory(organization=org)
    borrower = UserFactory(organization=org)
    spot = ParkingSpotFactory(organization=org, owner=owner)

    booking = BookingFactory(
        organization=org,
        spot=spot,
        borrower=borrower,
        time_range=DateTimeTZRange(_utc(2029, 12, 1, 10), _utc(2029, 12, 1, 14)),
        status="confirmed",
    )

    result = str(booking)
    assert str(booking.pk) in result
    assert "confirmed" in result
