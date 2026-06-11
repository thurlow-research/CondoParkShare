"""
Unit tests for accounts.erasure.erase_user_pii — Step 11 (right-to-erasure).

Tests:
 1.  test_erasure_scrubs_email
 2.  test_erasure_scrubs_display_name
 3.  test_erasure_clears_phone
 4.  test_erasure_clears_recovery_codes
 5.  test_erasure_password_unusable
 6.  test_erasure_deletes_totp_devices
 7.  test_erasure_deletes_push_subscriptions
 8.  test_erasure_anonymises_bookings
 9.  test_erasure_cancels_active_bookings_first
10.  test_erasure_scrubs_relay_bodies
11.  test_erasure_logs_audit_entry
12.  test_erasure_preserves_booking_records
13.  test_erasure_atomic
"""

import uuid
from datetime import datetime, timezone as dt_timezone, timedelta
from unittest.mock import patch

import pytest
import factory
from psycopg2.extras import DateTimeTZRange


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------

class OrganizationFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = 'parking.Organization'

    name = factory.Sequence(lambda n: f'ErasureOrg {n}')
    hostname = factory.Sequence(lambda n: f'erasureorg{n}.parkshare.test')
    support_email = factory.LazyAttribute(lambda o: f'support@{o.hostname}')
    registration_mode = 'invite_only'
    timezone = 'America/Los_Angeles'
    booking_horizon_baseline_days = 3
    booking_horizon_max_days = 30
    listing_to_horizon_ratio = 10
    tier_metric_window_days = 180
    launch_grace_days = 14
    launch_grace_horizon_days = 14


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = 'accounts.User'

    organization = factory.SubFactory(OrganizationFactory)
    email = factory.Sequence(lambda n: f'erasureuser{n}@example.com')
    display_name = factory.Sequence(lambda n: f'Erasure User {n}')
    status = 'active'
    last_booking_at = None

    @classmethod
    def _create(cls, model_class, *args, **kwargs):
        manager = model_class.objects
        password = kwargs.pop('password', 'test-password-secure!')
        return manager.create_user(password=password, *args, **kwargs)


class ParkingSpotFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = 'parking.ParkingSpot'

    organization = factory.SubFactory(OrganizationFactory)
    owner = None
    spot_number = factory.Sequence(lambda n: f'E{n:04d}')
    status = 'active'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utc(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=dt_timezone.utc)


def _make_booking(org, spot, borrower, offset_days=0, status='confirmed'):
    from parking.models import Booking
    base = datetime(2030, 6, 1 + offset_days, 10, 0, tzinfo=dt_timezone.utc)
    tr = DateTimeTZRange(base, base.replace(hour=12))
    return Booking.objects.create(
        organization=org,
        spot=spot,
        borrower=borrower,
        time_range=tr,
        status=status,
    )


def _make_relay_message(org, from_user, to_user, booking, body='Hello'):
    from notifications.models import RelayMessage
    from django.utils.timezone import now as django_now
    return RelayMessage.objects.create(
        organization=org,
        from_user=from_user,
        to_user=to_user,
        booking=booking,
        body=body,
        token_expires_at=django_now() + timedelta(hours=2),
    )


def _make_totp_device(user):
    from django_otp.plugins.otp_totp.models import TOTPDevice
    return TOTPDevice.objects.create(user=user, name='default', confirmed=True)


def _make_push_subscription(user):
    from notifications.models import WebPushSubscription
    return WebPushSubscription.objects.create(
        user=user,
        endpoint=f'https://fcm.googleapis.com/fcm/send/{uuid.uuid4()}',
        p256dh='BNEzGrYOQuqxhLiVwH6YKp1234567890',
        auth='auth1234567890',
    )


# ---------------------------------------------------------------------------
# 1. test_erasure_scrubs_email
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_erasure_scrubs_email():
    """After erase_user_pii, user.email contains 'erased-' and '@redacted.invalid'."""
    from accounts.erasure import erase_user_pii

    org = OrganizationFactory()
    user = UserFactory(organization=org)
    admin = UserFactory(organization=org)
    user_pk = user.pk

    erase_user_pii(user, erased_by=admin)
    user.refresh_from_db()

    assert 'erased-' in user.email, f"email should contain 'erased-', got {user.email!r}"
    assert '@redacted.invalid' in user.email, (
        f"email should contain '@redacted.invalid', got {user.email!r}"
    )
    assert user.email == f'erased-{user_pk}@redacted.invalid', (
        f"email should be 'erased-{{pk}}@redacted.invalid', got {user.email!r}"
    )


# ---------------------------------------------------------------------------
# 2. test_erasure_scrubs_display_name
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_erasure_scrubs_display_name():
    """After erase_user_pii, user.display_name is '[Erased User]'."""
    from accounts.erasure import erase_user_pii

    org = OrganizationFactory()
    user = UserFactory(organization=org)
    admin = UserFactory(organization=org)

    erase_user_pii(user, erased_by=admin)
    user.refresh_from_db()

    assert user.display_name == '[Erased User]', (
        f"display_name should be '[Erased User]', got {user.display_name!r}"
    )


# ---------------------------------------------------------------------------
# 3. test_erasure_clears_phone
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_erasure_clears_phone():
    """After erase_user_pii, user.phone is None."""
    from accounts.erasure import erase_user_pii

    org = OrganizationFactory()
    user = UserFactory(organization=org, phone='+15550001234')
    admin = UserFactory(organization=org)

    erase_user_pii(user, erased_by=admin)
    user.refresh_from_db()

    assert user.phone is None, f"phone should be None, got {user.phone!r}"


# ---------------------------------------------------------------------------
# 4. test_erasure_clears_recovery_codes
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_erasure_clears_recovery_codes():
    """After erase_user_pii, user.recovery_codes is []."""
    from accounts.erasure import erase_user_pii

    org = OrganizationFactory()
    user = UserFactory(organization=org, recovery_codes=['hash-a', 'hash-b', 'hash-c'])
    admin = UserFactory(organization=org)

    erase_user_pii(user, erased_by=admin)
    user.refresh_from_db()

    assert user.recovery_codes == [], (
        f"recovery_codes should be [], got {user.recovery_codes!r}"
    )


# ---------------------------------------------------------------------------
# 5. test_erasure_password_unusable
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_erasure_password_unusable():
    """After erase_user_pii, user.has_usable_password() is False."""
    from accounts.erasure import erase_user_pii

    org = OrganizationFactory()
    user = UserFactory(organization=org, password='a-real-password-123!')
    admin = UserFactory(organization=org)

    # Confirm usable before erasure
    assert user.has_usable_password() is True

    erase_user_pii(user, erased_by=admin)
    user.refresh_from_db()

    assert user.has_usable_password() is False, (
        "has_usable_password() should be False after erasure"
    )


# ---------------------------------------------------------------------------
# 6. test_erasure_deletes_totp_devices
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_erasure_deletes_totp_devices():
    """After erase_user_pii, TOTPDevice.objects.filter(user=user).count() == 0."""
    from django_otp.plugins.otp_totp.models import TOTPDevice
    from accounts.erasure import erase_user_pii

    org = OrganizationFactory()
    user = UserFactory(organization=org)
    admin = UserFactory(organization=org)

    _make_totp_device(user)
    assert TOTPDevice.objects.filter(user=user).count() == 1

    erase_user_pii(user, erased_by=admin)

    assert TOTPDevice.objects.filter(user=user).count() == 0, (
        "All TOTPDevice records for the erased user should be deleted"
    )


# ---------------------------------------------------------------------------
# 7. test_erasure_deletes_push_subscriptions
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_erasure_deletes_push_subscriptions():
    """After erase_user_pii, WebPushSubscription count for user is 0."""
    from notifications.models import WebPushSubscription
    from accounts.erasure import erase_user_pii

    org = OrganizationFactory()
    user = UserFactory(organization=org)
    admin = UserFactory(organization=org)

    _make_push_subscription(user)
    _make_push_subscription(user)
    assert WebPushSubscription.objects.filter(user=user).count() == 2

    erase_user_pii(user, erased_by=admin)

    assert WebPushSubscription.objects.filter(user=user).count() == 0, (
        "All WebPushSubscription records for the erased user should be deleted"
    )


# ---------------------------------------------------------------------------
# 8. test_erasure_anonymises_bookings
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_erasure_anonymises_bookings():
    """After erase_user_pii, bookings have borrower=None and is_anonymized=True."""
    from parking.models import Booking
    from accounts.erasure import erase_user_pii

    org = OrganizationFactory()
    spot = ParkingSpotFactory(organization=org)
    user = UserFactory(organization=org)
    admin = UserFactory(organization=org)

    booking = _make_booking(org, spot, user, offset_days=0, status='confirmed')

    erase_user_pii(user, erased_by=admin)
    booking.refresh_from_db()

    assert booking.borrower_id is None, (
        f"booking.borrower should be None after erasure, got {booking.borrower_id!r}"
    )
    assert booking.is_anonymized is True, (
        f"booking.is_anonymized should be True after erasure, got {booking.is_anonymized!r}"
    )


# ---------------------------------------------------------------------------
# 9. test_erasure_cancels_active_bookings_first
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_erasure_cancels_active_bookings_first():
    """Active booking is set to cancelled_admin THEN booking.borrower is None."""
    from parking.models import Booking
    from accounts.erasure import erase_user_pii

    org = OrganizationFactory()
    spot = ParkingSpotFactory(organization=org)
    user = UserFactory(organization=org)
    admin = UserFactory(organization=org)

    # Create an active booking
    booking = _make_booking(org, spot, user, offset_days=1, status='active')

    erase_user_pii(user, erased_by=admin)
    booking.refresh_from_db()

    # Booking was first cancelled (status=cancelled_admin), then anonymised
    assert booking.status == 'cancelled_admin', (
        f"Active booking should be cancelled_admin after erasure, got {booking.status!r}"
    )
    assert booking.borrower_id is None, (
        "booking.borrower should be None (anonymised) after erasure"
    )


# ---------------------------------------------------------------------------
# 10. test_erasure_scrubs_relay_bodies
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_erasure_scrubs_relay_bodies():
    """Relay message body is '[erased]' and the record still exists."""
    from notifications.models import RelayMessage
    from accounts.erasure import erase_user_pii

    org = OrganizationFactory()
    spot = ParkingSpotFactory(organization=org)
    sender = UserFactory(organization=org)
    receiver = UserFactory(organization=org)
    admin = UserFactory(organization=org)

    booking = _make_booking(org, spot, receiver, offset_days=2, status='confirmed')
    msg = _make_relay_message(org, sender, receiver, booking, body='Sensitive content')
    msg_pk = msg.pk

    erase_user_pii(sender, erased_by=admin)

    # Record still exists
    assert RelayMessage.objects.filter(pk=msg_pk).exists(), (
        "RelayMessage record should still exist after erasure (audit trail)"
    )

    msg.refresh_from_db()
    assert msg.body == '[erased]', (
        f"RelayMessage body should be '[erased]', got {msg.body!r}"
    )


# ---------------------------------------------------------------------------
# 11. test_erasure_logs_audit_entry
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_erasure_logs_audit_entry():
    """AdminAuditLog entry with action='pii_erasure' and target_id=user.pk is created."""
    from accounts.models import AdminAuditLog
    from accounts.erasure import erase_user_pii

    org = OrganizationFactory()
    user = UserFactory(organization=org)
    admin = UserFactory(organization=org)
    user_pk = user.pk

    erase_user_pii(user, erased_by=admin)

    entry = AdminAuditLog.objects.filter(
        action='pii_erasure',
        target_id=user_pk,
    ).first()

    assert entry is not None, (
        "AdminAuditLog should have an entry with action='pii_erasure' and target_id=user.pk"
    )
    assert entry.target_type == 'user', (
        f"target_type should be 'user', got {entry.target_type!r}"
    )
    assert entry.actor_id == admin.pk, (
        f"actor should be the admin user, got actor_id={entry.actor_id!r}"
    )


# ---------------------------------------------------------------------------
# 12. test_erasure_preserves_booking_records
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_erasure_preserves_booking_records():
    """Booking records still exist (not deleted) after erasure."""
    from parking.models import Booking
    from accounts.erasure import erase_user_pii

    org = OrganizationFactory()
    spot = ParkingSpotFactory(organization=org)
    user = UserFactory(organization=org)
    admin = UserFactory(organization=org)

    booking1 = _make_booking(org, spot, user, offset_days=3, status='confirmed')
    booking1_pk = booking1.pk

    erase_user_pii(user, erased_by=admin)

    # Records must still exist (anonymised, not deleted)
    assert Booking.objects.filter(pk=booking1_pk).exists(), (
        "Booking records should still exist after erasure (only anonymised, not deleted)"
    )


# ---------------------------------------------------------------------------
# 13. test_erasure_atomic
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_erasure_atomic():
    """All-or-nothing: if any step fails the transaction is rolled back."""
    from accounts.erasure import erase_user_pii
    from accounts.models import AdminAuditLog

    org = OrganizationFactory()
    user = UserFactory(organization=org)
    admin = UserFactory(organization=org)
    original_email = user.email
    original_display_name = user.display_name
    user_pk = user.pk

    # Force a failure inside the transaction after PII scrub but before commit
    # by patching AdminAuditLog.objects.create to raise an exception.
    with patch(
        'accounts.models.AdminAuditLog.objects.create',
        side_effect=Exception('Simulated DB failure'),
    ):
        with pytest.raises(Exception, match='Simulated DB failure'):
            erase_user_pii(user, erased_by=admin)

    # Transaction should have been rolled back — user PII must be intact
    user.refresh_from_db()
    assert user.email == original_email, (
        f"email should be unchanged after rolled-back erasure, got {user.email!r}"
    )
    assert user.display_name == original_display_name, (
        f"display_name should be unchanged after rolled-back erasure, "
        f"got {user.display_name!r}"
    )

    # No audit log entry should exist
    assert not AdminAuditLog.objects.filter(
        action='pii_erasure', target_id=user_pk
    ).exists(), "AdminAuditLog entry should not exist after rolled-back transaction"
