"""
Unit tests for CPS #78 — impersonation audit-log recovery.

Covers:
  1. Audit-log write failure → fail-open + structured recovery record emitted
     (target_type/target_id present in both the live row call and the record)
  2. backfill_audit_log reconstructs AdminAuditLog from a recovery JSONL file
     (created_at == attempted_at; target_type/target_id preserved)
  3. backfill_audit_log is idempotent — second run creates zero new rows
  4. Backfill rejects tampered records:
       - disallowed action
       - non-superuser actor
       - cross-tenant actor
"""

import json
import tempfile
from datetime import datetime, timezone as dt_timezone
from io import StringIO
from unittest.mock import call, patch

import pytest
from django.test import RequestFactory

# ---------------------------------------------------------------------------
# Factories / helpers
# ---------------------------------------------------------------------------


def _make_org(name, hostname):
    from parking.models import Organization

    return Organization.objects.create(
        name=name,
        hostname=hostname,
        support_email=f"support@{hostname}",
        registration_mode="invite_only",
        timezone="UTC",
        booking_horizon_baseline_days=3,
        booking_horizon_max_days=30,
        listing_to_horizon_ratio=10,
        tier_metric_window_days=180,
        launch_grace_days=14,
        launch_grace_horizon_days=14,
    )


def _make_user(org, email, is_superuser=False):
    from accounts.models import User

    return User.objects.create_user(
        email=email,
        organization=org,
        display_name=email.split("@")[0],
        password="test-password-secure!",
        is_superuser=is_superuser,
        is_staff=is_superuser,
        status="active",
    )


# ---------------------------------------------------------------------------
# Test 1a: fail-open + structured recovery record (mock-based)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_impersonation_audit_failure_is_fail_open_with_recovery_record():
    """
    When AdminAuditLog.objects.create raises, the middleware must:
    - still call get_response (fail-open)
    - emit a structured JSON recovery record via the 'audit_recovery' logger
    - the record must contain all required fields including target_type/target_id
    """
    import logging

    from parkshare.middleware import ImpersonationMiddleware

    org = _make_org("RecoveryOrg", "recoveryorg.example.com")
    operator = _make_user(org, "operator@recoveryorg.example.com", is_superuser=True)
    target = _make_user(org, "target@recoveryorg.example.com", is_superuser=False)

    response_called = []

    def fake_view(request):
        response_called.append(True)
        from django.http import HttpResponse

        return HttpResponse("ok")

    rf = RequestFactory()
    request = rf.post("/some/action/")
    request.user = operator
    request.organization = org
    request.session = {"impersonating": target.pk, "real_operator": operator.pk}

    middleware = ImpersonationMiddleware(fake_view)

    with patch("accounts.models.AdminAuditLog.objects.create") as mock_create:
        mock_create.side_effect = Exception("DB is down")
        with patch.object(
            logging.getLogger("audit_recovery"), "error"
        ) as mock_log_error:
            response = middleware(request)

    # Fail-open: the view was called and returned 200
    assert len(response_called) == 1, "get_response must be called even on audit failure"
    assert response.status_code == 200

    # Recovery record was emitted via the audit_recovery logger
    assert mock_log_error.called, "audit_recovery logger.error must be called"
    raw_message = mock_log_error.call_args[0][0]
    record = json.loads(raw_message)

    assert record["organization_id"] == org.pk
    assert record["actor_id"] == operator.pk
    assert record["on_behalf_of_id"] == target.pk
    assert record["action"] == "impersonate_action"
    assert record["target_type"] == "user"
    assert record["target_id"] == target.pk
    assert record["notes"] == "POST /some/action/"
    assert "attempted_at" in record
    # attempted_at must be an ISO-8601 UTC string
    parsed = datetime.fromisoformat(record["attempted_at"])
    assert parsed.tzinfo is not None, "attempted_at must be timezone-aware"


# ---------------------------------------------------------------------------
# Test 1b: fail-open recovery record — caplog variant
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_impersonation_audit_failure_uses_caplog(caplog):
    """
    Complementary test using pytest caplog to capture the 'audit_recovery' logger
    output, verifying the JSONL record is well-formed.

    The audit_recovery logger has propagate=False, so caplog's root-level handler
    does not see its records automatically. We temporarily install caplog's handler
    directly on the logger for the duration of this test.
    """
    import logging

    from parkshare.middleware import ImpersonationMiddleware

    org = _make_org("AssertLogsOrg", "assertlogsorg.example.com")
    operator = _make_user(org, "op@assertlogsorg.example.com", is_superuser=True)
    target = _make_user(org, "tgt@assertlogsorg.example.com", is_superuser=False)

    def fake_view(request):
        from django.http import HttpResponse

        return HttpResponse("ok")

    rf = RequestFactory()
    request = rf.post("/dashboard/")
    request.user = operator
    request.organization = org
    request.session = {"impersonating": target.pk}

    middleware = ImpersonationMiddleware(fake_view)

    audit_logger = logging.getLogger("audit_recovery")

    with patch("accounts.models.AdminAuditLog.objects.create") as mock_create:
        mock_create.side_effect = Exception("simulated failure")
        # audit_recovery has propagate=False; add caplog handler directly so
        # caplog.records captures the emission without relying on propagation.
        with caplog.at_level(logging.ERROR, logger="audit_recovery"):
            audit_logger.addHandler(caplog.handler)
            try:
                response = middleware(request)
            finally:
                audit_logger.removeHandler(caplog.handler)

    assert response.status_code == 200

    audit_records = [r for r in caplog.records if r.name == "audit_recovery"]
    assert len(audit_records) == 1, "Exactly one audit_recovery record expected"
    record = json.loads(audit_records[0].message)

    assert record["action"] == "impersonate_action"
    assert record["actor_id"] == operator.pk
    assert record["on_behalf_of_id"] == target.pk
    assert record["target_type"] == "user"
    assert record["target_id"] == target.pk
    assert record["notes"] == "POST /dashboard/"


# ---------------------------------------------------------------------------
# Test 1c: live create() receives target_type/target_id
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_live_audit_create_includes_target_fields():
    """
    On the happy path the live AdminAuditLog.objects.create call must include
    target_type='user' and target_id=<impersonated pk>.
    """
    from parkshare.middleware import ImpersonationMiddleware

    org = _make_org("LiveAuditOrg", "liveauditorg.example.com")
    operator = _make_user(org, "lv_op@liveauditorg.example.com", is_superuser=True)
    target = _make_user(org, "lv_tgt@liveauditorg.example.com", is_superuser=False)

    def fake_view(request):
        from django.http import HttpResponse

        return HttpResponse("ok")

    rf = RequestFactory()
    request = rf.post("/admin/action/")
    request.user = operator
    request.organization = org
    request.session = {"impersonating": target.pk}

    middleware = ImpersonationMiddleware(fake_view)

    with patch("accounts.models.AdminAuditLog.objects.create") as mock_create:
        from accounts.models import AdminAuditLog

        mock_create.return_value = AdminAuditLog.__new__(AdminAuditLog)
        middleware(request)

    assert mock_create.called
    kwargs = mock_create.call_args.kwargs
    assert kwargs["target_type"] == "user"
    assert kwargs["target_id"] == target.pk


# ---------------------------------------------------------------------------
# Test 2: backfill reconstructs the row (created_at == attempted_at)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_backfill_creates_audit_log_from_recovery_file():
    """
    backfill_audit_log must:
    - create an AdminAuditLog row from a recovery record
    - set created_at to the original attempted_at (not backfill run time)
    - preserve target_type and target_id
    - report created=1 skipped=0 rejected=0 malformed=0
    """
    from accounts.models import AdminAuditLog
    from django.core.management import call_command

    org = _make_org("BackfillOrg", "backfillorg.example.com")
    operator = _make_user(org, "bf_op@backfillorg.example.com", is_superuser=True)
    target = _make_user(org, "bf_tgt@backfillorg.example.com", is_superuser=False)

    attempted_at = "2026-06-14T12:00:00+00:00"

    recovery_record = {
        "organization_id": org.pk,
        "actor_id": operator.pk,
        "on_behalf_of_id": target.pk,
        "action": "impersonate_action",
        "target_type": "user",
        "target_id": target.pk,
        "notes": "POST /park/book/",
        "attempted_at": attempted_at,
    }

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonl", delete=False
    ) as f:
        f.write(json.dumps(recovery_record) + "\n")
        tmp_path = f.name

    initial_count = AdminAuditLog.objects.count()
    stdout = StringIO()
    call_command("backfill_audit_log", file=tmp_path, stdout=stdout)
    output = stdout.getvalue()

    assert "created=1" in output
    assert "skipped=0" in output
    assert "rejected=0" in output
    assert "malformed=0" in output

    new_count = AdminAuditLog.objects.count()
    assert new_count == initial_count + 1

    entry = AdminAuditLog.objects.filter(
        actor=operator, on_behalf_of=target, action="impersonate_action"
    ).last()
    assert entry is not None
    assert entry.organization == org

    # Notes carry the original path plus the recovery fingerprint
    assert "POST /park/book/" in entry.notes
    assert "recovered:attempted_at=" in entry.notes

    # target_type / target_id preserved
    assert entry.target_type == "user"
    assert entry.target_id == target.pk

    # created_at must equal the original attempted_at, not the backfill run time
    expected_at = datetime.fromisoformat(attempted_at).astimezone(dt_timezone.utc)
    entry.refresh_from_db()
    actual_at = entry.created_at.astimezone(dt_timezone.utc)
    assert actual_at == expected_at, (
        f"created_at {actual_at} must equal original attempted_at {expected_at}"
    )


# ---------------------------------------------------------------------------
# Test 3: backfill is idempotent
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_backfill_is_idempotent():
    """
    Running backfill_audit_log twice on the same recovery file must create the
    row only once. The second run must report created=0 skipped=1.
    """
    from accounts.models import AdminAuditLog
    from django.core.management import call_command

    org = _make_org("IdempotentOrg", "idempotentorg.example.com")
    operator = _make_user(org, "id_op@idempotentorg.example.com", is_superuser=True)
    target = _make_user(org, "id_tgt@idempotentorg.example.com", is_superuser=False)

    attempted_at = "2026-06-14T15:30:45+00:00"

    recovery_record = {
        "organization_id": org.pk,
        "actor_id": operator.pk,
        "on_behalf_of_id": target.pk,
        "action": "impersonate_action",
        "target_type": "user",
        "target_id": target.pk,
        "notes": "POST /settings/",
        "attempted_at": attempted_at,
    }

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonl", delete=False
    ) as f:
        f.write(json.dumps(recovery_record) + "\n")
        tmp_path = f.name

    stdout1 = StringIO()
    call_command("backfill_audit_log", file=tmp_path, stdout=stdout1)
    assert "created=1" in stdout1.getvalue()

    count_after_first = AdminAuditLog.objects.filter(
        actor=operator, action="impersonate_action"
    ).count()

    stdout2 = StringIO()
    call_command("backfill_audit_log", file=tmp_path, stdout=stdout2)
    output2 = stdout2.getvalue()

    assert "created=0" in output2, f"Expected created=0 on second run; got: {output2}"
    assert "skipped=1" in output2, f"Expected skipped=1 on second run; got: {output2}"

    count_after_second = AdminAuditLog.objects.filter(
        actor=operator, action="impersonate_action"
    ).count()
    assert count_after_second == count_after_first, (
        "Second backfill run must not create duplicate rows"
    )


# ---------------------------------------------------------------------------
# Test 4: backfill rejects tampered records
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_backfill_rejects_disallowed_action():
    """
    Backfill must skip and count as 'rejected' any record with an action not in
    the allowlist (only 'impersonate_action' is permitted).
    """
    from accounts.models import AdminAuditLog
    from django.core.management import call_command

    org = _make_org("RejectActionOrg", "rejectactionorg.example.com")
    operator = _make_user(org, "ra_op@rejectactionorg.example.com", is_superuser=True)

    tampered = {
        "organization_id": org.pk,
        "actor_id": operator.pk,
        "on_behalf_of_id": None,
        "action": "delete_user",  # not in allowlist
        "target_type": "user",
        "target_id": operator.pk,
        "notes": "tampered",
        "attempted_at": "2026-06-14T10:00:00+00:00",
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        f.write(json.dumps(tampered) + "\n")
        tmp_path = f.name

    before = AdminAuditLog.objects.count()
    stdout = StringIO()
    call_command("backfill_audit_log", file=tmp_path, stdout=stdout)
    output = stdout.getvalue()

    assert "created=0" in output
    assert "rejected=1" in output
    assert AdminAuditLog.objects.count() == before, "No rows must be created for rejected records"


@pytest.mark.django_db
def test_backfill_rejects_non_superuser_actor():
    """
    Backfill must reject records where the resolved actor is not a superuser,
    preventing privilege escalation via tampered JSONL.
    """
    from accounts.models import AdminAuditLog
    from django.core.management import call_command

    org = _make_org("RejectNonSuperOrg", "rejectnonsuperorg.example.com")
    regular_user = _make_user(org, "ordinary@rejectnonsuperorg.example.com", is_superuser=False)
    target = _make_user(org, "victim@rejectnonsuperorg.example.com", is_superuser=False)

    tampered = {
        "organization_id": org.pk,
        "actor_id": regular_user.pk,  # not a superuser
        "on_behalf_of_id": target.pk,
        "action": "impersonate_action",
        "target_type": "user",
        "target_id": target.pk,
        "notes": "POST /bad/",
        "attempted_at": "2026-06-14T11:00:00+00:00",
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        f.write(json.dumps(tampered) + "\n")
        tmp_path = f.name

    before = AdminAuditLog.objects.count()
    stdout = StringIO()
    call_command("backfill_audit_log", file=tmp_path, stdout=stdout)
    output = stdout.getvalue()

    assert "created=0" in output
    assert "rejected=1" in output
    assert AdminAuditLog.objects.count() == before


@pytest.mark.django_db
def test_backfill_rejects_cross_tenant_record():
    """
    Backfill must reject records where the actor's organization_id does not match
    the record's organization_id (cross-tenant forgery).
    """
    from accounts.models import AdminAuditLog
    from django.core.management import call_command

    org_a = _make_org("OrgAlpha", "orgalpha.example.com")
    org_b = _make_org("OrgBeta", "orgbeta.example.com")
    operator_a = _make_user(org_a, "op@orgalpha.example.com", is_superuser=True)

    # Record claims org_b but actor belongs to org_a
    tampered = {
        "organization_id": org_b.pk,
        "actor_id": operator_a.pk,
        "on_behalf_of_id": None,
        "action": "impersonate_action",
        "target_type": "user",
        "target_id": operator_a.pk,
        "notes": "POST /cross-tenant/",
        "attempted_at": "2026-06-14T09:00:00+00:00",
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        f.write(json.dumps(tampered) + "\n")
        tmp_path = f.name

    before = AdminAuditLog.objects.count()
    stdout = StringIO()
    call_command("backfill_audit_log", file=tmp_path, stdout=stdout)
    output = stdout.getvalue()

    assert "created=0" in output
    assert "rejected=1" in output
    assert AdminAuditLog.objects.count() == before
