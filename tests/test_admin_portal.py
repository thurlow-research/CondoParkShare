"""
Unit tests for CondoParkShare Step 10 — HOA admin portal.

Covers:
  AdminAuditLog on admin_cancel (1)
  AdminAuditLog on block (2)
  AdminAuditLog on approve_user (3)
  AdminAuditLog on pii_access via resident_list (4)
  AdminAuditLog immutability in Django admin (5)
  HOA portal tenant isolation — org A admin cannot access org B resident (6)
  HOA portal requires hoa_admin — regular resident blocked (7)
  HOA portal wrong org rejected — org A admin on org B hostname (8)
  resident_approve changes user.status to active (9)
  resident_block changes user.status to blocked (10)
  spot_approve changes ParkingSpot.status to active (11)
  invite_create uses secrets.token_urlsafe (12)
  impersonation blocked for superuser target (13)
"""

import secrets

import factory
import pytest
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------

class OrganizationFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = 'parking.Organization'

    name = factory.Sequence(lambda n: f'PortalOrg {n}')
    hostname = factory.Sequence(lambda n: f'portalorg{n}.parkshare.test')
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
    email = factory.Sequence(lambda n: f'portaluser{n}@example.com')
    display_name = factory.Sequence(lambda n: f'Portal User {n}')
    status = 'active'
    is_hoa_admin = False

    @classmethod
    def _create(cls, model_class, *args, **kwargs):
        manager = model_class.objects
        password = kwargs.pop('password', 'test-password-secure!')
        return manager.create_user(password=password, *args, **kwargs)


class ParkingSpotFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = 'parking.ParkingSpot'

    organization = factory.SubFactory(OrganizationFactory)
    owner = factory.SubFactory(
        UserFactory,
        organization=factory.SelfAttribute('..organization'),
    )
    spot_number = factory.Sequence(lambda n: f'S{n:04d}')
    status = 'active'


class BookingFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = 'parking.Booking'

    organization = factory.SubFactory(OrganizationFactory)
    spot = factory.SubFactory(
        ParkingSpotFactory,
        organization=factory.SelfAttribute('..organization'),
    )
    borrower = factory.SubFactory(
        UserFactory,
        organization=factory.SelfAttribute('..organization'),
    )
    status = 'confirmed'

    @classmethod
    def _create(cls, model_class, *args, **kwargs):
        from datetime import datetime, timezone as dt_timezone
        from psycopg2.extras import DateTimeTZRange
        if 'time_range' not in kwargs:
            kwargs['time_range'] = DateTimeTZRange(
                datetime(2029, 6, 1, 10, 0, tzinfo=dt_timezone.utc),
                datetime(2029, 6, 1, 14, 0, tzinfo=dt_timezone.utc),
            )
        return super()._create(model_class, *args, **kwargs)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_operator_admin():
    """
    Load /operator/admin.py without triggering Django's import machinery for
    the 'operator' package (which shadows Python's stdlib ``operator`` module).

    Uses ``importlib.util.spec_from_file_location`` with the operator app's
    admin_site.register temporarily replaced by a no-op so that the module-level
    ``@operator_admin_site.register(...)`` decorators do not attempt to
    re-register already-registered models.

    Returns the loaded module object.  Callers can access
    ``AdminAuditLogAdmin`` and ``UserAdmin`` from it.
    """
    import importlib.util
    from django.apps import apps
    from parkshare.admin_site import operator_admin_site

    orig_register = operator_admin_site.register

    def noop_register(*args, **kwargs):
        def decorator(cls):
            return cls
        if args and isinstance(args[0], type) and hasattr(args[0], '_meta'):
            return decorator
        return decorator

    operator_admin_site.register = noop_register
    try:
        spec = importlib.util.spec_from_file_location(
            '_ps_operator_admin',
            '/Users/sthurlow/Code/CondoParkShare/operator/admin.py',
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        operator_admin_site.register = orig_register

    return mod


def _make_hoa_request(view_func, admin_user, org, method='GET', post_data=None, path='/portal/'):
    """
    Build a fake request with request.user and request.organization set,
    then call view_func(request, ...).  Returns the request (not the response)
    so callers can also call view_func themselves.
    """
    rf = RequestFactory()
    if method == 'POST':
        request = rf.post(path, data=post_data or {})
    else:
        request = rf.get(path)
    request.user = admin_user
    request.organization = org
    # Minimal session for views that may access it
    request.session = {}
    return request


# ---------------------------------------------------------------------------
# 1. test_audit_log_written_on_admin_cancel
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_audit_log_written_on_admin_cancel():
    """admin cancel of booking via portal_booking_cancel creates AdminAuditLog with action='admin_cancel'."""
    from accounts.models import AdminAuditLog
    from portal.views import portal_booking_cancel

    org = OrganizationFactory()
    admin = UserFactory(organization=org, is_hoa_admin=True, status='active')
    borrower = UserFactory(organization=org)
    spot = ParkingSpotFactory(organization=org)
    booking = BookingFactory(organization=org, spot=spot, borrower=borrower, status='confirmed')

    request = _make_hoa_request(portal_booking_cancel, admin, org, method='POST', post_data={})

    portal_booking_cancel(request, pk=booking.pk)

    log_entry = AdminAuditLog.objects.filter(
        action='admin_cancel',
        target_type='booking',
        target_id=booking.pk,
        organization=org,
    ).first()

    assert log_entry is not None, (
        "AdminAuditLog entry with action='admin_cancel' was not created after portal booking cancel."
    )
    assert log_entry.actor == admin


# ---------------------------------------------------------------------------
# 2. test_audit_log_written_on_block
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_audit_log_written_on_block():
    """Blocking a user via resident_block creates AdminAuditLog with action='block'."""
    from accounts.models import AdminAuditLog
    from portal.views import resident_block

    org = OrganizationFactory()
    admin = UserFactory(organization=org, is_hoa_admin=True, status='active')
    target = UserFactory(organization=org, status='active')

    request = _make_hoa_request(resident_block, admin, org, method='POST', post_data={})

    resident_block(request, pk=target.pk)

    log_entry = AdminAuditLog.objects.filter(
        action='block',
        target_type='user',
        target_id=target.pk,
        organization=org,
    ).first()

    assert log_entry is not None, (
        "AdminAuditLog entry with action='block' was not created after resident_block."
    )
    assert log_entry.actor == admin


# ---------------------------------------------------------------------------
# 3. test_audit_log_written_on_approve
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_audit_log_written_on_approve():
    """Approving a user via resident_approve creates AdminAuditLog with action='approve_user'."""
    from accounts.models import AdminAuditLog
    from portal.views import resident_approve

    org = OrganizationFactory()
    admin = UserFactory(organization=org, is_hoa_admin=True, status='active')
    target = UserFactory(organization=org, status='pending_approval')

    request = _make_hoa_request(resident_approve, admin, org, method='POST', post_data={})

    resident_approve(request, pk=target.pk)

    log_entry = AdminAuditLog.objects.filter(
        action='approve_user',
        target_type='user',
        target_id=target.pk,
        organization=org,
    ).first()

    assert log_entry is not None, (
        "AdminAuditLog entry with action='approve_user' was not created after resident_approve."
    )
    assert log_entry.actor == admin


# ---------------------------------------------------------------------------
# 4. test_audit_log_written_on_pii_access
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_audit_log_written_on_pii_access():
    """resident_list view creates an AdminAuditLog entry with action='pii_access'."""
    from accounts.models import AdminAuditLog
    from portal.views import resident_list

    org = OrganizationFactory()
    admin = UserFactory(organization=org, is_hoa_admin=True, status='active')

    # Count before
    before = AdminAuditLog.objects.filter(action='pii_access', organization=org).count()

    request = _make_hoa_request(resident_list, admin, org)

    # resident_list calls render(), which needs templates — patch it to avoid template errors
    from unittest.mock import patch
    with patch('portal.views.render') as mock_render:
        from django.http import HttpResponse
        mock_render.return_value = HttpResponse('ok')
        resident_list(request)

    after = AdminAuditLog.objects.filter(action='pii_access', organization=org).count()

    assert after == before + 1, (
        f"Expected one new pii_access log entry; before={before}, after={after}."
    )


# ---------------------------------------------------------------------------
# 5. test_audit_log_immutable
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_audit_log_immutable():
    """AdminAuditLog has no add, update, or delete permission in the operator admin."""
    from accounts.models import AdminAuditLog
    from parkshare.admin_site import operator_admin_site

    mod = _load_operator_admin()
    AdminAuditLogAdmin = mod.AdminAuditLogAdmin

    # Instantiate the admin class the same way Django does
    admin_instance = AdminAuditLogAdmin(
        model=AdminAuditLog,
        admin_site=operator_admin_site,
    )

    # Build a minimal superuser request
    org = OrganizationFactory()
    superuser = UserFactory(organization=org, is_hoa_admin=True, is_staff=True, status='active')
    superuser.is_superuser = True
    superuser.save(update_fields=['is_superuser'])

    rf = RequestFactory()
    request = rf.get('/')
    request.user = superuser

    assert admin_instance.has_add_permission(request) is False, (
        "AdminAuditLogAdmin.has_add_permission() must return False."
    )
    assert admin_instance.has_change_permission(request) is False, (
        "AdminAuditLogAdmin.has_change_permission() must return False."
    )
    assert admin_instance.has_delete_permission(request) is False, (
        "AdminAuditLogAdmin.has_delete_permission() must return False."
    )


# ---------------------------------------------------------------------------
# 6. test_hoa_portal_tenant_isolation
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_hoa_portal_tenant_isolation():
    """HOA admin from org A cannot access org B resident by pk — should get 404 or PermissionDenied."""
    from portal.views import resident_detail

    org_a = OrganizationFactory()
    org_b = OrganizationFactory()

    admin_a = UserFactory(organization=org_a, is_hoa_admin=True, status='active')
    resident_b = UserFactory(organization=org_b, status='active')

    # Admin from org A, but request.organization = org A — trying to access org B user by pk
    rf = RequestFactory()
    request = rf.get(f'/portal/residents/{resident_b.pk}/')
    request.user = admin_a
    request.organization = org_a  # org A context
    request.session = {}

    from django.http import Http404
    # resident_detail uses get_object_or_404(User, pk=pk, organization=org)
    # so accessing org_b's resident from org_a context must 404
    with pytest.raises(Http404):
        resident_detail(request, pk=resident_b.pk)


# ---------------------------------------------------------------------------
# 7. test_hoa_portal_requires_hoa_admin
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_hoa_portal_requires_hoa_admin():
    """A regular resident (is_hoa_admin=False) accessing portal views receives 403."""
    from portal.views import portal_home

    org = OrganizationFactory()
    resident = UserFactory(organization=org, is_hoa_admin=False, status='active')

    rf = RequestFactory()
    request = rf.get('/portal/')
    request.user = resident
    request.organization = org
    request.session = {}

    with pytest.raises(PermissionDenied):
        portal_home(request)


# ---------------------------------------------------------------------------
# 8. test_hoa_portal_wrong_org_rejected
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_hoa_portal_wrong_org_rejected():
    """HOA admin from org A cannot access portal views when request.organization is org B."""
    from portal.views import portal_home

    org_a = OrganizationFactory()
    org_b = OrganizationFactory()

    # admin_a belongs to org_a but the request context says org_b (wrong hostname)
    admin_a = UserFactory(organization=org_a, is_hoa_admin=True, status='active')

    rf = RequestFactory()
    request = rf.get('/portal/')
    request.user = admin_a
    request.organization = org_b  # org B hostname context
    request.session = {}

    # hoa_admin_required checks: user.organization == request.organization
    # admin_a.organization is org_a, but request.organization is org_b => PermissionDenied
    with pytest.raises(PermissionDenied):
        portal_home(request)


# ---------------------------------------------------------------------------
# 9. test_resident_approve_changes_status
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_resident_approve_changes_status():
    """resident_approve (POST) changes user.status from 'pending_approval' to 'active'."""
    from portal.views import resident_approve

    org = OrganizationFactory()
    admin = UserFactory(organization=org, is_hoa_admin=True, status='active')
    target = UserFactory(organization=org, status='pending_approval')

    request = _make_hoa_request(resident_approve, admin, org, method='POST', post_data={})
    resident_approve(request, pk=target.pk)

    target.refresh_from_db()
    assert target.status == 'active', (
        f"resident_approve should change status to 'active', got {target.status!r}"
    )


# ---------------------------------------------------------------------------
# 10. test_resident_block_changes_status
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_resident_block_changes_status():
    """resident_block (POST) changes user.status to 'blocked'."""
    from portal.views import resident_block

    org = OrganizationFactory()
    admin = UserFactory(organization=org, is_hoa_admin=True, status='active')
    target = UserFactory(organization=org, status='active')

    request = _make_hoa_request(resident_block, admin, org, method='POST', post_data={})
    resident_block(request, pk=target.pk)

    target.refresh_from_db()
    assert target.status == 'blocked', (
        f"resident_block should change status to 'blocked', got {target.status!r}"
    )


# ---------------------------------------------------------------------------
# 11. test_spot_approve_changes_status
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_spot_approve_changes_status():
    """spot_approve (POST) changes ParkingSpot.status from 'pending' to 'active'."""
    from portal.views import spot_approve

    org = OrganizationFactory()
    admin = UserFactory(organization=org, is_hoa_admin=True, status='active')
    owner = UserFactory(organization=org, status='active')
    spot = ParkingSpotFactory(organization=org, owner=owner, status='pending')

    request = _make_hoa_request(spot_approve, admin, org, method='POST', post_data={})
    spot_approve(request, pk=spot.pk)

    spot.refresh_from_db()
    assert spot.status == 'active', (
        f"spot_approve should change status to 'active', got {spot.status!r}"
    )


# ---------------------------------------------------------------------------
# 12. test_invite_create_uses_secrets
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_invite_create_uses_secrets():
    """Created Invite.code is non-empty and length > 20 (urlsafe token from secrets.token_urlsafe)."""
    from accounts.models import Invite
    from portal.views import invite_create

    org = OrganizationFactory()
    admin = UserFactory(organization=org, is_hoa_admin=True, status='active')

    rf = RequestFactory()
    request = rf.post('/portal/invites/create/', data={'max_uses': '1', 'unit_number': '', 'expires_at': ''})
    request.user = admin
    request.organization = org
    request.session = {}

    invite_create(request)

    invite = Invite.objects.filter(organization=org, issued_by=admin).order_by('-created_at').first()

    assert invite is not None, "No Invite was created."
    assert invite.code, "invite.code must be non-empty."
    assert len(invite.code) > 20, (
        f"invite.code length should be > 20 (urlsafe token), got {len(invite.code)}: {invite.code!r}"
    )


# ---------------------------------------------------------------------------
# 13. test_impersonation_blocked_for_superuser_target
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_impersonation_blocked_for_superuser_target():
    """impersonate_user action in UserAdmin is rejected when the target is a superuser."""
    from parkshare.admin_site import operator_admin_site
    from accounts.models import User

    mod = _load_operator_admin()
    UserAdmin = mod.UserAdmin

    org = OrganizationFactory()
    operator = UserFactory(
        organization=org,
        is_hoa_admin=True,
        is_staff=True,
        status='active',
    )
    operator.is_superuser = True
    operator.save(update_fields=['is_superuser'])

    superuser_target = UserFactory(
        organization=org,
        is_staff=True,
        status='active',
    )
    superuser_target.is_superuser = True
    superuser_target.save(update_fields=['is_superuser'])

    admin_instance = UserAdmin(model=User, admin_site=operator_admin_site)

    rf = RequestFactory()
    request = rf.get('/')
    request.user = operator
    request.session = {}

    # Simulate the Django admin messages framework
    from django.contrib.messages.storage.fallback import FallbackStorage
    request._messages = FallbackStorage(request)

    queryset = User.objects.filter(pk=superuser_target.pk)

    # impersonate_user should refuse and NOT set session['impersonating']
    admin_instance.impersonate_user(request, queryset)

    assert request.session.get('impersonating') is None, (
        "impersonate_user must NOT set session['impersonating'] when target is a superuser."
    )
