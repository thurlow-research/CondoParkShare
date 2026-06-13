"""
Unit tests for notifications.views — push subscription and relay message views.

Covers the uncovered lines in notifications/views.py:
  push_subscribe (lines 35-51)
  push_unsubscribe (lines 58-68)
  message_send GET/POST paths (lines 84-155)
  message_reply valid token GET/POST paths (lines 178-224)
"""

import json
import uuid
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

import factory
import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, override_settings
from django.utils.timezone import now
from psycopg2.extras import DateTimeTZRange


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc(year, month, day, hour=0, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=dt_timezone.utc)


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------


class OrganizationFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "parking.Organization"

    name = factory.Sequence(lambda n: f"NotifViewOrg {n}")
    hostname = factory.Sequence(lambda n: f"notifvieworg{n}.parkshare.test")
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
    email = factory.Sequence(lambda n: f"nvuser{n}@example.com")
    display_name = factory.Sequence(lambda n: f"NV User {n}")
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
    spot_number = factory.Sequence(lambda n: f"NV{n:04d}")
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
        _utc(2030, 6, 1, 10),
        _utc(2030, 6, 1, 14),
    )
    status = "confirmed"


def _make_request(method="GET", user=None, data=None, body=None, session=None, org=None):
    """Helper: create a RequestFactory request with user and org attached."""
    rf = RequestFactory()
    if method.upper() == "POST":
        if body is not None:
            req = rf.post("/", data=body, content_type="application/json")
        else:
            req = rf.post("/", data or {})
    else:
        req = rf.get("/")
    req.user = user or AnonymousUser()
    req.session = session or {}
    if org is not None:
        req.organization = org
    return req


# ---------------------------------------------------------------------------
# push_subscribe
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_push_subscribe_creates_subscription():
    """push_subscribe POST creates a WebPushSubscription record."""
    from notifications.models import WebPushSubscription
    from notifications.views import push_subscribe

    org = OrganizationFactory()
    user = UserFactory(organization=org)

    payload = json.dumps({
        "endpoint": "https://push.example.com/test-endpoint-123",
        "keys": {
            "p256dh": "BNEzGrYOQuqxhLiVwH6YKp1234567890",
            "auth": "auth1234567890",
        },
    })

    request = _make_request("POST", user=user, body=payload, org=org)
    response = push_subscribe(request)

    assert response.status_code == 200
    data = json.loads(response.content)
    assert data.get("status") == "subscribed"

    assert WebPushSubscription.objects.filter(
        user=user, endpoint="https://push.example.com/test-endpoint-123"
    ).exists(), "WebPushSubscription should be created"


@pytest.mark.django_db
def test_push_subscribe_invalid_json_returns_400():
    """push_subscribe POST with invalid JSON returns 400."""
    from notifications.views import push_subscribe

    org = OrganizationFactory()
    user = UserFactory(organization=org)

    rf = RequestFactory()
    request = rf.post("/", data="not-json", content_type="application/json")
    request.user = user
    request.session = {}

    response = push_subscribe(request)

    assert response.status_code == 400
    data = json.loads(response.content)
    assert "error" in data


@pytest.mark.django_db
def test_push_subscribe_missing_keys_returns_400():
    """push_subscribe POST with missing keys returns 400."""
    from notifications.views import push_subscribe

    org = OrganizationFactory()
    user = UserFactory(organization=org)

    payload = json.dumps({"endpoint": "https://push.example.com/test"})  # missing keys

    request = _make_request("POST", user=user, body=payload, org=org)
    response = push_subscribe(request)

    assert response.status_code == 400


# ---------------------------------------------------------------------------
# push_unsubscribe
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_push_unsubscribe_removes_subscription():
    """push_unsubscribe POST removes the matching WebPushSubscription."""
    from notifications.models import WebPushSubscription
    from notifications.views import push_unsubscribe

    org = OrganizationFactory()
    user = UserFactory(organization=org)

    endpoint = "https://push.example.com/remove-me"
    WebPushSubscription.objects.create(
        user=user,
        endpoint=endpoint,
        p256dh="BNEzGrYOQuqxhLiVwH6YKp1234567890",
        auth="auth1234567890",
    )

    payload = json.dumps({"endpoint": endpoint})
    request = _make_request("POST", user=user, body=payload, org=org)
    response = push_unsubscribe(request)

    assert response.status_code == 200
    data = json.loads(response.content)
    assert data.get("status") == "unsubscribed"

    assert not WebPushSubscription.objects.filter(
        user=user, endpoint=endpoint
    ).exists(), "WebPushSubscription should be deleted"


@pytest.mark.django_db
def test_push_unsubscribe_invalid_json_returns_400():
    """push_unsubscribe POST with invalid JSON returns 400."""
    from notifications.views import push_unsubscribe

    org = OrganizationFactory()
    user = UserFactory(organization=org)

    rf = RequestFactory()
    request = rf.post("/", data="not-json", content_type="application/json")
    request.user = user
    request.session = {}

    response = push_unsubscribe(request)

    assert response.status_code == 400


# ---------------------------------------------------------------------------
# message_send
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_message_send_get_renders_form():
    """message_send GET renders the send form for the borrower."""
    from notifications.views import message_send
    from parking.models import Booking

    org = OrganizationFactory()
    owner = UserFactory(organization=org)
    borrower = UserFactory(organization=org)
    spot = ParkingSpotFactory(organization=org, owner=owner)

    booking = BookingFactory(
        organization=org,
        spot=spot,
        borrower=borrower,
        time_range=DateTimeTZRange(_utc(2030, 7, 1, 10), _utc(2030, 7, 1, 14)),
        status="confirmed",
    )

    request = _make_request("GET", user=borrower, org=org)

    # Booking.scoped uses org-scoped manager — patch it to return our booking
    with _patch_scoped_get(booking):
        response = message_send(request, booking_pk=booking.pk)

    assert response.status_code == 200


@pytest.mark.django_db
def test_message_send_post_sends_message():
    """message_send POST creates a relay message and returns sent confirmation."""
    from notifications.views import message_send

    org = OrganizationFactory()
    owner = UserFactory(organization=org)
    borrower = UserFactory(organization=org)
    spot = ParkingSpotFactory(organization=org, owner=owner)

    booking = BookingFactory(
        organization=org,
        spot=spot,
        borrower=borrower,
        time_range=DateTimeTZRange(_utc(2030, 7, 2, 10), _utc(2030, 7, 2, 14)),
        status="confirmed",
    )

    rf = RequestFactory()
    request = rf.post("/", {"body": "Hi, just checking on the spot."})
    request.user = borrower
    request.session = {}
    request.organization = org

    with _patch_scoped_get(booking):
        with _patch_send_relay():
            response = message_send(request, booking_pk=booking.pk)

    assert response.status_code == 200


@pytest.mark.django_db
def test_message_send_permission_denied_for_unrelated_user():
    """message_send raises PermissionDenied for a user unrelated to the booking."""
    from django.core.exceptions import PermissionDenied as DjangoPermissionDenied

    from notifications.views import message_send

    org = OrganizationFactory()
    owner = UserFactory(organization=org)
    borrower = UserFactory(organization=org)
    stranger = UserFactory(organization=org)
    spot = ParkingSpotFactory(organization=org, owner=owner)

    booking = BookingFactory(
        organization=org,
        spot=spot,
        borrower=borrower,
        time_range=DateTimeTZRange(_utc(2030, 7, 3, 10), _utc(2030, 7, 3, 14)),
        status="confirmed",
    )

    request = _make_request("GET", user=stranger, org=org)

    with _patch_scoped_get(booking):
        with pytest.raises(DjangoPermissionDenied):
            message_send(request, booking_pk=booking.pk)


@pytest.mark.django_db
def test_message_send_non_messageable_status_returns_400():
    """message_send returns 400 for bookings not in confirmed/active status."""
    from notifications.views import message_send

    org = OrganizationFactory()
    owner = UserFactory(organization=org)
    borrower = UserFactory(organization=org)
    spot = ParkingSpotFactory(organization=org, owner=owner)

    booking = BookingFactory(
        organization=org,
        spot=spot,
        borrower=borrower,
        time_range=DateTimeTZRange(_utc(2030, 7, 4, 10), _utc(2030, 7, 4, 14)),
        status="completed",
    )

    request = _make_request("GET", user=borrower, org=org)

    with _patch_scoped_get(booking):
        response = message_send(request, booking_pk=booking.pk)

    assert response.status_code == 400


@pytest.mark.django_db
def test_message_send_expired_booking_returns_400():
    """message_send returns 400 when booking.time_range.upper <= now."""
    from freezegun import freeze_time

    from notifications.views import message_send

    org = OrganizationFactory()
    owner = UserFactory(organization=org)
    borrower = UserFactory(organization=org)
    spot = ParkingSpotFactory(organization=org, owner=owner)

    # Booking in the past (already ended)
    booking = BookingFactory(
        organization=org,
        spot=spot,
        borrower=borrower,
        time_range=DateTimeTZRange(_utc(2025, 1, 1, 10), _utc(2025, 1, 1, 14)),
        status="confirmed",
    )

    frozen_now = _utc(2026, 6, 12, 12)
    request = _make_request("GET", user=borrower, org=org)

    with freeze_time(frozen_now):
        with _patch_scoped_get(booking):
            response = message_send(request, booking_pk=booking.pk)

    assert response.status_code == 400


# ---------------------------------------------------------------------------
# message_reply
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_message_reply_valid_token_get_renders_form():
    """message_reply GET with a valid token renders the reply form."""
    from notifications.models import RelayMessage
    from notifications.views import message_reply

    org = OrganizationFactory()
    owner = UserFactory(organization=org)
    borrower = UserFactory(organization=org)
    spot = ParkingSpotFactory(organization=org, owner=owner)

    booking = BookingFactory(
        organization=org,
        spot=spot,
        borrower=borrower,
        time_range=DateTimeTZRange(_utc(2030, 8, 1, 10), _utc(2030, 8, 1, 14)),
        status="confirmed",
    )

    token = uuid.uuid4()
    RelayMessage.objects.create(
        organization=org,
        from_user=borrower,
        to_user=owner,
        booking=booking,
        body="Hello",
        reply_token=token,
        token_expires_at=_utc(2030, 8, 1, 14),  # in the future
    )

    from freezegun import freeze_time

    request = _make_request("GET", user=AnonymousUser(), org=org)

    # Freeze time so the token appears not expired
    with freeze_time(_utc(2030, 8, 1, 11)):
        response = message_reply(request, token=token)

    assert response.status_code == 200


@pytest.mark.django_db
def test_message_reply_expired_token_returns_404():
    """message_reply returns 404 for an expired token."""
    from notifications.models import RelayMessage
    from notifications.views import message_reply

    org = OrganizationFactory()
    owner = UserFactory(organization=org)
    borrower = UserFactory(organization=org)
    spot = ParkingSpotFactory(organization=org, owner=owner)

    booking = BookingFactory(
        organization=org,
        spot=spot,
        borrower=borrower,
        time_range=DateTimeTZRange(_utc(2025, 1, 1, 10), _utc(2025, 1, 1, 14)),
        status="completed",
    )

    token = uuid.uuid4()
    RelayMessage.objects.create(
        organization=org,
        from_user=borrower,
        to_user=owner,
        booking=booking,
        body="Old message",
        reply_token=token,
        token_expires_at=_utc(2025, 1, 1, 14),  # expired
    )

    request = _make_request("GET", user=AnonymousUser(), org=org)
    response = message_reply(request, token=token)

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Helpers for patching scoped manager and relay
# ---------------------------------------------------------------------------


from contextlib import contextmanager
from unittest.mock import MagicMock, patch


@contextmanager
def _patch_scoped_get(booking):
    """Patch get_object_or_404 in notifications.views to return the given booking."""
    with patch("notifications.views.get_object_or_404", return_value=booking):
        yield


@contextmanager
def _patch_send_relay():
    """Patch send_relay_message to be a no-op."""
    with patch("notifications.views.send_relay_message"), \
         patch("notifications.views.can_send_relay", return_value=True):
        yield
