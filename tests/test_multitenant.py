"""
Unit tests for CondoParkShare Step 2 — multi-tenancy.

Covers:
  1. Organization model fields and __str__
  2. OrganizationScopedManager returns empty queryset without org context
  3. TenantMiddleware sets request.organization from HTTP_HOST
  4. TenantMiddleware raises Http404 for unknown host
  5. TenantMiddleware clears thread-local after request (even on exception)
  6. ImpersonationMiddleware ignores 'impersonating' session key for non-superusers
  7. ImpersonationMiddleware blocks superuser-to-superuser impersonation
  8. OrganizationScopedManager filters to the active org only
"""

import pytest
from django.http import Http404
from django.test import RequestFactory, override_settings

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_org(name, hostname, **kwargs):
    """Create and persist an Organization, returning the instance."""
    from parking.models import Organization

    return Organization.objects.create(
        name=name,
        hostname=hostname,
        support_email=f"support@{hostname}",
        **kwargs,
    )


def _make_user(org, email, is_superuser=False):
    """Create a minimal User in *org*."""
    from accounts.models import User

    return User.objects.create_user(
        email=email,
        organization=org,
        display_name=email.split("@")[0],
        password="test-password",
        is_superuser=is_superuser,
        is_staff=is_superuser,
        status="active",
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_thread_local():
    """Ensure thread-local org is cleared before and after every test."""
    from parkshare.middleware import _thread_locals

    _thread_locals.organization = None
    yield
    _thread_locals.organization = None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_organization_fields():
    """Create an Organization with all required fields; assert values persist; __str__ returns name."""
    from parking.models import Organization

    org = Organization.objects.create(
        name="Bellevue Towers",
        hostname="bellevuetowers.parkshare.app",
        timezone="America/Los_Angeles",
        registration_mode="invite_only",
        unit_count=120,
        payer_model="free_forever",
        support_email="hoa@bellevuetowers.example.com",
        booking_buffer_hours=1,
        max_concurrent_bookings=2,
        max_booking_hours=72,
        booking_horizon_baseline_days=5,
        booking_horizon_max_days=30,
        listing_to_horizon_ratio=10,
        tier_metric_window_days=180,
        launch_grace_days=14,
        launch_grace_horizon_days=14,
    )

    # Reload from DB to confirm persistence
    org.refresh_from_db()

    assert org.name == "Bellevue Towers"
    assert org.hostname == "bellevuetowers.parkshare.app"
    assert org.timezone == "America/Los_Angeles"
    assert org.registration_mode == "invite_only"
    assert org.unit_count == 120
    assert org.payer_model == "free_forever"
    assert org.support_email == "hoa@bellevuetowers.example.com"
    assert org.booking_buffer_hours == 1
    assert org.max_concurrent_bookings == 2
    assert org.max_booking_hours == 72
    assert org.pk is not None
    assert org.created_at is not None
    assert org.updated_at is not None

    # __str__ must return the name
    assert str(org) == "Bellevue Towers"


@pytest.mark.django_db
def test_scoped_manager_returns_none_without_org():
    """
    OrganizationScopedManager.get_queryset() must return an empty queryset
    when there is no current organization in thread-local (i.e. outside a request).
    """
    from parking.models import ParkingSpot
    from parkshare.middleware import get_current_organization

    # Confirm no org is active
    assert get_current_organization() is None

    # Create an org and a spot so there IS data in the DB
    org = _make_org("TestOrg", "testorg.example.com")
    ParkingSpot.objects.create(
        organization=org,
        spot_number="P001",
        status="active",
    )

    # Scoped manager must return empty queryset
    qs = ParkingSpot.scoped.all()
    assert qs.count() == 0, (
        f"Expected empty queryset, got {qs.count()} spots. "
        "OrganizationScopedManager must call qs.none() when org is None."
    )


@pytest.mark.django_db
@override_settings(ALLOWED_HOSTS=["test.example.com"])
def test_tenant_middleware_sets_request_org():
    """
    TenantMiddleware must look up the Organization by HTTP_HOST and set
    request.organization on the request.
    """
    from parkshare.middleware import TenantMiddleware

    org = _make_org("MiddlewareOrg", "test.example.com")

    captured = {}

    def fake_view(request):
        captured["org"] = request.organization
        from django.http import HttpResponse

        return HttpResponse("ok")

    factory = RequestFactory()
    request = factory.get("/", HTTP_HOST="test.example.com")
    middleware = TenantMiddleware(fake_view)
    response = middleware(request)

    assert response.status_code == 200
    assert captured.get("org") is not None
    assert captured["org"].pk == org.pk
    assert captured["org"].hostname == "test.example.com"


@pytest.mark.django_db
@override_settings(ALLOWED_HOSTS=["unknown.example.com"])
def test_tenant_middleware_404_unknown_host():
    """TenantMiddleware must raise Http404 for a host that matches no Organization."""
    from parkshare.middleware import TenantMiddleware

    def fake_view(request):
        from django.http import HttpResponse

        return HttpResponse("ok")

    factory = RequestFactory()
    request = factory.get("/", HTTP_HOST="unknown.example.com")
    middleware = TenantMiddleware(fake_view)

    with pytest.raises(Http404):
        middleware(request)


@pytest.mark.django_db
@override_settings(ALLOWED_HOSTS=["clear.example.com"])
def test_tenant_middleware_clears_threadlocal():
    """
    After the request completes (including when the view raises), the thread-local
    organization must be None so subsequent requests on the same thread are not tainted.
    """
    from parkshare.middleware import TenantMiddleware, get_current_organization

    _make_org("ClearOrg", "clear.example.com")

    # --- Case 1: normal completion ---
    def normal_view(request):
        from django.http import HttpResponse

        return HttpResponse("ok")

    factory = RequestFactory()
    request = factory.get("/", HTTP_HOST="clear.example.com")
    TenantMiddleware(normal_view)(request)
    assert (
        get_current_organization() is None
    ), "Thread-local org must be cleared after a successful request."

    # --- Case 2: view raises an exception ---
    def raising_view(request):
        raise RuntimeError("view exploded")

    request2 = factory.get("/", HTTP_HOST="clear.example.com")
    with pytest.raises(RuntimeError):
        TenantMiddleware(raising_view)(request2)

    assert (
        get_current_organization() is None
    ), "Thread-local org must be cleared even when the view raises."


@pytest.mark.django_db
def test_impersonation_requires_superuser():
    """
    ImpersonationMiddleware must NOT apply impersonation when the acting user is
    not a superuser, even if session['impersonating'] is set.
    """
    from parkshare.middleware import ImpersonationMiddleware

    org = _make_org("ImpOrg1", "imporg1.example.com")
    regular_user = _make_user(org, "regular@imporg1.example.com", is_superuser=False)
    target_user = _make_user(org, "target@imporg1.example.com", is_superuser=False)

    captured = {}

    def fake_view(request):
        captured["user"] = request.user
        from django.http import HttpResponse

        return HttpResponse("ok")

    factory = RequestFactory()
    request = factory.get("/")
    request.user = regular_user
    # Simulate a session dict with 'impersonating' set
    request.session = {"impersonating": target_user.pk}

    middleware = ImpersonationMiddleware(fake_view)
    middleware(request)

    # The user should NOT have been swapped — regular_user is not a superuser
    assert (
        captured["user"].pk == regular_user.pk
    ), "Non-superuser should not be able to trigger impersonation."


@pytest.mark.django_db
def test_impersonation_cannot_impersonate_superuser():
    """
    ImpersonationMiddleware must block a superuser attempting to impersonate
    another superuser — request.user must remain the real operator.
    """
    from parkshare.middleware import ImpersonationMiddleware

    org = _make_org("ImpOrg2", "imporg2.example.com")
    operator = _make_user(org, "operator@imporg2.example.com", is_superuser=True)
    target_superuser = _make_user(org, "admin@imporg2.example.com", is_superuser=True)

    captured = {}

    def fake_view(request):
        captured["user"] = request.user
        from django.http import HttpResponse

        return HttpResponse("ok")

    factory = RequestFactory()
    request = factory.get("/")
    request.user = operator
    request.session = {"impersonating": target_superuser.pk}

    middleware = ImpersonationMiddleware(fake_view)
    middleware(request)

    # Impersonation of a superuser must be blocked; operator remains the active user
    assert (
        captured["user"].pk == operator.pk
    ), "Superuser-to-superuser impersonation must be blocked."
    # Session key must be cleared to prevent re-use
    assert (
        "impersonating" not in request.session
    ), "Session 'impersonating' key must be cleared when target is a superuser."


@pytest.mark.django_db
def test_scoped_manager_filters_by_org():
    """
    With the thread-local organization set to org1, ParkingSpot.scoped.all()
    must return only org1's spots and not org2's.
    """
    from parking.models import ParkingSpot
    from parkshare.middleware import _thread_locals

    org1 = _make_org("ScopedOrg1", "scopedorg1.example.com")
    org2 = _make_org("ScopedOrg2", "scopedorg2.example.com")

    spot1 = ParkingSpot.objects.create(
        organization=org1,
        spot_number="A101",
        status="active",
    )
    ParkingSpot.objects.create(
        organization=org2,
        spot_number="B202",
        status="active",
    )

    # Set thread-local to org1 (simulates what TenantMiddleware does)
    _thread_locals.organization = org1
    try:
        scoped_qs = ParkingSpot.scoped.all()
        pks = list(scoped_qs.values_list("pk", flat=True))
        assert spot1.pk in pks, "org1's spot must appear in scoped queryset"
        assert len(pks) == 1, (
            f"Expected 1 spot for org1, got {len(pks)}: {pks}. "
            "scoped manager must not return spots from other orgs."
        )
    finally:
        _thread_locals.organization = None
