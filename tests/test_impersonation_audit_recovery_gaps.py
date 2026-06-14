"""
Gap-filling unit tests for the audit-recovery change (PR #88).

Covers lines not reached by the existing 16 tests:

  middleware.py:
    - TenantMiddleware: Organization.DoesNotExist → Http404 (lines 53-56)
    - RatelimitMiddleware.process_exception: Ratelimited branch (lines 84-89)
    - RatelimitMiddleware.process_exception: non-Ratelimited → None (line 90)
    - ImpersonationMiddleware: User.DoesNotExist → session cleared (lines 118-121)
    - ImpersonationMiddleware: superuser-to-superuser → blocked (lines 124-127)

  backfill_audit_log.py:
    - FileNotFoundError on missing recovery file (lines 68-72)
    - Blank/empty line in JSONL → silently skipped (line 77)
    - Malformed JSON line → malformed_count++ (lines 94-101)
    - Naive attempted_at (no tzinfo) → make_aware() branch (line 116)
    - Unparseable attempted_at string → malformed_count++ (lines 119-126)
    - actor_id references non-existent User → malformed_count++ (lines 130-137)
    - on_behalf_of user not found → row still created with on_behalf_of=None (lines 181-182)
    - organization_id references non-existent Organization → org=None (lines 193-194)
"""

import json
import tempfile
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest
from django.test import RequestFactory


# ---------------------------------------------------------------------------
# Shared helpers
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


def _fake_view(request):
    from django.http import HttpResponse

    return HttpResponse("ok")


# ===========================================================================
# TenantMiddleware — Organization.DoesNotExist → Http404
# ===========================================================================


@pytest.mark.django_db
def test_tenant_middleware_raises_http404_for_unknown_hostname():
    """
    TenantMiddleware must raise Http404 when no Organization matches the
    request hostname (middleware.py lines 53-56).
    """
    from django.http import Http404
    from django.test import override_settings

    from parkshare.middleware import TenantMiddleware

    middleware = TenantMiddleware(_fake_view)
    rf = RequestFactory()
    # SERVER_NAME is an unknown hostname — no org in DB matches it.
    # override_settings(ALLOWED_HOSTS=...) so Django does not reject the host
    # before TenantMiddleware even runs.
    with override_settings(ALLOWED_HOSTS=["no-such-host.example.com"]):
        request = rf.get("/", SERVER_NAME="no-such-host.example.com")
        with pytest.raises(Http404):
            middleware(request)


# ===========================================================================
# RatelimitMiddleware — process_exception branches
# ===========================================================================


def test_ratelimit_middleware_returns_429_for_ratelimited_exception():
    """
    RatelimitMiddleware.process_exception must return a 429 response when the
    exception is a Ratelimited instance (middleware.py lines 84-89).

    django.shortcuts.render is imported locally inside process_exception, so
    the correct patch target is 'django.shortcuts.render'.
    """
    from django_ratelimit.exceptions import Ratelimited

    from parkshare.middleware import RatelimitMiddleware

    middleware = RatelimitMiddleware(_fake_view)
    rf = RequestFactory()
    request = rf.get("/some/path/")

    with patch("django.shortcuts.render") as mock_render:
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_render.return_value = mock_response

        response = middleware.process_exception(request, Ratelimited())

    assert response is not None
    assert response.status_code == 429


def test_ratelimit_middleware_returns_none_for_non_ratelimited_exception():
    """
    RatelimitMiddleware.process_exception must return None for any exception
    that is NOT a Ratelimited instance (middleware.py line 90).
    """
    from parkshare.middleware import RatelimitMiddleware

    middleware = RatelimitMiddleware(_fake_view)
    rf = RequestFactory()
    request = rf.get("/some/path/")

    result = middleware.process_exception(request, ValueError("unrelated error"))

    assert result is None


# ===========================================================================
# ImpersonationMiddleware — User.DoesNotExist clears session
# ===========================================================================


@pytest.mark.django_db
def test_impersonation_middleware_clears_session_when_target_not_found():
    """
    When session['impersonating'] contains a pk that no longer exists in the
    DB, ImpersonationMiddleware must clear the session keys and continue
    processing the request without crashing (middleware.py lines 118-121).
    """
    from parkshare.middleware import ImpersonationMiddleware

    org = _make_org("ClearsSessionOrg", "clearssession.example.com")
    operator = _make_user(org, "op@clearssession.example.com", is_superuser=True)

    response_called = []

    def tracking_view(request):
        from django.http import HttpResponse

        response_called.append(True)
        return HttpResponse("ok")

    middleware = ImpersonationMiddleware(tracking_view)
    rf = RequestFactory()
    request = rf.post("/any/path/")
    request.user = operator
    request.organization = org

    # Use a pk that does not correspond to any User in the DB
    nonexistent_pk = 999_999_999
    request.session = {"impersonating": nonexistent_pk, "real_operator": operator.pk}

    response = middleware(request)

    # Fail-open: view must still be called and return 200
    assert len(response_called) == 1
    assert response.status_code == 200

    # Session keys must have been cleared
    assert "impersonating" not in request.session
    assert "real_operator" not in request.session


# ===========================================================================
# ImpersonationMiddleware — superuser-to-superuser impersonation blocked
# ===========================================================================


@pytest.mark.django_db
def test_impersonation_middleware_blocks_superuser_to_superuser():
    """
    ImpersonationMiddleware must not allow a superuser to impersonate another
    superuser. The session keys must be cleared and the request must proceed
    unimpersonated (middleware.py lines 124-127).
    """
    from parkshare.middleware import ImpersonationMiddleware

    org = _make_org("SuperImpersonateOrg", "superimpersonate.example.com")
    operator = _make_user(org, "op@superimpersonate.example.com", is_superuser=True)
    target_super = _make_user(
        org, "target_su@superimpersonate.example.com", is_superuser=True
    )

    seen_user_pks = []

    def capturing_view(request):
        from django.http import HttpResponse

        seen_user_pks.append(request.user.pk)
        return HttpResponse("ok")

    middleware = ImpersonationMiddleware(capturing_view)
    rf = RequestFactory()
    request = rf.post("/admin/action/")
    request.user = operator
    request.organization = org
    request.session = {
        "impersonating": target_super.pk,
        "real_operator": operator.pk,
    }

    response = middleware(request)

    assert response.status_code == 200
    # The view must see the original operator (not the superuser target)
    assert seen_user_pks == [operator.pk], (
        f"Expected request.user to remain as operator pk={operator.pk}, "
        f"got {seen_user_pks}"
    )
    # Session keys must have been cleared
    assert "impersonating" not in request.session


# ===========================================================================
# backfill_audit_log — FileNotFoundError
# ===========================================================================


@pytest.mark.django_db
def test_backfill_reports_error_when_file_not_found():
    """
    backfill_audit_log must write an error message to stderr and return
    (no crash) when the recovery file does not exist (lines 68-72).
    """
    from django.core.management import call_command

    stderr = StringIO()
    call_command(
        "backfill_audit_log",
        file="/tmp/absolutely-does-not-exist-xyz.jsonl",
        stderr=stderr,
    )

    assert "not found" in stderr.getvalue().lower()


# ===========================================================================
# backfill_audit_log — blank line in JSONL
# ===========================================================================


@pytest.mark.django_db
def test_backfill_skips_blank_lines_in_jsonl():
    """
    Blank lines in the JSONL file must be silently skipped (not counted as
    malformed) (backfill_audit_log.py line 77).
    """
    from accounts.models import AdminAuditLog
    from django.core.management import call_command

    org = _make_org("BlankLineOrg", "blanklineorg.example.com")
    operator = _make_user(org, "bl_op@blanklineorg.example.com", is_superuser=True)
    target = _make_user(org, "bl_tgt@blanklineorg.example.com", is_superuser=False)

    good_record = {
        "organization_id": org.pk,
        "actor_id": operator.pk,
        "on_behalf_of_id": target.pk,
        "action": "impersonate_action",
        "target_type": "user",
        "target_id": target.pk,
        "notes": "POST /blank-line-test/",
        "attempted_at": "2026-06-14T08:00:00+00:00",
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        f.write("\n")  # blank line first
        f.write(json.dumps(good_record) + "\n")
        f.write("   \n")  # whitespace-only line
        tmp_path = f.name

    before = AdminAuditLog.objects.count()
    stdout = StringIO()
    call_command("backfill_audit_log", file=tmp_path, stdout=stdout)
    output = stdout.getvalue()

    # The good record should be created; blanks should not appear as malformed
    assert "created=1" in output, f"Expected created=1; got: {output}"
    assert "malformed=0" in output, f"Expected malformed=0; got: {output}"
    assert AdminAuditLog.objects.count() == before + 1


# ===========================================================================
# backfill_audit_log — malformed JSON line
# ===========================================================================


@pytest.mark.django_db
def test_backfill_counts_malformed_json_lines():
    """
    A line that is not valid JSON must be counted as malformed and skipped
    without crashing (backfill_audit_log.py lines 94-101).
    """
    from django.core.management import call_command

    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        f.write("this is not json at all\n")
        tmp_path = f.name

    stdout = StringIO()
    stderr = StringIO()
    call_command("backfill_audit_log", file=tmp_path, stdout=stdout, stderr=stderr)
    output = stdout.getvalue()

    assert "malformed=1" in output, f"Expected malformed=1; got: {output}"
    assert "created=0" in output


@pytest.mark.django_db
def test_backfill_counts_missing_required_key_as_malformed():
    """
    A JSON object that is missing required keys (e.g. 'actor_id') must be
    counted as malformed (backfill_audit_log.py lines 94-101 KeyError branch).
    """
    from django.core.management import call_command

    # actor_id is required; omitting it should trigger KeyError
    bad_record = {
        "organization_id": 1,
        "on_behalf_of_id": None,
        "action": "impersonate_action",
        "target_type": "user",
        "target_id": 1,
        "notes": "POST /x/",
        "attempted_at": "2026-06-14T08:00:00+00:00",
        # actor_id intentionally missing
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        f.write(json.dumps(bad_record) + "\n")
        tmp_path = f.name

    stdout = StringIO()
    call_command("backfill_audit_log", file=tmp_path, stdout=stdout)
    output = stdout.getvalue()

    assert "malformed=1" in output, f"Expected malformed=1; got: {output}"
    assert "created=0" in output


# ===========================================================================
# backfill_audit_log — naive attempted_at (no tzinfo) → make_aware()
# ===========================================================================


@pytest.mark.django_db
def test_backfill_accepts_naive_attempted_at_and_makes_it_aware():
    """
    A record whose attempted_at is a naive datetime string (no UTC offset or Z)
    must be accepted: the command calls make_aware() and continues normally
    (backfill_audit_log.py line 116).
    """
    from accounts.models import AdminAuditLog
    from django.core.management import call_command

    org = _make_org("NaiveAtOrg", "naiveatorog.example.com")
    operator = _make_user(org, "na_op@naiveatorog.example.com", is_superuser=True)
    target = _make_user(org, "na_tgt@naiveatorog.example.com", is_superuser=False)

    record = {
        "organization_id": org.pk,
        "actor_id": operator.pk,
        "on_behalf_of_id": target.pk,
        "action": "impersonate_action",
        "target_type": "user",
        "target_id": target.pk,
        "notes": "POST /naive-at-test/",
        "attempted_at": "2026-06-14T08:00:00",  # no UTC offset — naive datetime
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        f.write(json.dumps(record) + "\n")
        tmp_path = f.name

    before = AdminAuditLog.objects.count()
    stdout = StringIO()
    call_command("backfill_audit_log", file=tmp_path, stdout=stdout)
    output = stdout.getvalue()

    assert "created=1" in output, f"Expected created=1; got: {output}"
    assert "malformed=0" in output
    assert AdminAuditLog.objects.count() == before + 1


# ===========================================================================
# backfill_audit_log — unparseable attempted_at
# ===========================================================================


@pytest.mark.django_db
def test_backfill_counts_unparseable_attempted_at_as_malformed():
    """
    A record whose attempted_at value cannot be parsed as ISO-8601 must be
    counted as malformed and skipped (backfill_audit_log.py lines 119-126).
    """
    from django.core.management import call_command

    record = {
        "organization_id": 1,
        "actor_id": 1,
        "on_behalf_of_id": None,
        "action": "impersonate_action",
        "target_type": "user",
        "target_id": 1,
        "notes": "POST /x/",
        "attempted_at": "not-a-date",  # unparseable
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        f.write(json.dumps(record) + "\n")
        tmp_path = f.name

    stdout = StringIO()
    call_command("backfill_audit_log", file=tmp_path, stdout=stdout)
    output = stdout.getvalue()

    assert "malformed=1" in output, f"Expected malformed=1; got: {output}"
    assert "created=0" in output


# ===========================================================================
# backfill_audit_log — actor User.DoesNotExist
# ===========================================================================


@pytest.mark.django_db
def test_backfill_counts_nonexistent_actor_as_malformed():
    """
    A record whose actor_id does not correspond to a User in the DB must be
    counted as malformed and skipped (backfill_audit_log.py lines 130-137).
    """
    from django.core.management import call_command

    record = {
        "organization_id": 1,
        "actor_id": 999_999_888,  # no such User
        "on_behalf_of_id": None,
        "action": "impersonate_action",
        "target_type": "user",
        "target_id": 1,
        "notes": "POST /x/",
        "attempted_at": "2026-06-14T08:00:00+00:00",
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        f.write(json.dumps(record) + "\n")
        tmp_path = f.name

    stdout = StringIO()
    call_command("backfill_audit_log", file=tmp_path, stdout=stdout)
    output = stdout.getvalue()

    assert "malformed=1" in output, f"Expected malformed=1; got: {output}"
    assert "created=0" in output


# ===========================================================================
# backfill_audit_log — on_behalf_of User.DoesNotExist
# ===========================================================================


@pytest.mark.django_db
def test_backfill_creates_row_when_on_behalf_of_not_found():
    """
    When on_behalf_of_id references a User that no longer exists, the backfill
    must still create the AdminAuditLog row with on_behalf_of=None, and emit a
    warning (backfill_audit_log.py lines 181-182).
    """
    from accounts.models import AdminAuditLog
    from django.core.management import call_command

    org = _make_org("OnBehalfMissingOrg", "onbehalfmissing.example.com")
    operator = _make_user(org, "obm_op@onbehalfmissing.example.com", is_superuser=True)

    record = {
        "organization_id": org.pk,
        "actor_id": operator.pk,
        "on_behalf_of_id": 999_999_777,  # no such User
        "action": "impersonate_action",
        "target_type": "user",
        "target_id": 999_999_777,
        "notes": "POST /on-behalf-missing/",
        "attempted_at": "2026-06-14T09:00:00+00:00",
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        f.write(json.dumps(record) + "\n")
        tmp_path = f.name

    before = AdminAuditLog.objects.count()
    stdout = StringIO()
    stderr = StringIO()
    call_command("backfill_audit_log", file=tmp_path, stdout=stdout, stderr=stderr)
    output = stdout.getvalue()
    err_output = stderr.getvalue()

    # Row should still be created (not rejected/malformed)
    assert "created=1" in output, f"Expected created=1; got: {output}"
    assert AdminAuditLog.objects.count() == before + 1

    # Warning must appear on stderr
    assert "on_behalf_of" in err_output.lower() or "not found" in err_output.lower(), (
        f"Expected 'not found' warning in stderr; got: {err_output!r}"
    )

    # The row must have on_behalf_of=None
    entry = AdminAuditLog.objects.filter(actor=operator).latest("created_at")
    assert entry.on_behalf_of is None, (
        f"Expected on_behalf_of to be None, got {entry.on_behalf_of}"
    )


# ===========================================================================
# backfill_audit_log — Organization.DoesNotExist (org in record missing from DB)
# ===========================================================================


@pytest.mark.django_db
def test_backfill_creates_row_when_organization_not_found():
    """
    When organization_id references an Organization that no longer exists in
    the DB, the backfill must still create the AdminAuditLog row with
    organization=None (backfill_audit_log.py lines 193-194).

    This covers the Organization.DoesNotExist except-pass branch.
    """
    from accounts.models import AdminAuditLog
    from django.core.management import call_command

    org = _make_org("OrgNotFoundOrg", "orgnotfound.example.com")
    operator = _make_user(org, "onf_op@orgnotfound.example.com", is_superuser=True)
    target = _make_user(org, "onf_tgt@orgnotfound.example.com", is_superuser=False)

    # Use the operator's real org pk, then delete the org after capturing pk.
    # But we cannot delete org while users exist (FK). Instead, use a
    # phantom org pk that was never inserted.
    phantom_org_pk = 999_999_666

    # The actor belongs to `org`, and the record's organization_id is the
    # actor's own org pk — so the cross-tenant check passes (actor.organization_id
    # == organization_id). But the Organization row referenced by the phantom_pk
    # does not exist, so Organization.objects.get() raises DoesNotExist.
    #
    # To make cross-tenant check pass, we need actor.organization_id == phantom_org_pk.
    # That's impossible without updating the actor's org in the DB.
    # Use organization_id=None instead — this skips both the cross-tenant check
    # AND the org lookup, but doesn't exercise line 193-194.
    #
    # The correct approach: patch Organization.objects.get to raise DoesNotExist
    # for the phantom pk, while allowing the real org to still be usable for
    # the actor cross-tenant check.  We use operator's real org_id in the
    # record so the cross-tenant guard passes, then the lookup itself raises.

    record = {
        "organization_id": org.pk,
        "actor_id": operator.pk,
        "on_behalf_of_id": target.pk,
        "action": "impersonate_action",
        "target_type": "user",
        "target_id": target.pk,
        "notes": "POST /org-missing/",
        "attempted_at": "2026-06-14T10:00:00+00:00",
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        f.write(json.dumps(record) + "\n")
        tmp_path = f.name

    from parking.models import Organization as OrgModel

    original_get = OrgModel.objects.get

    def patched_get(**kwargs):
        if kwargs.get("pk") == org.pk:
            raise OrgModel.DoesNotExist("simulated missing org")
        return original_get(**kwargs)

    before = AdminAuditLog.objects.count()
    stdout = StringIO()

    with patch.object(OrgModel.objects, "get", side_effect=patched_get):
        call_command("backfill_audit_log", file=tmp_path, stdout=stdout)

    output = stdout.getvalue()

    # Row must still be created even if organization lookup fails
    assert "created=1" in output, f"Expected created=1 even with missing org; got: {output}"
    assert AdminAuditLog.objects.count() == before + 1

    # organization field must be None on the created row
    entry = AdminAuditLog.objects.filter(actor=operator).latest("created_at")
    assert entry.organization is None, (
        f"Expected organization=None when org not found; got {entry.organization}"
    )


# ===========================================================================
# backfill_audit_log — atomic create+update preserves idempotency
# ===========================================================================


@pytest.mark.django_db
def test_atomic_backfill_insert_is_idempotent():
    """
    The create()+update() pair is now wrapped in transaction.atomic().
    Confirm that idempotency still holds: running the command twice on the
    same JSONL produces created=1 skipped=0 on the first run and
    created=0 skipped=1 on the second, with exactly one row in the DB.
    """
    from accounts.models import AdminAuditLog
    from django.core.management import call_command

    org = _make_org("AtomicIdempotentOrg", "atomicidempotent.example.com")
    operator = _make_user(org, "ai_op@atomicidempotent.example.com", is_superuser=True)
    target = _make_user(org, "ai_tgt@atomicidempotent.example.com", is_superuser=False)

    record = {
        "organization_id": org.pk,
        "actor_id": operator.pk,
        "on_behalf_of_id": target.pk,
        "action": "impersonate_action",
        "target_type": "user",
        "target_id": target.pk,
        "notes": "POST /atomic-test/",
        "attempted_at": "2026-06-14T14:00:00+00:00",
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        f.write(json.dumps(record) + "\n")
        tmp_path = f.name

    stdout1 = StringIO()
    call_command("backfill_audit_log", file=tmp_path, stdout=stdout1)
    assert "created=1" in stdout1.getvalue()
    assert "skipped=0" in stdout1.getvalue()

    count_after_first = AdminAuditLog.objects.filter(
        actor=operator, action="impersonate_action"
    ).count()
    assert count_after_first == 1

    stdout2 = StringIO()
    call_command("backfill_audit_log", file=tmp_path, stdout=stdout2)
    output2 = stdout2.getvalue()
    assert "created=0" in output2, f"Expected created=0 on second run; got: {output2}"
    assert "skipped=1" in output2, f"Expected skipped=1 on second run; got: {output2}"

    count_after_second = AdminAuditLog.objects.filter(
        actor=operator, action="impersonate_action"
    ).count()
    assert count_after_second == count_after_first, (
        "Atomic wrap must not break idempotency: second run must not add rows"
    )
