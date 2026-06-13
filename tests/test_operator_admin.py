"""
Unit tests for operator_console.admin — admin actions.

Covers the uncovered lines in operator_console/admin.py:
  pii_erasure action (lines 125-138): multiple selection error path and success path
  impersonate_user action (lines 215-240): success path with session/audit
  AdminAuditLogAdmin.get_readonly_fields (line 280)
  BookingAdmin.admin_cancel_booking (lines 313-326)
"""

from datetime import datetime
from datetime import timezone as dt_timezone
from unittest.mock import MagicMock, patch

import factory
import pytest
from django.test import RequestFactory
from psycopg2.extras import DateTimeTZRange


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc(year, month, day, hour=0):
    return datetime(year, month, day, hour, tzinfo=dt_timezone.utc)


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------


class OrganizationFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "parking.Organization"

    name = factory.Sequence(lambda n: f"OAOrg {n}")
    hostname = factory.Sequence(lambda n: f"oaorg{n}.parkshare.test")
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
    email = factory.Sequence(lambda n: f"oauser{n}@example.com")
    display_name = factory.Sequence(lambda n: f"OA User {n}")
    status = "active"
    is_superuser = False
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
    spot_number = factory.Sequence(lambda n: f"OA{n:04d}")
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
        _utc(2030, 5, 1, 10),
        _utc(2030, 5, 1, 14),
    )
    status = "confirmed"
    penalty_hours = 0


def _make_admin_request(user, session=None):
    """Create a mock Django admin request."""
    rf = RequestFactory()
    request = rf.post("/")
    request.user = user
    request.session = session or {}
    return request


def _make_mock_modeladmin():
    """Create a mock ModelAdmin instance."""
    ma = MagicMock()
    ma.message_user = MagicMock()
    return ma


# ---------------------------------------------------------------------------
# pii_erasure action
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_pii_erasure_multiple_selection_shows_error():
    """pii_erasure shows an error message when more than one user is selected."""
    from accounts.models import User
    from operator_console.admin import pii_erasure

    org = OrganizationFactory()
    superuser = UserFactory(organization=org, is_superuser=True, is_staff=True)
    user1 = UserFactory(organization=org)
    user2 = UserFactory(organization=org)

    queryset = User.objects.filter(pk__in=[user1.pk, user2.pk])
    request = _make_admin_request(superuser)
    modeladmin = _make_mock_modeladmin()

    pii_erasure(modeladmin, request, queryset)

    modeladmin.message_user.assert_called_once()
    call_args = modeladmin.message_user.call_args
    # The message should mention "exactly one"
    msg_text = str(call_args[0][1])
    assert "exactly one" in msg_text.lower() or "one" in msg_text.lower(), (
        f"Expected 'exactly one' error message; got {msg_text!r}"
    )


@pytest.mark.django_db
def test_pii_erasure_single_user_erases_pii():
    """pii_erasure action erases PII for a single selected user."""
    from accounts.models import User
    from operator_console.admin import pii_erasure

    org = OrganizationFactory()
    superuser = UserFactory(organization=org, is_superuser=True, is_staff=True)
    target = UserFactory(organization=org)
    target_pk = target.pk

    queryset = User.objects.filter(pk=target.pk)
    request = _make_admin_request(superuser)
    modeladmin = _make_mock_modeladmin()

    pii_erasure(modeladmin, request, queryset)

    target.refresh_from_db()
    assert f"erased-{target_pk}@redacted.invalid" == target.email, (
        f"User email should be erased; got {target.email!r}"
    )
    modeladmin.message_user.assert_called_once()


# ---------------------------------------------------------------------------
# impersonate_user action
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_impersonate_user_multiple_selection_shows_error():
    """impersonate_user shows an error when more than one user is selected."""
    from accounts.models import User
    from operator_console.admin import UserAdmin
    from parkshare.admin_site import operator_admin_site

    org = OrganizationFactory()
    superuser = UserFactory(organization=org, is_superuser=True, is_staff=True)
    user1 = UserFactory(organization=org)
    user2 = UserFactory(organization=org)

    queryset = User.objects.filter(pk__in=[user1.pk, user2.pk])
    request = _make_admin_request(superuser, session={})

    admin_instance = UserAdmin(User, operator_admin_site)
    admin_instance.message_user = MagicMock()

    admin_instance.impersonate_user(request, queryset)

    admin_instance.message_user.assert_called_once()
    msg_text = str(admin_instance.message_user.call_args[0][1])
    assert "exactly one" in msg_text.lower() or "one" in msg_text.lower()


@pytest.mark.django_db
def test_impersonate_user_superuser_target_blocked():
    """impersonate_user blocks impersonation of a superuser."""
    from accounts.models import User
    from operator_console.admin import UserAdmin
    from parkshare.admin_site import operator_admin_site

    org = OrganizationFactory()
    operator = UserFactory(organization=org, is_superuser=True, is_staff=True)
    target_superuser = UserFactory(organization=org, is_superuser=True, is_staff=True)

    queryset = User.objects.filter(pk=target_superuser.pk)
    request = _make_admin_request(operator, session={})

    admin_instance = UserAdmin(User, operator_admin_site)
    admin_instance.message_user = MagicMock()

    admin_instance.impersonate_user(request, queryset)

    admin_instance.message_user.assert_called_once()
    msg_text = str(admin_instance.message_user.call_args[0][1])
    assert "superuser" in msg_text.lower(), (
        f"Expected 'superuser' in error; got {msg_text!r}"
    )
    # Session should NOT have 'impersonating' key
    assert "impersonating" not in request.session


@pytest.mark.django_db
def test_impersonate_user_success_sets_session_and_logs():
    """impersonate_user success sets session keys and creates AdminAuditLog entry."""
    from accounts.models import AdminAuditLog, User
    from operator_console.admin import UserAdmin
    from parkshare.admin_site import operator_admin_site

    org = OrganizationFactory()
    operator = UserFactory(organization=org, is_superuser=True, is_staff=True)
    target = UserFactory(organization=org, is_superuser=False)

    queryset = User.objects.filter(pk=target.pk)
    request = _make_admin_request(operator, session={})

    admin_instance = UserAdmin(User, operator_admin_site)
    admin_instance.message_user = MagicMock()

    result = admin_instance.impersonate_user(request, queryset)

    # Session should have impersonating key
    assert request.session.get("impersonating") == target.pk, (
        f"Session['impersonating'] should be target.pk; got {request.session.get('impersonating')}"
    )
    assert request.session.get("real_operator") == operator.pk

    # Audit log should exist
    entry = AdminAuditLog.objects.filter(
        action="impersonate_start",
        target_id=target.pk,
    ).first()
    assert entry is not None, "impersonate_start audit log should be created"


# ---------------------------------------------------------------------------
# AdminAuditLogAdmin.get_readonly_fields
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_audit_log_admin_has_no_add_permission():
    """AdminAuditLogAdmin.has_add_permission always returns False."""
    from accounts.models import AdminAuditLog
    from operator_console.admin import AdminAuditLogAdmin
    from parkshare.admin_site import operator_admin_site

    org = OrganizationFactory()
    admin_user = UserFactory(organization=org, is_superuser=True)

    admin_instance = AdminAuditLogAdmin(AdminAuditLog, operator_admin_site)
    rf = RequestFactory()
    request = rf.get("/")
    request.user = admin_user

    assert admin_instance.has_add_permission(request) is False
    assert admin_instance.has_change_permission(request) is False
    assert admin_instance.has_delete_permission(request) is False


@pytest.mark.django_db
def test_audit_log_admin_get_readonly_fields_via_admin_site():
    """AdminAuditLogAdmin.get_readonly_fields returns field names when called via admin site."""
    from accounts.models import AdminAuditLog
    from operator_console.admin import AdminAuditLogAdmin
    from parkshare.admin_site import operator_admin_site

    org = OrganizationFactory()
    admin_user = UserFactory(organization=org)
    entry = AdminAuditLog.objects.create(
        organization=org,
        actor=admin_user,
        action="test_action",
    )

    admin_instance = AdminAuditLogAdmin(AdminAuditLog, operator_admin_site)

    rf = RequestFactory()
    request = rf.get("/")
    request.user = admin_user

    # Manually set the _meta attribute to simulate Django admin registration
    from types import SimpleNamespace
    admin_instance._meta = SimpleNamespace(model=AdminAuditLog)

    readonly_fields = admin_instance.get_readonly_fields(request, obj=entry)

    assert len(readonly_fields) > 0, "get_readonly_fields should return a non-empty list"
    # All returned fields should be valid field names on the model
    model_field_names = {f.name for f in AdminAuditLog._meta.get_fields() if hasattr(f, "name")}
    for field_name in readonly_fields:
        assert field_name in model_field_names, (
            f"Returned field name {field_name!r} is not a valid model field"
        )


# ---------------------------------------------------------------------------
# BookingAdmin.admin_cancel_booking
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_admin_cancel_booking_cancels_active_bookings():
    """admin_cancel_booking cancels tentative/confirmed/active bookings and logs audit entries."""
    from accounts.models import AdminAuditLog
    from operator_console.admin import BookingAdmin
    from parking.models import Booking
    from parkshare.admin_site import operator_admin_site

    org = OrganizationFactory()
    operator = UserFactory(organization=org, is_superuser=True, is_staff=True)
    owner = UserFactory(organization=org)
    borrower = UserFactory(organization=org)
    spot = ParkingSpotFactory(organization=org, owner=owner)

    booking1 = BookingFactory(
        organization=org,
        spot=spot,
        borrower=borrower,
        time_range=DateTimeTZRange(_utc(2030, 6, 1, 10), _utc(2030, 6, 1, 14)),
        status="confirmed",
    )
    booking2 = BookingFactory(
        organization=org,
        spot=spot,
        borrower=borrower,
        time_range=DateTimeTZRange(_utc(2030, 7, 1, 10), _utc(2030, 7, 1, 14)),
        status="tentative",
    )

    queryset = Booking.objects.filter(pk__in=[booking1.pk, booking2.pk])
    request = _make_admin_request(operator)

    admin_instance = BookingAdmin(Booking, operator_admin_site)
    admin_instance.message_user = MagicMock()

    admin_instance.admin_cancel_booking(request, queryset)

    booking1.refresh_from_db()
    booking2.refresh_from_db()

    assert booking1.status == "cancelled_admin", (
        f"confirmed booking should be cancelled_admin; got {booking1.status!r}"
    )
    assert booking2.status == "cancelled_admin", (
        f"tentative booking should be cancelled_admin; got {booking2.status!r}"
    )

    # Audit log entries for each booking
    entry1 = AdminAuditLog.objects.filter(action="admin_cancel", target_id=booking1.pk).first()
    entry2 = AdminAuditLog.objects.filter(action="admin_cancel", target_id=booking2.pk).first()
    assert entry1 is not None, "admin_cancel audit log should be created for booking1"
    assert entry2 is not None, "admin_cancel audit log should be created for booking2"

    # Success message should be shown
    admin_instance.message_user.assert_called_once()


@pytest.mark.django_db
def test_admin_cancel_booking_skips_completed_bookings():
    """admin_cancel_booking ignores completed/cancelled bookings."""
    from operator_console.admin import BookingAdmin
    from parking.models import Booking
    from parkshare.admin_site import operator_admin_site

    org = OrganizationFactory()
    operator = UserFactory(organization=org, is_superuser=True, is_staff=True)
    owner = UserFactory(organization=org)
    borrower = UserFactory(organization=org)
    spot = ParkingSpotFactory(organization=org, owner=owner)

    booking = BookingFactory(
        organization=org,
        spot=spot,
        borrower=borrower,
        time_range=DateTimeTZRange(_utc(2030, 8, 1, 10), _utc(2030, 8, 1, 14)),
        status="completed",
    )

    queryset = Booking.objects.filter(pk=booking.pk)
    request = _make_admin_request(operator)

    admin_instance = BookingAdmin(Booking, operator_admin_site)
    admin_instance.message_user = MagicMock()

    admin_instance.admin_cancel_booking(request, queryset)

    booking.refresh_from_db()
    assert booking.status == "completed", (
        f"Completed booking should not be changed; got {booking.status!r}"
    )
