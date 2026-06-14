"""
System tests — Impersonation audit-recovery mechanism (SPEC-1 §8, §9; CPS #78).

Exercises the full HTTP request/response cycle for the operator impersonation
audit trail, including:

  1. Happy path: superuser operator impersonates a resident and performs a
     real state-changing POST → AdminAuditLog row created with correct fields.

  2. Fail-open + recovery: audit DB write forced to raise → POST still
     succeeds (fail-open); structured JSONL recovery record written with ALL
     required fields.

  3. Recovery → backfill reconciliation: run backfill_audit_log against the
     JSONL written in scenario 2 → AdminAuditLog row present with
     created_at == attempted_at (original incident time, not backfill time).

  4. Idempotency: run backfill twice → exactly one row; second run reports
     created=0 skipped=1.

  5. Anti-forgery: JSONL records with (a) disallowed action, (b) non-superuser
     actor, (c) cross-tenant org → backfill creates none; reports all as
     rejected.

Design note on OTP / superuser admin console access:
  The SuperuserAdminSite.has_permission() check requires is_verified() (TOTP),
  so driving the impersonate_user Django admin action through the full HTTP
  path would require a real TOTP device attached to the operator and a
  valid code.  That is impractical in a non-interactive test without a live
  TOTP device.

  Instead, all tests set the session keys directly — exactly as the
  operator_console/admin.py impersonate_user action does:
      request.session["impersonating"] = user.pk
      request.session["real_operator"] = request.user.pk
  The ImpersonationMiddleware only reads these session keys; it does not
  re-verify TOTP.  The session-setup path therefore covers the middleware at
  the highest practical system level without a Selenium/TOTP harness.
"""

import json
import logging
import tempfile
from datetime import datetime, timezone as dt_timezone
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.test import Client, override_settings
from psycopg2.extras import DateTimeTZRange

from tests.system.conftest import (
    client_post,
    make_org,
    make_user,
)

# ---------------------------------------------------------------------------
# Hostname for tenant resolution during system tests
# ---------------------------------------------------------------------------

HOSTNAME = "impersonation-audit.parkshare.test"

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def org(db):
    return make_org("ImpersonationAuditOrg", HOSTNAME)


@pytest.fixture
def operator(org):
    """Superuser / platform operator — the actor in impersonation."""
    from accounts.models import User

    return User.objects.create_user(
        email="operator@impersonation-audit.parkshare.test",
        organization=org,
        display_name="Operator",
        password="OperatorPass-Secure-1!",
        is_superuser=True,
        is_staff=True,
        status="active",
    )


@pytest.fixture
def resident(org):
    """Regular active resident — the target of impersonation."""
    return make_user(
        org,
        "resident@impersonation-audit.parkshare.test",
        display_name="Resident",
    )


@pytest.fixture
def spot(org, resident):
    """A parking spot owned by the resident (used as booking target)."""
    from parking.models import ParkingSpot

    return ParkingSpot.objects.create(
        organization=org,
        owner=resident,
        spot_number="IMP001",
        status="active",
    )


@pytest.fixture
def confirmed_booking(org, spot, resident):
    """A confirmed booking that the resident-as-borrower can cancel."""
    from datetime import timezone as dt_timezone
    from datetime import datetime

    start = datetime(2029, 7, 10, 10, 0, tzinfo=dt_timezone.utc)
    end = datetime(2029, 7, 10, 14, 0, tzinfo=dt_timezone.utc)

    from parking.models import Booking

    return Booking.objects.create(
        organization=org,
        spot=spot,
        borrower=resident,
        time_range=DateTimeTZRange(start, end),
        status="confirmed",
    )


# ---------------------------------------------------------------------------
# Helper: set up an operator client that is impersonating a resident.
#
# We force-login the operator (bypassing TOTP as documented above), then
# manually write the session keys that operator_console/admin.py sets when
# the impersonate_user action fires.  The ImpersonationMiddleware reads these
# keys and swaps request.user accordingly.
# ---------------------------------------------------------------------------


def _impersonation_client(operator, resident, hostname):
    """
    Return a Django test Client that is:
      - force-logged in as *operator* (a superuser)
      - has session['impersonating'] = resident.pk
      - has session['real_operator'] = operator.pk

    The ImpersonationMiddleware will then treat every request as if the
    operator is acting on behalf of *resident*.
    """
    client = Client()
    client.force_login(operator)
    session = client.session
    session["impersonating"] = resident.pk
    session["real_operator"] = operator.pk
    session.save()
    return client


# ---------------------------------------------------------------------------
# Scenario 1 — Happy path: real POST → AdminAuditLog row created
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@override_settings(ALLOWED_HOSTS=[HOSTNAME])
def test_happy_path_impersonation_post_creates_audit_log(
    org, operator, resident, spot, confirmed_booking
):
    """
    Happy path: operator impersonates resident and POSTs to a state-changing
    endpoint (booking_cancel). ImpersonationMiddleware must create an
    AdminAuditLog row with:
      - actor = operator
      - on_behalf_of = resident
      - action = "impersonate_action"
      - target_type = "user"
      - target_id = resident.pk
      - notes = "POST /bookings/<pk>/cancel/"

    SPEC-1 §8: admin audit log; §9: operator console.
    """
    from accounts.models import AdminAuditLog

    before_count = AdminAuditLog.objects.filter(action="impersonate_action").count()

    client = _impersonation_client(operator, resident, HOSTNAME)

    # booking_cancel requires the request to come from borrower or spot owner.
    # The middleware replaces request.user with `resident` (the borrower), so
    # the cancel view's permission check passes.
    cancel_url = f"/bookings/{confirmed_booking.pk}/cancel/"
    response = client_post(client, HOSTNAME, cancel_url, data={"reason": ""})

    # The view either redirects (success) or renders a form.
    # We care only that it did NOT 5xx.
    assert response.status_code in (302, 200), (
        f"Expected 200 or 302 from booking_cancel, got {response.status_code}"
    )

    # AdminAuditLog row must exist with the correct fields.
    after_count = AdminAuditLog.objects.filter(action="impersonate_action").count()
    assert after_count == before_count + 1, (
        "Expected one new AdminAuditLog row with action='impersonate_action'"
    )

    entry = AdminAuditLog.objects.filter(action="impersonate_action").latest("created_at")
    assert entry.actor_id == operator.pk, (
        f"actor must be the operator (pk={operator.pk}), got {entry.actor_id}"
    )
    assert entry.on_behalf_of_id == resident.pk, (
        f"on_behalf_of must be the resident (pk={resident.pk}), "
        f"got {entry.on_behalf_of_id}"
    )
    assert entry.target_type == "user", (
        f"target_type must be 'user', got {entry.target_type!r}"
    )
    assert entry.target_id == resident.pk, (
        f"target_id must equal resident.pk ({resident.pk}), got {entry.target_id}"
    )
    assert entry.notes == f"POST {cancel_url}", (
        # middleware sets notes = "POST <request.path>"
        f"notes must be 'POST {cancel_url}', got {entry.notes!r}"
    )


# ---------------------------------------------------------------------------
# Scenario 2 — Fail-open + recovery record written to JSONL sink
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@override_settings(ALLOWED_HOSTS=[HOSTNAME])
def test_fail_open_recovery_record_written_on_audit_db_failure(
    org, operator, resident, spot, confirmed_booking, tmp_path
):
    """
    When AdminAuditLog.objects.create raises during impersonation POST:
      (a) The POST still succeeds (fail-open — no 5xx from the audit failure).
      (b) A recovery record with ALL required fields is written to the JSONL
          sink (settings.AUDIT_RECOVERY_LOG → tmp file).

    Required fields per ImpersonationMiddleware:
      organization_id, actor_id, on_behalf_of_id, action, target_type,
      target_id, notes, attempted_at

    SPEC-1 §8 (audit log), §9 (operator console); CPS #78.
    """
    recovery_file = tmp_path / "audit-recovery.jsonl"

    # Override both the setting and the logging handler so the JSONL goes to
    # our tmp file rather than logs/audit-recovery.jsonl.
    logging_override = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "plain": {"format": "%(message)s"},
        },
        "handlers": {
            "audit_recovery_file": {
                "class": "logging.FileHandler",
                "filename": str(recovery_file),
                "formatter": "plain",
            },
        },
        "loggers": {
            "audit_recovery": {
                "handlers": ["audit_recovery_file"],
                "level": "ERROR",
                "propagate": False,
            },
        },
    }

    cancel_url = f"/bookings/{confirmed_booking.pk}/cancel/"

    with override_settings(
        AUDIT_RECOVERY_LOG=str(recovery_file),
        LOGGING=logging_override,
    ):
        # Re-configure logging so the override takes effect immediately.
        import logging.config

        logging.config.dictConfig(logging_override)

        client = _impersonation_client(operator, resident, HOSTNAME)

        with patch("accounts.models.AdminAuditLog.objects.create") as mock_create:
            mock_create.side_effect = Exception("simulated DB failure")
            response = client_post(
                client, HOSTNAME, cancel_url, data={"reason": ""}
            )

    # (a) Fail-open: response must not be a 5xx error.
    assert response.status_code in (302, 200), (
        f"Expected 200 or 302 (fail-open), got {response.status_code}"
    )

    # (b) Recovery record must exist in the JSONL file.
    assert recovery_file.exists(), (
        f"Recovery JSONL file was not created at {recovery_file}"
    )

    lines = [
        line.strip()
        for line in recovery_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(lines) >= 1, "Expected at least one recovery record in the JSONL file"

    # Parse the last record (most recent write).
    record = json.loads(lines[-1])

    assert record["organization_id"] == org.pk, (
        f"organization_id must be {org.pk}, got {record.get('organization_id')}"
    )
    assert record["actor_id"] == operator.pk, (
        f"actor_id must be {operator.pk}, got {record.get('actor_id')}"
    )
    assert record["on_behalf_of_id"] == resident.pk, (
        f"on_behalf_of_id must be {resident.pk}, got {record.get('on_behalf_of_id')}"
    )
    assert record["action"] == "impersonate_action", (
        f"action must be 'impersonate_action', got {record.get('action')!r}"
    )
    assert record["target_type"] == "user", (
        f"target_type must be 'user', got {record.get('target_type')!r}"
    )
    assert record["target_id"] == resident.pk, (
        f"target_id must be {resident.pk}, got {record.get('target_id')}"
    )
    expected_notes = f"POST {cancel_url}"
    assert record["notes"] == expected_notes, (
        f"notes must be {expected_notes!r}, got {record.get('notes')!r}"
    )
    assert "attempted_at" in record, "Recovery record must contain 'attempted_at'"
    parsed_at = datetime.fromisoformat(record["attempted_at"])
    assert parsed_at.tzinfo is not None, "attempted_at must be timezone-aware"


# ---------------------------------------------------------------------------
# Scenario 3 — Recovery → backfill reconciliation
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@override_settings(ALLOWED_HOSTS=[HOSTNAME])
def test_backfill_reconciliation_creates_row_with_correct_created_at(
    org, operator, resident, spot, confirmed_booking, tmp_path
):
    """
    After a fail-open scenario writes a recovery record, running
    backfill_audit_log --file <tmp_jsonl> must:
      - Create one AdminAuditLog row.
      - Set created_at == attempted_at (the original incident time, not
        the backfill run time).
      - Preserve actor, on_behalf_of, action, target_type, target_id,
        organization fields exactly as in the happy-path row.

    SPEC-1 §8; CPS #78 backfill reconciliation.
    """
    from accounts.models import AdminAuditLog

    # Craft a recovery record manually — this is what the middleware would
    # have written if the DB write had failed during an impersonation POST.
    attempted_at_str = "2029-07-10T11:30:00+00:00"
    cancel_url = f"/bookings/{confirmed_booking.pk}/cancel/"

    recovery_record = {
        "organization_id": org.pk,
        "actor_id": operator.pk,
        "on_behalf_of_id": resident.pk,
        "action": "impersonate_action",
        "target_type": "user",
        "target_id": resident.pk,
        "notes": f"POST {cancel_url}",
        "attempted_at": attempted_at_str,
    }

    jsonl_file = tmp_path / "audit-recovery.jsonl"
    jsonl_file.write_text(json.dumps(recovery_record) + "\n", encoding="utf-8")

    before_count = AdminAuditLog.objects.filter(action="impersonate_action").count()

    stdout = StringIO()
    call_command("backfill_audit_log", file=str(jsonl_file), stdout=stdout)
    output = stdout.getvalue()

    assert "created=1" in output, f"Expected 'created=1' in backfill output, got: {output}"
    assert "skipped=0" in output
    assert "rejected=0" in output
    assert "malformed=0" in output

    after_count = AdminAuditLog.objects.filter(action="impersonate_action").count()
    assert after_count == before_count + 1, "Backfill must create exactly one new row"

    entry = AdminAuditLog.objects.filter(
        actor_id=operator.pk,
        on_behalf_of_id=resident.pk,
        action="impersonate_action",
    ).latest("created_at")

    # Field-identical assertions (matches what the happy-path produces)
    assert entry.organization == org
    assert entry.actor_id == operator.pk
    assert entry.on_behalf_of_id == resident.pk
    assert entry.action == "impersonate_action"
    assert entry.target_type == "user"
    assert entry.target_id == resident.pk

    # created_at must equal attempted_at (original incident time)
    expected_at = datetime.fromisoformat(attempted_at_str).astimezone(dt_timezone.utc)
    actual_at = entry.created_at.astimezone(dt_timezone.utc)
    assert actual_at == expected_at, (
        f"created_at {actual_at} must equal original attempted_at {expected_at}"
    )


# ---------------------------------------------------------------------------
# Scenario 4 — Idempotency: backfill twice → one row, second run creates=0
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@override_settings(ALLOWED_HOSTS=[HOSTNAME])
def test_backfill_idempotency_second_run_creates_zero_rows(
    org, operator, resident, tmp_path
):
    """
    Running backfill_audit_log twice on the same JSONL file must:
      - Create the row exactly once.
      - On the second run, report created=0, skipped=1.

    SPEC-1 §8; CPS #78 idempotency.
    """
    from accounts.models import AdminAuditLog

    attempted_at_str = "2029-07-11T09:15:00+00:00"
    recovery_record = {
        "organization_id": org.pk,
        "actor_id": operator.pk,
        "on_behalf_of_id": resident.pk,
        "action": "impersonate_action",
        "target_type": "user",
        "target_id": resident.pk,
        "notes": "POST /bookings/99/cancel/",
        "attempted_at": attempted_at_str,
    }

    jsonl_file = tmp_path / "idempotent.jsonl"
    jsonl_file.write_text(json.dumps(recovery_record) + "\n", encoding="utf-8")

    # First run
    stdout1 = StringIO()
    call_command("backfill_audit_log", file=str(jsonl_file), stdout=stdout1)
    output1 = stdout1.getvalue()
    assert "created=1" in output1, f"First run must create=1; got: {output1}"

    count_after_first = AdminAuditLog.objects.filter(
        actor_id=operator.pk,
        action="impersonate_action",
        on_behalf_of_id=resident.pk,
    ).count()

    # Second run (same file)
    stdout2 = StringIO()
    call_command("backfill_audit_log", file=str(jsonl_file), stdout=stdout2)
    output2 = stdout2.getvalue()
    assert "created=0" in output2, f"Second run must report created=0; got: {output2}"
    assert "skipped=1" in output2, f"Second run must report skipped=1; got: {output2}"

    count_after_second = AdminAuditLog.objects.filter(
        actor_id=operator.pk,
        action="impersonate_action",
        on_behalf_of_id=resident.pk,
    ).count()

    assert count_after_second == count_after_first, (
        "Second backfill run must not create duplicate rows: "
        f"count went from {count_after_first} to {count_after_second}"
    )


# ---------------------------------------------------------------------------
# Scenario 5a — Anti-forgery: disallowed action
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@override_settings(ALLOWED_HOSTS=[HOSTNAME])
def test_backfill_anti_forgery_disallowed_action(org, operator, resident, tmp_path):
    """
    Backfill must reject JSONL records with an action not in the allowlist
    ({impersonate_action}), and report them as rejected=1.

    CPS #78 anti-forgery; SPEC-1 §8.
    """
    from accounts.models import AdminAuditLog

    tampered = {
        "organization_id": org.pk,
        "actor_id": operator.pk,
        "on_behalf_of_id": resident.pk,
        "action": "delete_all_users",  # not in allowlist
        "target_type": "user",
        "target_id": resident.pk,
        "notes": "POST /destroy/",
        "attempted_at": "2029-07-12T08:00:00+00:00",
    }

    jsonl_file = tmp_path / "tampered-action.jsonl"
    jsonl_file.write_text(json.dumps(tampered) + "\n", encoding="utf-8")

    before = AdminAuditLog.objects.count()
    stdout = StringIO()
    call_command("backfill_audit_log", file=str(jsonl_file), stdout=stdout)
    output = stdout.getvalue()

    assert "created=0" in output, f"Expected created=0 for disallowed action; got: {output}"
    assert "rejected=1" in output, f"Expected rejected=1 for disallowed action; got: {output}"
    assert AdminAuditLog.objects.count() == before, (
        "No AdminAuditLog rows must be created for a tampered/disallowed action"
    )


# ---------------------------------------------------------------------------
# Scenario 5b — Anti-forgery: non-superuser actor
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@override_settings(ALLOWED_HOSTS=[HOSTNAME])
def test_backfill_anti_forgery_non_superuser_actor(org, resident, tmp_path):
    """
    Backfill must reject records where the actor is not a superuser.

    This prevents privilege escalation: an attacker who can write to the JSONL
    file cannot impersonate themselves as having performed audit-worthy
    operator actions.

    CPS #78 anti-forgery; SPEC-1 §8.
    """
    from accounts.models import AdminAuditLog

    # resident is a regular active user, not a superuser
    assert not resident.is_superuser

    tampered = {
        "organization_id": org.pk,
        "actor_id": resident.pk,  # non-superuser
        "on_behalf_of_id": None,
        "action": "impersonate_action",
        "target_type": "user",
        "target_id": resident.pk,
        "notes": "POST /some/path/",
        "attempted_at": "2029-07-12T09:00:00+00:00",
    }

    jsonl_file = tmp_path / "tampered-nonsuperuser.jsonl"
    jsonl_file.write_text(json.dumps(tampered) + "\n", encoding="utf-8")

    before = AdminAuditLog.objects.count()
    stdout = StringIO()
    call_command("backfill_audit_log", file=str(jsonl_file), stdout=stdout)
    output = stdout.getvalue()

    assert "created=0" in output, (
        f"Expected created=0 for non-superuser actor; got: {output}"
    )
    assert "rejected=1" in output, (
        f"Expected rejected=1 for non-superuser actor; got: {output}"
    )
    assert AdminAuditLog.objects.count() == before, (
        "No rows must be created when the actor is not a superuser"
    )


# ---------------------------------------------------------------------------
# Scenario 5c — Anti-forgery: cross-tenant organization_id
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@override_settings(ALLOWED_HOSTS=[HOSTNAME])
def test_backfill_anti_forgery_cross_tenant_org(org, operator, tmp_path, db):
    """
    Backfill must reject records where organization_id does not match the
    actor's own organization, preventing cross-tenant log injection.

    CPS #78 anti-forgery; SPEC-1 §8.
    """
    from accounts.models import AdminAuditLog

    # Create a second org (different tenant)
    other_org = make_org("OtherAuditOrg", "other-audit.parkshare.test")

    # Record claims other_org but actor belongs to org
    tampered = {
        "organization_id": other_org.pk,  # cross-tenant
        "actor_id": operator.pk,           # actor belongs to org, not other_org
        "on_behalf_of_id": None,
        "action": "impersonate_action",
        "target_type": "user",
        "target_id": operator.pk,
        "notes": "POST /cross-tenant/action/",
        "attempted_at": "2029-07-12T10:00:00+00:00",
    }

    jsonl_file = tmp_path / "tampered-crosstenant.jsonl"
    jsonl_file.write_text(json.dumps(tampered) + "\n", encoding="utf-8")

    before = AdminAuditLog.objects.count()
    stdout = StringIO()
    call_command("backfill_audit_log", file=str(jsonl_file), stdout=stdout)
    output = stdout.getvalue()

    assert "created=0" in output, (
        f"Expected created=0 for cross-tenant record; got: {output}"
    )
    assert "rejected=1" in output, (
        f"Expected rejected=1 for cross-tenant record; got: {output}"
    )
    assert AdminAuditLog.objects.count() == before, (
        "No rows must be created for cross-tenant tampered records"
    )


# ---------------------------------------------------------------------------
# Scenario 6 — End-to-end round-trip: fail-open → JSONL sink → backfill
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@override_settings(ALLOWED_HOSTS=[HOSTNAME])
def test_end_to_end_fail_open_then_backfill_produces_correct_row(
    org, operator, resident, spot, confirmed_booking, tmp_path
):
    """
    Full end-to-end round-trip:
      1. Make a real impersonated POST; force AdminAuditLog.objects.create to
         raise → verify fail-open (no 5xx) + JSONL recovery record written.
      2. Run backfill_audit_log against the written JSONL.
      3. Assert the AdminAuditLog row is field-identical to what the happy
         path would have produced, with created_at == attempted_at.

    This is the most important integration scenario — it proves the complete
    fail-open → recovery → backfill pipeline works as a unit.

    SPEC-1 §8; CPS #78.
    """
    from accounts.models import AdminAuditLog

    recovery_file = tmp_path / "e2e-recovery.jsonl"

    logging_override = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "plain": {"format": "%(message)s"},
        },
        "handlers": {
            "audit_recovery_file": {
                "class": "logging.FileHandler",
                "filename": str(recovery_file),
                "formatter": "plain",
            },
        },
        "loggers": {
            "audit_recovery": {
                "handlers": ["audit_recovery_file"],
                "level": "ERROR",
                "propagate": False,
            },
        },
    }

    cancel_url = f"/bookings/{confirmed_booking.pk}/cancel/"

    before_count = AdminAuditLog.objects.filter(action="impersonate_action").count()

    # Step 1: Perform impersonated POST with forced DB failure.
    with override_settings(
        AUDIT_RECOVERY_LOG=str(recovery_file),
        LOGGING=logging_override,
    ):
        import logging.config

        logging.config.dictConfig(logging_override)

        client = _impersonation_client(operator, resident, HOSTNAME)

        with patch("accounts.models.AdminAuditLog.objects.create") as mock_create:
            mock_create.side_effect = Exception("DB unavailable")
            response = client_post(
                client, HOSTNAME, cancel_url, data={"reason": ""}
            )

    # Fail-open: no 5xx
    assert response.status_code in (302, 200), (
        f"Expected 200 or 302 (fail-open), got {response.status_code}"
    )

    # JSONL file must have been written
    assert recovery_file.exists(), "Recovery JSONL file must exist after fail-open"
    lines = [
        l.strip()
        for l in recovery_file.read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]
    assert lines, "Recovery file must not be empty"

    # Parse the recovery record and capture attempted_at for comparison
    record = json.loads(lines[-1])
    attempted_at_str = record["attempted_at"]
    assert record["action"] == "impersonate_action"

    # Step 2: Backfill
    stdout = StringIO()
    call_command("backfill_audit_log", file=str(recovery_file), stdout=stdout)
    output = stdout.getvalue()
    assert "created=1" in output, f"Backfill must create=1; got: {output}"

    # Step 3: Verify the backfilled row
    after_count = AdminAuditLog.objects.filter(action="impersonate_action").count()
    assert after_count == before_count + 1

    entry = AdminAuditLog.objects.filter(
        actor_id=operator.pk,
        on_behalf_of_id=resident.pk,
        action="impersonate_action",
    ).latest("created_at")

    assert entry.organization == org
    assert entry.actor_id == operator.pk
    assert entry.on_behalf_of_id == resident.pk
    assert entry.target_type == "user"
    assert entry.target_id == resident.pk

    # created_at must equal the attempted_at from the recovery record
    expected_at = datetime.fromisoformat(attempted_at_str).astimezone(dt_timezone.utc)
    actual_at = entry.created_at.astimezone(dt_timezone.utc)
    assert actual_at == expected_at, (
        f"Backfilled created_at {actual_at} must equal "
        f"attempted_at from recovery record {expected_at}"
    )

    # Verify notes contain the original path and the recovery fingerprint
    assert cancel_url in entry.notes, (
        f"notes must contain the original path {cancel_url!r}"
    )
    assert "recovered:attempted_at=" in entry.notes, (
        "notes must contain the recovery fingerprint 'recovered:attempted_at='"
    )
