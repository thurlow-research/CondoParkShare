"""
Smoke tests for the CondoParkShare Step 1 scaffold.

These tests verify that:
- Django system check passes
- All core models are importable
- Custom user model is configured correctly
- Middleware and managers exist and are importable
- All migrations are applied
"""

import os
import subprocess
import sys


def _manage_py():
    """Return the path to manage.py."""
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), "manage.py")


def _env():
    """Return environment dict with required vars set."""
    env = os.environ.copy()
    env.setdefault("DJANGO_SETTINGS_MODULE", "parkshare.settings.base")
    return env


def test_django_check():
    """Running `manage.py check` should exit with code 0."""
    result = subprocess.run(
        [sys.executable, _manage_py(), "check"],
        capture_output=True,
        text=True,
        env=_env(),
    )
    assert result.returncode == 0, (
        f"manage.py check failed (rc={result.returncode}):\n"
        f"STDOUT:\n{result.stdout}\n"
        f"STDERR:\n{result.stderr}"
    )


def test_models_importable():
    """All core models must be importable without error."""
    from accounts.models import AdminAuditLog, EmailOTP, Invite, User
    from notifications.models import RelayMessage, WebPushSubscription
    from parking.models import AvailabilityWindow, Booking, Organization, ParkingSpot

    # Assert all names resolve to classes (not None / import stub)
    for cls in (
        Organization,
        ParkingSpot,
        AvailabilityWindow,
        Booking,
        User,
        Invite,
        EmailOTP,
        AdminAuditLog,
        WebPushSubscription,
        RelayMessage,
    ):
        assert cls is not None, f"{cls!r} resolved to None"


def test_user_model_config():
    """AUTH_USER_MODEL must be accounts.User and USERNAME_FIELD must be 'email'."""
    from django.contrib.auth import get_user_model

    UserModel = get_user_model()
    assert (
        UserModel.__module__ == "accounts.models"
    ), f"Expected accounts.models, got {UserModel.__module__!r}"
    assert UserModel.__name__ == "User", f"Expected User, got {UserModel.__name__!r}"
    assert (
        UserModel.USERNAME_FIELD == "email"
    ), f"Expected USERNAME_FIELD='email', got {UserModel.USERNAME_FIELD!r}"


def test_tenant_middleware_exists():
    """TenantMiddleware must be importable from parkshare.middleware."""
    from parkshare.middleware import TenantMiddleware

    assert TenantMiddleware is not None
    # Verify it has the standard WSGI middleware interface
    assert callable(TenantMiddleware), "TenantMiddleware must be callable"


def test_scoped_manager_exists():
    """OrganizationScopedManager must be importable from parkshare.managers."""
    from parkshare.managers import OrganizationScopedManager

    assert OrganizationScopedManager is not None
    assert callable(
        OrganizationScopedManager
    ), "OrganizationScopedManager must be callable"


def test_migrations_complete():
    """All migrations must be applied — `manage.py migrate --check` must exit 0."""
    result = subprocess.run(
        [sys.executable, _manage_py(), "migrate", "--check"],
        capture_output=True,
        text=True,
        env=_env(),
    )
    assert result.returncode == 0, (
        f"manage.py migrate --check failed (rc={result.returncode}):\n"
        f"STDOUT:\n{result.stdout}\n"
        f"STDERR:\n{result.stderr}\n\n"
        "Run `manage.py migrate` to apply pending migrations."
    )
