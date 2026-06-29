"""
Unit tests for accounts/management/commands/bootstrap_admin.py (issue #172).

Covers:
  - First run with --password-from-env: org + superuser + confirmed TOTP device,
    support_email defaults to --email.
  - First run with a generated password: command succeeds and the printed
    password authenticates the created user.
  - Idempotent re-run: no second user/org/device; existing password and status
    are unchanged.
  - Missing env password var -> CommandError.
  - Invalid email -> CommandError.
  - Hostname collision with a different org name -> uses existing org, warns,
    does not duplicate.
  - --print-totp-uri surfaces an otpauth:// URI in output.
  - Concurrent-create race: a unique_together collision raised by a concurrent
    invocation is caught (savepoint), and the command no-ops cleanly instead of
    crashing with an IntegrityError traceback.
"""

import re
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError

from accounts.models import EncryptedTOTPDevice, User
from parking.models import Organization

ADMIN_EMAIL = "admin@example.com"
ORG_NAME = "Maple Court HOA"
ORG_HOSTNAME = "maplecourt.example.com"


def _run(**overrides):
    """Invoke bootstrap_admin with sensible defaults; return captured stdout."""
    out = StringIO()
    kwargs = {
        "email": ADMIN_EMAIL,
        "org_name": ORG_NAME,
        "org_hostname": ORG_HOSTNAME,
        "stdout": out,
    }
    kwargs.update(overrides)
    call_command("bootstrap_admin", **kwargs)
    return out.getvalue()


@pytest.mark.django_db
def test_first_run_with_env_password(monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "s3cret-from-env!")
    # support_email omitted -> should default to the admin email.
    _run(password_from_env="ADMIN_PASSWORD")

    org = Organization.objects.get(hostname=ORG_HOSTNAME)
    assert org.name == ORG_NAME
    assert org.support_email == ADMIN_EMAIL  # defaulted to --email

    user = User.objects.get(organization=org, email=ADMIN_EMAIL)
    assert user.is_superuser is True
    assert user.is_staff is True
    assert user.is_hoa_admin is True
    assert user.status == "active"
    assert user.check_password("s3cret-from-env!")
    assert user.display_name == "admin"  # local-part of admin@example.com

    device = EncryptedTOTPDevice.objects.get(user=user)
    assert device.confirmed is True


@pytest.mark.django_db
def test_first_run_generates_password_that_authenticates():
    out = _run()  # no --password-from-env -> generate

    user = User.objects.get(email=ADMIN_EMAIL)
    # Extract the generated password printed once to stdout.
    m = re.search(r"store this now.*?\n\s+(\S+)", out, re.IGNORECASE | re.DOTALL)
    assert m, f"generated password not found in output:\n{out}"
    generated = m.group(1)
    assert user.check_password(generated)


@pytest.mark.django_db
def test_idempotent_rerun_does_not_mutate(monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "first-password!")
    _run(password_from_env="ADMIN_PASSWORD")

    user = User.objects.get(email=ADMIN_EMAIL)
    # Set a sentinel password + status to detect any mutation on re-run.
    user.set_password("sentinel-password-xyz")
    user.status = "active"
    user.save()

    # Re-run with a DIFFERENT env password — must be ignored (no reset).
    monkeypatch.setenv("ADMIN_PASSWORD", "second-password!")
    out = _run(password_from_env="ADMIN_PASSWORD")

    assert "nothing to do" in out
    assert Organization.objects.filter(hostname=ORG_HOSTNAME).count() == 1
    assert User.objects.filter(email=ADMIN_EMAIL).count() == 1
    assert EncryptedTOTPDevice.objects.filter(user=user).count() == 1

    user.refresh_from_db()
    assert user.check_password("sentinel-password-xyz")  # unchanged
    assert not user.check_password("second-password!")
    assert user.status == "active"


@pytest.mark.django_db
def test_missing_env_password_var_errors():
    # Var intentionally not set.
    with pytest.raises(CommandError):
        _run(password_from_env="DEFINITELY_UNSET_VAR_172")


@pytest.mark.django_db
def test_empty_env_password_var_errors(monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "")
    with pytest.raises(CommandError):
        _run(password_from_env="ADMIN_PASSWORD")


@pytest.mark.django_db
def test_invalid_email_errors():
    with pytest.raises(CommandError):
        _run(email="not-an-email")


@pytest.mark.django_db
def test_hostname_collision_uses_existing_org(monkeypatch):
    existing = Organization.objects.create(
        name="Pre-existing Name",
        hostname=ORG_HOSTNAME,
        support_email="support@maplecourt.example.com",
    )
    monkeypatch.setenv("ADMIN_PASSWORD", "pw!")
    out = _run(password_from_env="ADMIN_PASSWORD", org_name="Different Name")

    # Only one org; its name was NOT overwritten.
    assert Organization.objects.filter(hostname=ORG_HOSTNAME).count() == 1
    existing.refresh_from_db()
    assert existing.name == "Pre-existing Name"
    assert "different name" in out.lower()

    # The admin was created in the existing org.
    user = User.objects.get(email=ADMIN_EMAIL)
    assert user.organization_id == existing.id


@pytest.mark.django_db
def test_print_totp_uri_outputs_otpauth(monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "pw!")
    out = _run(password_from_env="ADMIN_PASSWORD", print_totp_uri=True)
    assert "otpauth://" in out
    assert "TOTP_OTPAUTH_URI=" in out


@pytest.mark.django_db
def test_concurrent_create_race_is_handled(monkeypatch):
    """A concurrent invocation winning the (org, email) insert race must result
    in a clean no-op, not an unhandled IntegrityError traceback.

    Real-race model: the *other* invocation commits its (org, email) row on a
    separate connection, so that row survives our nested-savepoint rollback.
    We reproduce this in a single connection by:
      1. pre-creating the conflicting "winner" row (it exists and is committed
         relative to the command's savepoint), then
      2. forcing the command's INITIAL existence check to see None (so it takes
         the create path), and
      3. making create_superuser raise IntegrityError once (the duplicate the
         DB would raise).
    The command must catch it (savepoint rolls back only the failed insert),
    re-query — now finding the winner row — and report the already-exists no-op
    with exit 0, without resetting the winner's password.
    """
    monkeypatch.setenv("ADMIN_PASSWORD", "pw!")

    org = Organization.objects.create(
        name=ORG_NAME,
        hostname=ORG_HOSTNAME,
        support_email=ADMIN_EMAIL,
    )
    # The concurrent winner's already-committed row.
    winner = User.objects.create_user(
        email=ADMIN_EMAIL,
        organization=org,
        display_name="winner",
        password="winner-pw",
    )

    # Force the command's initial `.filter(...).first()` to return None so it
    # believes it is the first run and proceeds to the create path. We patch
    # only the first call; the post-collision re-query uses the real queryset
    # and finds the winner row.
    real_filter = User.objects.filter
    state = {"first_check_done": False}

    def filter_seeing_no_user_first(*args, **kwargs):
        qs = real_filter(*args, **kwargs)
        if not state["first_check_done"] and kwargs.get("organization") == org and kwargs.get("email") == ADMIN_EMAIL:
            state["first_check_done"] = True
            return qs.none()  # initial existence check sees nothing
        return qs

    monkeypatch.setattr(User.objects, "filter", filter_seeing_no_user_first)

    real_create_superuser = User.objects.create_superuser

    def racing_create_superuser(*args, **kwargs):
        # The DB rejects the duplicate (org, email) — same as a concurrent loser.
        raise IntegrityError("duplicate key value violates unique constraint")

    monkeypatch.setattr(User.objects, "create_superuser", racing_create_superuser)

    # Must NOT raise — the race is handled and the command no-ops.
    out = _run(password_from_env="ADMIN_PASSWORD")

    assert state["first_check_done"] is True
    assert "nothing to do" in out
    # Exactly one user; the winner's password was NOT reset by the no-op path.
    assert User.objects.filter(organization=org, email=ADMIN_EMAIL).count() == 1
    winner.refresh_from_db()
    assert winner.check_password("winner-pw")

    _ = real_create_superuser  # referenced to document the restored original
