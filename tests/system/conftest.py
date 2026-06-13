"""
Shared fixtures and helpers for system-level tests.

All system tests use Django's full test Client (not RequestFactory) to
exercise the complete request/response cycle including middleware, sessions,
and redirect chains.

Since TenantMiddleware resolves the Organization from HTTP_HOST, every
request must carry a hostname that matches an Organization in the DB.
We use override_settings(ALLOWED_HOSTS=[...]) per test and pass
SERVER_NAME=<hostname> to client.get/post.
"""

from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

import pytest
from django.contrib.auth.hashers import make_password
from psycopg2.extras import DateTimeTZRange


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------


def utc(year, month, day, hour=0, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=dt_timezone.utc)


# ---------------------------------------------------------------------------
# Model-creation helpers
# ---------------------------------------------------------------------------


def make_org(name="TestOrg", hostname="testorg.parkshare.test", **kwargs):
    from parking.models import Organization

    defaults = dict(
        name=name,
        hostname=hostname,
        support_email=f"support@{hostname}",
        registration_mode="invite_only",
        timezone="America/Los_Angeles",
        booking_horizon_baseline_days=3,
        booking_horizon_max_days=30,
        listing_to_horizon_ratio=10,
        tier_metric_window_days=180,
        launch_grace_days=14,
        launch_grace_horizon_days=14,
        launched_at=None,
    )
    defaults.update(kwargs)
    return Organization.objects.create(**defaults)


def make_user(org, email, password="Test-Pass-1!", status="active", **kwargs):
    from accounts.models import User

    return User.objects.create_user(
        email=email,
        organization=org,
        display_name=kwargs.pop("display_name", email.split("@")[0]),
        password=password,
        status=status,
        **kwargs,
    )


def make_spot(org, owner, spot_number="A001", status="active"):
    from parking.models import ParkingSpot

    return ParkingSpot.objects.create(
        organization=org,
        owner=owner,
        spot_number=spot_number,
        status=status,
    )


def make_window(org, spot, start, end):
    from parking.models import AvailabilityWindow

    return AvailabilityWindow.objects.create(
        organization=org,
        spot=spot,
        time_range=DateTimeTZRange(start, end),
    )


def make_booking(org, spot, borrower, start, end, status="confirmed"):
    from parking.models import Booking

    return Booking.objects.create(
        organization=org,
        spot=spot,
        borrower=borrower,
        time_range=DateTimeTZRange(start, end),
        status=status,
    )


# ---------------------------------------------------------------------------
# Client helpers
# ---------------------------------------------------------------------------


def force_login_active(client, user):
    """
    Force-login a user via the test client, bypassing TOTP.

    We use force_login so system tests are not blocked by TOTP ceremony.
    The TOTP authentication flow itself is tested separately in test_auth*.
    """
    client.force_login(user)


def client_get(client, hostname, path, **kwargs):
    return client.get(path, SERVER_NAME=hostname, **kwargs)


def client_post(client, hostname, path, data=None, **kwargs):
    return client.post(path, data=data or {}, SERVER_NAME=hostname, **kwargs)
