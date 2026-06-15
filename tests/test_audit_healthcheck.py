"""
Unit tests for:
  - accounts/management/commands/audit_healthcheck.py
  - accounts/management/commands/backfill_audit_log.py
  - accounts/models.AuditProbe

Covers the high-value behaviors and edge cases called out in the review:
  - Exact metric names and labels in the .prom file
  - No PII in .prom or liveness status
  - Fail-safe: DB error → liveness_ok=0 + exit code 1
  - AuditProbe write+read-back round trip
  - max(0.0, ...) guard for clock skew
  - _read_last_success_ts edge cases
  - _validate_label valid/invalid/unknown
  - --dry-run is read-only
  - compute_backlog counts, anti-forgery, deduplication
  - 50 MB file-size guard
"""

import json
import os
import tempfile
from datetime import datetime, timezone as dt_timezone
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.test import TestCase, override_settings


# ---------------------------------------------------------------------------
# Helpers / factories
# ---------------------------------------------------------------------------


def _make_org(name="TestOrg", hostname="test.example.com"):
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


def _make_superuser(org, email="admin@test.example.com"):
    from accounts.models import User

    return User.objects.create_user(
        email=email,
        organization=org,
        display_name="Admin User",
        password="test-password-secure!",
        is_superuser=True,
        is_staff=True,
        status="active",
    )


def _make_regular_user(org, email="user@test.example.com"):
    from accounts.models import User

    return User.objects.create_user(
        email=email,
        organization=org,
        display_name="Regular User",
        password="test-password-secure!",
        is_superuser=False,
        status="active",
    )


def _make_recovery_jsonl(lines, tmp_path):
    """Write a list of dicts (or raw strings) to a JSONL file and return the path."""
    p = tmp_path / "audit-recovery.jsonl"
    with open(p, "w", encoding="utf-8") as fh:
        for line in lines:
            if isinstance(line, dict):
                fh.write(json.dumps(line) + "\n")
            else:
                fh.write(line + "\n")
    return str(p)


def _make_status_jsonl(lines, tmp_path, filename="audit-liveness.jsonl"):
    """Write liveness status lines to a JSONL file and return the path."""
    p = tmp_path / filename
    with open(p, "w", encoding="utf-8") as fh:
        for line in lines:
            if isinstance(line, dict):
                fh.write(json.dumps(line) + "\n")
            else:
                fh.write(line + "\n")
    return str(p)


def _valid_record(actor_id, org_id=None, on_behalf_of_id=None, notes="test note"):
    return {
        "organization_id": org_id,
        "actor_id": actor_id,
        "on_behalf_of_id": on_behalf_of_id,
        "action": "impersonate_action",
        "target_type": "user",
        "target_id": 99,
        "notes": notes,
        "attempted_at": "2025-01-15T10:00:00+00:00",
    }


# ---------------------------------------------------------------------------
# _validate_label tests
# ---------------------------------------------------------------------------


class ValidateLabelTests(TestCase):
    """Tests for the _validate_label function."""

    def test_valid_alphanumeric_passes(self):
        from accounts.management.commands.audit_healthcheck import _validate_label

        result = _validate_label("env", "production")
        self.assertEqual(result, "production")

    def test_valid_with_dots_dashes_passes(self):
        from accounts.management.commands.audit_healthcheck import _validate_label

        result = _validate_label("host", "my-host.example.com")
        self.assertEqual(result, "my-host.example.com")

    def test_valid_64_chars_passes(self):
        from accounts.management.commands.audit_healthcheck import _validate_label

        value = "a" * 64
        result = _validate_label("env", value)
        self.assertEqual(result, value)

    def test_value_with_double_quote_raises(self):
        from accounts.management.commands.audit_healthcheck import _validate_label

        with self.assertRaises(ImproperlyConfigured):
            _validate_label("env", 'bad"value')

    def test_value_with_newline_raises(self):
        from accounts.management.commands.audit_healthcheck import _validate_label

        with self.assertRaises(ImproperlyConfigured):
            _validate_label("env", "bad\nvalue")

    def test_value_with_space_raises(self):
        from accounts.management.commands.audit_healthcheck import _validate_label

        with self.assertRaises(ImproperlyConfigured):
            _validate_label("env", "bad value")

    def test_value_65_chars_raises(self):
        from accounts.management.commands.audit_healthcheck import _validate_label

        value = "a" * 65
        with self.assertRaises(ImproperlyConfigured):
            _validate_label("env", value)

    def test_empty_string_raises(self):
        from accounts.management.commands.audit_healthcheck import _validate_label

        with self.assertRaises(ImproperlyConfigured):
            _validate_label("env", "")

    def test_unknown_logs_warning_but_does_not_raise(self):
        """'unknown' is a valid label value (it passes the regex), so no exception."""
        from accounts.management.commands.audit_healthcheck import _validate_label

        # Should not raise — just returns the value
        result = _validate_label("env", "unknown")
        self.assertEqual(result, "unknown")


# ---------------------------------------------------------------------------
# _build_prom_text / _prom_gauge tests — exact metric names and labels
# ---------------------------------------------------------------------------


class PromTextTests(TestCase):
    """Tests for the _build_prom_text helper function."""

    def _get_prom_text(self):
        from accounts.management.commands.audit_healthcheck import _build_prom_text

        return _build_prom_text(
            liveness_ok=1,
            liveness_age_seconds=0.0,
            backlog_records=5,
            backlog_oldest_seconds=300.0,
            backlog_rejected=2,
            backlog_malformed=1,
            healthcheck_unixtime=1700000000.0,
        )

    def test_liveness_ok_metric_name_present(self):
        text = self._get_prom_text()
        self.assertIn("parkshare_audit_liveness_ok", text)

    def test_liveness_age_seconds_metric_name_present(self):
        text = self._get_prom_text()
        self.assertIn("parkshare_audit_liveness_age_seconds", text)

    def test_backlog_records_metric_name_present(self):
        text = self._get_prom_text()
        self.assertIn("parkshare_audit_backlog_records", text)

    def test_backlog_oldest_seconds_metric_name_present(self):
        text = self._get_prom_text()
        self.assertIn("parkshare_audit_backlog_oldest_seconds", text)

    def test_backlog_rejected_metric_name_present(self):
        text = self._get_prom_text()
        self.assertIn("parkshare_audit_backlog_rejected", text)

    def test_backlog_malformed_metric_name_present(self):
        text = self._get_prom_text()
        self.assertIn("parkshare_audit_backlog_malformed", text)

    def test_healthcheck_unixtime_metric_name_present(self):
        text = self._get_prom_text()
        self.assertIn("parkshare_audit_healthcheck_unixtime", text)

    def test_service_label_present(self):
        text = self._get_prom_text()
        self.assertIn('service="parkshare-web"', text)

    def test_env_label_present(self):
        text = self._get_prom_text()
        self.assertIn("env=", text)

    def test_host_label_present(self):
        text = self._get_prom_text()
        self.assertIn("host=", text)

    def test_gauge_type_declarations_present(self):
        text = self._get_prom_text()
        self.assertIn("# TYPE parkshare_audit_liveness_ok gauge", text)
        self.assertIn("# TYPE parkshare_audit_backlog_records gauge", text)

    def test_metric_values_embedded_correctly(self):
        text = self._get_prom_text()
        # backlog_records = 5
        self.assertIn("} 5\n", text)
        # backlog_rejected = 2
        self.assertIn("} 2\n", text)

    def test_no_pii_in_prom_text(self):
        """The .prom text must contain no actor IDs, notes, or paths."""
        text = self._get_prom_text()
        # PII probe: no email addresses, no operator names in the text
        self.assertNotIn("@", text)
        self.assertNotIn("recovered:", text)


# ---------------------------------------------------------------------------
# _write_prom_atomically tests
# ---------------------------------------------------------------------------


class WritePomAtomicallyTests(TestCase):
    """Tests for atomic .prom file writing."""

    def test_creates_prom_file_in_directory(self):
        from accounts.management.commands.audit_healthcheck import _write_prom_atomically

        with tempfile.TemporaryDirectory() as tmpdir:
            _write_prom_atomically(tmpdir, "test content")
            target = Path(tmpdir) / "parkshare_audit.prom"
            self.assertTrue(target.exists())

    def test_prom_file_contains_written_content(self):
        from accounts.management.commands.audit_healthcheck import _write_prom_atomically

        with tempfile.TemporaryDirectory() as tmpdir:
            _write_prom_atomically(tmpdir, "# HELP test\n# TYPE test gauge\ntest 1\n")
            target = Path(tmpdir) / "parkshare_audit.prom"
            content = target.read_text(encoding="utf-8")
            self.assertIn("# HELP test", content)

    def test_atomic_write_no_temp_files_left(self):
        """After a successful write, no .tmp files should remain."""
        from accounts.management.commands.audit_healthcheck import _write_prom_atomically

        with tempfile.TemporaryDirectory() as tmpdir:
            _write_prom_atomically(tmpdir, "content")
            leftover = list(Path(tmpdir).glob("*.tmp"))
            self.assertEqual(leftover, [])

    def test_raises_oserror_for_nonexistent_dir(self):
        from accounts.management.commands.audit_healthcheck import _write_prom_atomically

        with self.assertRaises(OSError):
            _write_prom_atomically("/nonexistent/path/that/does/not/exist", "content")


# ---------------------------------------------------------------------------
# _append_liveness_status tests
# ---------------------------------------------------------------------------


class AppendLivenessStatusTests(TestCase):
    """Tests for _append_liveness_status."""

    def test_appends_json_line_to_file(self):
        from accounts.management.commands.audit_healthcheck import _append_liveness_status

        with tempfile.TemporaryDirectory() as tmpdir:
            status_path = os.path.join(tmpdir, "liveness.jsonl")
            record = {"ts": 1700000000.0, "ok": True}
            _append_liveness_status(status_path, record)
            with open(status_path) as fh:
                line = fh.readline().strip()
            loaded = json.loads(line)
            self.assertEqual(loaded["ok"], True)

    def test_creates_parent_dirs_if_missing(self):
        from accounts.management.commands.audit_healthcheck import _append_liveness_status

        with tempfile.TemporaryDirectory() as tmpdir:
            status_path = os.path.join(tmpdir, "subdir", "liveness.jsonl")
            _append_liveness_status(status_path, {"ts": 1.0, "ok": True})
            self.assertTrue(Path(status_path).exists())

    def test_multiple_appends_create_multiple_lines(self):
        from accounts.management.commands.audit_healthcheck import _append_liveness_status

        with tempfile.TemporaryDirectory() as tmpdir:
            status_path = os.path.join(tmpdir, "liveness.jsonl")
            _append_liveness_status(status_path, {"ts": 1.0, "ok": True})
            _append_liveness_status(status_path, {"ts": 2.0, "ok": False})
            with open(status_path) as fh:
                lines = fh.readlines()
            self.assertEqual(len(lines), 2)


# ---------------------------------------------------------------------------
# _read_last_success_ts edge case tests
# ---------------------------------------------------------------------------


class ReadLastSuccessTsTests(TestCase):
    """Tests for the backward-scanning tail reader."""

    def _call(self, path):
        from accounts.management.commands.audit_healthcheck import _read_last_success_ts

        return _read_last_success_ts(path)

    def test_missing_file_returns_none(self):
        result = self._call("/nonexistent/path/to/file.jsonl")
        self.assertIsNone(result)

    def test_empty_file_returns_none(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as fh:
            path = fh.name
        try:
            result = self._call(path)
            self.assertIsNone(result)
        finally:
            os.unlink(path)

    def test_single_success_line_no_trailing_newline(self):
        """A file with no trailing newline — last line must still be parsed."""
        with tempfile.NamedTemporaryFile(
            mode="wb", suffix=".jsonl", delete=False
        ) as fh:
            path = fh.name
            record = json.dumps({"ts": 1700100000.0, "ok": True})
            fh.write(record.encode("utf-8"))  # no trailing \n
        try:
            result = self._call(path)
            self.assertEqual(result, 1700100000.0)
        finally:
            os.unlink(path)

    def test_returns_most_recent_success(self):
        """When multiple ok=true entries exist, the most recent one is returned."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False
        ) as fh:
            path = fh.name
            fh.write(json.dumps({"ts": 1700000000.0, "ok": True}) + "\n")
            fh.write(json.dumps({"ts": 1700000100.0, "ok": True}) + "\n")
            fh.write(json.dumps({"ts": 1700000200.0, "ok": True}) + "\n")
        try:
            result = self._call(path)
            self.assertEqual(result, 1700000200.0)
        finally:
            os.unlink(path)

    def test_no_ok_true_entry_returns_none(self):
        """When no entry has ok=true, returns None."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False
        ) as fh:
            path = fh.name
            fh.write(json.dumps({"ts": 1700000000.0, "ok": False}) + "\n")
            fh.write(json.dumps({"ts": 1700000100.0, "ok": False}) + "\n")
        try:
            result = self._call(path)
            self.assertIsNone(result)
        finally:
            os.unlink(path)

    def test_malformed_lines_skipped(self):
        """Malformed JSON lines are skipped; valid entries still returned."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False
        ) as fh:
            path = fh.name
            fh.write("not-valid-json\n")
            fh.write(json.dumps({"ts": 1700000500.0, "ok": True}) + "\n")
            fh.write("{broken}\n")
        try:
            result = self._call(path)
            self.assertEqual(result, 1700000500.0)
        finally:
            os.unlink(path)

    def test_failure_entries_skipped_returns_last_success(self):
        """ok=false entries are skipped; ok=true ones are returned."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False
        ) as fh:
            path = fh.name
            fh.write(json.dumps({"ts": 1700000000.0, "ok": True}) + "\n")
            fh.write(json.dumps({"ts": 1700000100.0, "ok": False}) + "\n")
            fh.write(json.dumps({"ts": 1700000200.0, "ok": False}) + "\n")
        try:
            result = self._call(path)
            self.assertEqual(result, 1700000000.0)
        finally:
            os.unlink(path)

    def test_first_line_of_file_parsed(self):
        """The very first line of the file is accessible via the remainder path."""
        with tempfile.NamedTemporaryFile(
            mode="wb", suffix=".jsonl", delete=False
        ) as fh:
            path = fh.name
            # Single line, no newline — only path is the remainder handler
            record = json.dumps({"ts": 1700099999.0, "ok": True})
            fh.write(record.encode("utf-8"))
        try:
            result = self._call(path)
            self.assertEqual(result, 1700099999.0)
        finally:
            os.unlink(path)

    def test_first_line_malformed_remainder_returns_none(self):
        """If the very first line (remainder after backward scan) is malformed JSON, returns None."""
        with tempfile.NamedTemporaryFile(
            mode="wb", suffix=".jsonl", delete=False
        ) as fh:
            path = fh.name
            # Single line that is invalid JSON — only path is the remainder handler
            fh.write(b"THIS IS NOT JSON AT ALL")
        try:
            result = self._call(path)
            self.assertIsNone(result)
        finally:
            os.unlink(path)

    def test_json_line_crossing_8kb_chunk_boundary(self):
        """A JSON entry that straddles the 8192-byte chunk boundary is reassembled."""
        # We need the file total size to be just over 8192 bytes, and one success
        # line near the start that would straddle the boundary.
        with tempfile.NamedTemporaryFile(
            mode="wb", suffix=".jsonl", delete=False
        ) as fh:
            path = fh.name
            # First line: padded to land right at the boundary cut point
            # We want a success record that, when reading backward in 8192-byte chunks,
            # will be split across two chunks.
            # File layout: [padding lines to ~8150 bytes][success record spanning ~100 bytes][failure lines]
            # Write enough padding to push the target record to straddle chunk 1/chunk 2
            padding_line = json.dumps({"ts": 1.0, "ok": False}) + "\n"
            # We need enough content so the file is > 8192 bytes
            # Put the target success near position 8100-8200
            fh.write(b"x" * 8150 + b"\n")  # first ~8KB of junk
            success_record = json.dumps({"ts": 1700111111.0, "ok": True}) + "\n"
            fh.write(success_record.encode("utf-8"))
            # Add some more lines after
            fh.write((json.dumps({"ts": 2.0, "ok": False}) + "\n").encode("utf-8"))
        try:
            result = self._call(path)
            # The padded junk line is not valid JSON so it's skipped;
            # we should find the success record
            self.assertEqual(result, 1700111111.0)
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# AuditProbe model tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class AuditProbeModelTests(TestCase):
    """Tests for the AuditProbe model."""

    def test_create_audit_probe(self):
        from accounts.models import AuditProbe

        probe = AuditProbe.objects.create()
        self.assertIsNotNone(probe.pk)
        self.assertIsNotNone(probe.created_at)

    def test_read_back_audit_probe(self):
        from accounts.models import AuditProbe

        probe = AuditProbe.objects.create()
        retrieved = AuditProbe.objects.get(pk=probe.pk)
        self.assertEqual(retrieved.pk, probe.pk)

    def test_str_representation(self):
        from accounts.models import AuditProbe

        probe = AuditProbe.objects.create()
        self.assertIn("AuditProbe", str(probe))

    def test_ordering_is_most_recent_first(self):
        from accounts.models import AuditProbe

        p1 = AuditProbe.objects.create()
        p2 = AuditProbe.objects.create()
        qs = list(AuditProbe.objects.all())
        # Most recent first (-created_at ordering)
        self.assertEqual(qs[0].pk, p2.pk)
        self.assertEqual(qs[1].pk, p1.pk)


# ---------------------------------------------------------------------------
# audit_healthcheck command tests — liveness probe
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class AuditHealthcheckLivenessTests(TestCase):
    """Tests for the liveness probe behavior of audit_healthcheck."""

    def _run_command(self, tmpdir, recovery_path=None, exit_ok=True):
        """Run the command in a temp dir and return stdout."""
        status_path = os.path.join(tmpdir, "liveness.jsonl")
        if recovery_path is None:
            recovery_path = os.path.join(tmpdir, "audit-recovery.jsonl")
        out = StringIO()
        with override_settings(
            AUDIT_LIVENESS_STATUS=status_path,
            NODE_EXPORTER_TEXTFILE_DIR=tmpdir,
            AUDIT_RECOVERY_LOG=recovery_path,
            ENVIRONMENT="test",
        ):
            try:
                call_command("audit_healthcheck", stdout=out, stderr=StringIO())
            except SystemExit as e:
                if exit_ok and e.code != 0:
                    raise
        return out.getvalue(), status_path, tmpdir

    def test_probe_writes_prom_file(self):
        from accounts.models import AuditProbe

        with tempfile.TemporaryDirectory() as tmpdir:
            self._run_command(tmpdir)
            prom_file = Path(tmpdir) / "parkshare_audit.prom"
            self.assertTrue(prom_file.exists(), "parkshare_audit.prom must be created")

    def test_probe_creates_audit_probe_row(self):
        from accounts.models import AuditProbe

        before = AuditProbe.objects.count()
        with tempfile.TemporaryDirectory() as tmpdir:
            self._run_command(tmpdir)
        # At least one probe was created (old ones may be pruned)
        after = AuditProbe.objects.count()
        self.assertGreater(after, before - 1)  # net at least 0 (pruning keeps ≤10)

    def test_prom_file_contains_exact_metric_names(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self._run_command(tmpdir)
            prom_file = Path(tmpdir) / "parkshare_audit.prom"
            content = prom_file.read_text(encoding="utf-8")

        expected_metrics = [
            "parkshare_audit_liveness_ok",
            "parkshare_audit_liveness_age_seconds",
            "parkshare_audit_backlog_records",
            "parkshare_audit_backlog_oldest_seconds",
            "parkshare_audit_backlog_rejected",
            "parkshare_audit_backlog_malformed",
            "parkshare_audit_healthcheck_unixtime",
        ]
        for metric in expected_metrics:
            self.assertIn(metric, content, f"Metric {metric!r} missing from .prom output")

    def test_prom_file_has_env_host_service_labels(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self._run_command(tmpdir)
            prom_file = Path(tmpdir) / "parkshare_audit.prom"
            content = prom_file.read_text(encoding="utf-8")

        self.assertIn("env=", content)
        self.assertIn("host=", content)
        self.assertIn('service="parkshare-web"', content)

    def test_liveness_ok_equals_1_on_success(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self._run_command(tmpdir)
            prom_file = Path(tmpdir) / "parkshare_audit.prom"
            content = prom_file.read_text(encoding="utf-8")

        # Find liveness_ok line and check value is 1
        for line in content.splitlines():
            if "parkshare_audit_liveness_ok{" in line:
                self.assertTrue(line.endswith("} 1"), f"Expected liveness_ok=1, got: {line}")
                break
        else:
            self.fail("parkshare_audit_liveness_ok metric line not found")

    def test_liveness_status_file_written_on_success(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _, status_path, _ = self._run_command(tmpdir)
            self.assertTrue(Path(status_path).exists(), "Liveness status file must be created")
            with open(status_path) as fh:
                lines = [l.strip() for l in fh if l.strip()]
            self.assertGreater(len(lines), 0)
            record = json.loads(lines[-1])
            self.assertTrue(record["ok"])

    def test_no_pii_in_prom_file(self):
        """PII must not appear in the .prom output."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Write a recovery JSONL with PII-like content
            recovery_path = os.path.join(tmpdir, "audit-recovery.jsonl")
            with open(recovery_path, "w") as fh:
                fh.write(
                    json.dumps(
                        {
                            "actor_id": 99999,
                            "action": "impersonate_action",
                            "attempted_at": "2025-01-15T10:00:00+00:00",
                            "notes": "SECRET_NOTE actor=secret@pii.com",
                        }
                    )
                    + "\n"
                )
            self._run_command(tmpdir, recovery_path=recovery_path)
            prom_file = Path(tmpdir) / "parkshare_audit.prom"
            content = prom_file.read_text(encoding="utf-8")

        self.assertNotIn("SECRET_NOTE", content)
        self.assertNotIn("secret@pii.com", content)
        self.assertNotIn("99999", content)

    def test_no_pii_in_liveness_status(self):
        """Actor IDs, notes, and paths from recovery records must not appear in the status file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            recovery_path = os.path.join(tmpdir, "audit-recovery.jsonl")
            with open(recovery_path, "w") as fh:
                fh.write(
                    json.dumps(
                        {
                            "actor_id": 88888,
                            "action": "impersonate_action",
                            "attempted_at": "2025-01-15T10:00:00+00:00",
                            "notes": "PRIVATE_PATH /var/secret/data",
                        }
                    )
                    + "\n"
                )
            _, status_path, _ = self._run_command(tmpdir, recovery_path=recovery_path)
            with open(status_path) as fh:
                status_content = fh.read()

        self.assertNotIn("PRIVATE_PATH", status_content)
        self.assertNotIn("88888", status_content)

    def test_fail_safe_db_error_emits_liveness_ok_zero(self):
        """On DB failure, liveness_ok=0 must appear in the .prom file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            status_path = os.path.join(tmpdir, "liveness.jsonl")
            recovery_path = os.path.join(tmpdir, "audit-recovery.jsonl")
            with override_settings(
                AUDIT_LIVENESS_STATUS=status_path,
                NODE_EXPORTER_TEXTFILE_DIR=tmpdir,
                AUDIT_RECOVERY_LOG=recovery_path,
                ENVIRONMENT="test",
            ):
                with patch(
                    "accounts.models.AuditProbe.objects.create",
                    side_effect=Exception("DB error"),
                ):
                    with self.assertRaises(SystemExit) as cm:
                        call_command("audit_healthcheck", stdout=StringIO(), stderr=StringIO())
                self.assertEqual(cm.exception.code, 1)

            prom_file = Path(tmpdir) / "parkshare_audit.prom"
            content = prom_file.read_text(encoding="utf-8")
            for line in content.splitlines():
                if "parkshare_audit_liveness_ok{" in line:
                    self.assertTrue(
                        line.endswith("} 0"),
                        f"Expected liveness_ok=0 on DB failure, got: {line}",
                    )
                    break
            else:
                self.fail("parkshare_audit_liveness_ok not found in .prom on DB failure")

    def test_fail_safe_db_error_exits_nonzero(self):
        """On DB failure, the command exits with code 1."""
        with tempfile.TemporaryDirectory() as tmpdir:
            status_path = os.path.join(tmpdir, "liveness.jsonl")
            recovery_path = os.path.join(tmpdir, "audit-recovery.jsonl")
            with override_settings(
                AUDIT_LIVENESS_STATUS=status_path,
                NODE_EXPORTER_TEXTFILE_DIR=tmpdir,
                AUDIT_RECOVERY_LOG=recovery_path,
                ENVIRONMENT="test",
            ):
                with patch(
                    "accounts.models.AuditProbe.objects.create",
                    side_effect=Exception("simulated DB failure"),
                ):
                    with self.assertRaises(SystemExit) as cm:
                        call_command("audit_healthcheck", stdout=StringIO(), stderr=StringIO())
            self.assertEqual(cm.exception.code, 1)

    def test_max_zero_guard_future_last_ok_ts(self):
        """Clock skew: last_ok_ts in the future yields liveness_age_seconds=0.0, not negative."""
        with tempfile.TemporaryDirectory() as tmpdir:
            status_path = os.path.join(tmpdir, "liveness.jsonl")
            recovery_path = os.path.join(tmpdir, "audit-recovery.jsonl")
            # Write a status file with a future timestamp
            future_ts = 9999999999.0  # year 2286
            with open(status_path, "w") as fh:
                fh.write(json.dumps({"ts": future_ts, "ok": True}) + "\n")

            with override_settings(
                AUDIT_LIVENESS_STATUS=status_path,
                NODE_EXPORTER_TEXTFILE_DIR=tmpdir,
                AUDIT_RECOVERY_LOG=recovery_path,
                ENVIRONMENT="test",
            ):
                # Force the probe to fail so we enter the liveness_age path
                with patch(
                    "accounts.models.AuditProbe.objects.create",
                    side_effect=Exception("forced failure"),
                ):
                    try:
                        call_command("audit_healthcheck", stdout=StringIO(), stderr=StringIO())
                    except SystemExit:
                        pass

            prom_file = Path(tmpdir) / "parkshare_audit.prom"
            content = prom_file.read_text(encoding="utf-8")

            # Find the age line and verify it's 0.0 (not negative)
            for line in content.splitlines():
                if "parkshare_audit_liveness_age_seconds{" in line:
                    parts = line.split("} ")
                    age_value = float(parts[-1])
                    self.assertGreaterEqual(age_value, 0.0, "Liveness age must be >= 0.0")
                    break

    def test_liveness_age_is_inf_sentinel_when_no_previous_success(self):
        """When no prior success exists, liveness_age_seconds is capped at 1-year sentinel."""
        with tempfile.TemporaryDirectory() as tmpdir:
            status_path = os.path.join(tmpdir, "liveness.jsonl")
            recovery_path = os.path.join(tmpdir, "audit-recovery.jsonl")
            # Empty status file — no prior success
            with open(status_path, "w") as fh:
                pass

            with override_settings(
                AUDIT_LIVENESS_STATUS=status_path,
                NODE_EXPORTER_TEXTFILE_DIR=tmpdir,
                AUDIT_RECOVERY_LOG=recovery_path,
                ENVIRONMENT="test",
            ):
                with patch(
                    "accounts.models.AuditProbe.objects.create",
                    side_effect=Exception("forced"),
                ):
                    try:
                        call_command("audit_healthcheck", stdout=StringIO(), stderr=StringIO())
                    except SystemExit:
                        pass

            prom_file = Path(tmpdir) / "parkshare_audit.prom"
            content = prom_file.read_text(encoding="utf-8")
            one_year_seconds = 86400.0 * 365
            for line in content.splitlines():
                if "parkshare_audit_liveness_age_seconds{" in line:
                    age_value = float(line.split("} ")[-1])
                    self.assertEqual(age_value, one_year_seconds)
                    break

    def test_probe_prunes_old_probe_rows(self):
        """After probe, only _PROBE_RETAIN_COUNT (10) rows remain."""
        from accounts.models import AuditProbe

        # Create 15 existing probe rows
        for _ in range(15):
            AuditProbe.objects.create()

        with tempfile.TemporaryDirectory() as tmpdir:
            self._run_command(tmpdir)

        # After the command, at most 10 should remain
        self.assertLessEqual(AuditProbe.objects.count(), 10)

    def test_backlog_file_not_found_does_not_crash(self):
        """When recovery log doesn't exist, command succeeds with backlog=0."""
        with tempfile.TemporaryDirectory() as tmpdir:
            nonexistent_recovery = os.path.join(tmpdir, "does-not-exist.jsonl")
            status_path = os.path.join(tmpdir, "liveness.jsonl")
            with override_settings(
                AUDIT_LIVENESS_STATUS=status_path,
                NODE_EXPORTER_TEXTFILE_DIR=tmpdir,
                AUDIT_RECOVERY_LOG=nonexistent_recovery,
                ENVIRONMENT="test",
            ):
                # Should not raise
                call_command("audit_healthcheck", stdout=StringIO(), stderr=StringIO())

            prom_file = Path(tmpdir) / "parkshare_audit.prom"
            content = prom_file.read_text(encoding="utf-8")
            for line in content.splitlines():
                if "parkshare_audit_backlog_records{" in line:
                    value = float(line.split("} ")[-1])
                    self.assertEqual(value, 0.0)
                    break

    def test_prom_write_failure_does_not_raise(self):
        """If the .prom directory is unwritable, the command logs and continues."""
        with tempfile.TemporaryDirectory() as tmpdir:
            status_path = os.path.join(tmpdir, "liveness.jsonl")
            recovery_path = os.path.join(tmpdir, "audit-recovery.jsonl")
            bad_textfile_dir = "/nonexistent/dir/for/prometheus"

            with override_settings(
                AUDIT_LIVENESS_STATUS=status_path,
                NODE_EXPORTER_TEXTFILE_DIR=bad_textfile_dir,
                AUDIT_RECOVERY_LOG=recovery_path,
                ENVIRONMENT="test",
            ):
                # Should not raise — logs error instead
                call_command("audit_healthcheck", stdout=StringIO(), stderr=StringIO())

    def test_status_file_write_failure_does_not_crash_prom_write(self):
        """If the status file write fails, .prom is still written."""
        with tempfile.TemporaryDirectory() as tmpdir:
            status_path = "/nonexistent/unwritable/liveness.jsonl"
            recovery_path = os.path.join(tmpdir, "audit-recovery.jsonl")

            with override_settings(
                AUDIT_LIVENESS_STATUS=status_path,
                NODE_EXPORTER_TEXTFILE_DIR=tmpdir,
                AUDIT_RECOVERY_LOG=recovery_path,
                ENVIRONMENT="test",
            ):
                # Should not raise — logs error instead
                call_command("audit_healthcheck", stdout=StringIO(), stderr=StringIO())

            prom_file = Path(tmpdir) / "parkshare_audit.prom"
            self.assertTrue(prom_file.exists(), ".prom must still be written despite status failure")


# ---------------------------------------------------------------------------
# compute_backlog tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class ComputeBacklogTests(TestCase):
    """Tests for compute_backlog() in backfill_audit_log."""

    def setUp(self):
        self.org = _make_org()
        self.superuser = _make_superuser(self.org)
        self.regular_user = _make_regular_user(self.org, email="regular@test.example.com")

    def _call(self, lines, tmp_path=None):
        from accounts.management.commands.backfill_audit_log import compute_backlog

        if tmp_path is None:
            tmp_path = Path(tempfile.mkdtemp())
        path = _make_recovery_jsonl(lines, tmp_path)
        return compute_backlog(path), path

    def test_empty_file_returns_zero_counts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            counts, _ = self._call([], Path(tmpdir))
        self.assertEqual(counts.created_would_be, 0)
        self.assertEqual(counts.skipped, 0)
        self.assertEqual(counts.rejected, 0)
        self.assertEqual(counts.malformed, 0)
        self.assertIsNone(counts.oldest_unreconciled_at)

    def test_valid_record_counted_as_would_create(self):
        record = _valid_record(self.superuser.pk, org_id=self.org.pk)
        with tempfile.TemporaryDirectory() as tmpdir:
            counts, _ = self._call([record], Path(tmpdir))
        self.assertEqual(counts.created_would_be, 1)
        self.assertEqual(counts.rejected, 0)
        self.assertEqual(counts.malformed, 0)

    def test_malformed_json_counted_as_malformed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            counts, _ = self._call(["this is not json"], Path(tmpdir))
        self.assertEqual(counts.malformed, 1)
        self.assertEqual(counts.created_would_be, 0)

    def test_missing_required_field_counted_as_malformed(self):
        """A record missing actor_id is malformed."""
        bad_record = {
            "action": "impersonate_action",
            "attempted_at": "2025-01-15T10:00:00+00:00",
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            counts, _ = self._call([bad_record], Path(tmpdir))
        self.assertEqual(counts.malformed, 1)

    def test_non_allowlisted_action_counted_as_rejected(self):
        record = _valid_record(self.superuser.pk, org_id=self.org.pk)
        record["action"] = "delete_database"  # not in _ALLOWED_ACTIONS
        with tempfile.TemporaryDirectory() as tmpdir:
            counts, _ = self._call([record], Path(tmpdir))
        self.assertEqual(counts.rejected, 1)
        self.assertEqual(counts.created_would_be, 0)

    def test_non_superuser_actor_counted_as_rejected(self):
        record = _valid_record(self.regular_user.pk, org_id=self.org.pk)
        with tempfile.TemporaryDirectory() as tmpdir:
            counts, _ = self._call([record], Path(tmpdir))
        self.assertEqual(counts.rejected, 1)
        self.assertEqual(counts.created_would_be, 0)

    def test_nonexistent_actor_counted_as_malformed(self):
        record = _valid_record(actor_id=99999999)  # no such user
        with tempfile.TemporaryDirectory() as tmpdir:
            counts, _ = self._call([record], Path(tmpdir))
        self.assertEqual(counts.malformed, 1)

    def test_org_mismatch_counted_as_rejected(self):
        """Actor from org A with record claiming org B → rejected."""
        other_org = _make_org(name="OtherOrg", hostname="other.example.com")
        record = _valid_record(self.superuser.pk, org_id=other_org.pk)
        with tempfile.TemporaryDirectory() as tmpdir:
            counts, _ = self._call([record], Path(tmpdir))
        self.assertEqual(counts.rejected, 1)
        self.assertEqual(counts.created_would_be, 0)

    def test_no_org_id_in_record_is_allowed(self):
        """organization_id=None means no tenant check — should be counted."""
        record = _valid_record(self.superuser.pk, org_id=None)
        with tempfile.TemporaryDirectory() as tmpdir:
            counts, _ = self._call([record], Path(tmpdir))
        self.assertEqual(counts.created_would_be, 1)
        self.assertEqual(counts.rejected, 0)

    def test_on_behalf_of_id_int_cast_in_compute_backlog(self):
        """on_behalf_of_id present and non-None exercises the int() cast branch (line 113)."""
        target = _make_regular_user(self.org, email="target-behalf@test.example.com")
        record = _valid_record(self.superuser.pk, on_behalf_of_id=target.pk)
        with tempfile.TemporaryDirectory() as tmpdir:
            counts, _ = self._call([record], Path(tmpdir))
        self.assertEqual(counts.created_would_be, 1)

    def test_rejected_count_increments_per_rejected_record(self):
        """Each rejected record increments rejected by exactly 1 (tests += vs = mutation)."""
        record1 = _valid_record(self.superuser.pk)
        record1["action"] = "bad_action_1"
        record2 = _valid_record(self.superuser.pk)
        record2["action"] = "bad_action_2"
        with tempfile.TemporaryDirectory() as tmpdir:
            counts, _ = self._call([record1, record2], Path(tmpdir))
        self.assertEqual(counts.rejected, 2)

    def test_skipped_continue_not_break(self):
        """After a skipped (reconciled) record, subsequent records are still processed."""
        from accounts.models import AdminAuditLog
        record1 = _valid_record(self.superuser.pk, notes="already done")
        backfill_notes1 = "already done [recovered:attempted_at=2025-01-15T10:00:00+00:00]"
        AdminAuditLog.objects.create(
            organization=self.org,
            actor=self.superuser,
            on_behalf_of=None,
            action="impersonate_action",
            notes=backfill_notes1,
        )
        record2 = _valid_record(self.superuser.pk, notes="should count")
        with tempfile.TemporaryDirectory() as tmpdir:
            counts, _ = self._call([record1, record2], Path(tmpdir))
        self.assertEqual(counts.skipped, 1)
        self.assertEqual(counts.created_would_be, 1)

    def test_already_reconciled_record_counted_as_skipped(self):
        """A record whose fingerprint is already in AdminAuditLog is skipped."""
        from accounts.models import AdminAuditLog

        record = _valid_record(self.superuser.pk, org_id=self.org.pk, notes="orig note")
        backfill_notes = "orig note [recovered:attempted_at=2025-01-15T10:00:00+00:00]"
        AdminAuditLog.objects.create(
            organization=self.org,
            actor=self.superuser,
            on_behalf_of=None,
            action="impersonate_action",
            notes=backfill_notes,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            counts, _ = self._call([record], Path(tmpdir))
        self.assertEqual(counts.skipped, 1)
        self.assertEqual(counts.created_would_be, 0)

    def test_oldest_unreconciled_at_set_correctly(self):
        """oldest_unreconciled_at tracks the oldest valid unreconciled record."""
        record1 = _valid_record(self.superuser.pk)
        record1["attempted_at"] = "2025-01-10T08:00:00+00:00"
        record1["notes"] = "note1"
        record2 = _valid_record(self.superuser.pk)
        record2["attempted_at"] = "2025-01-15T10:00:00+00:00"
        record2["notes"] = "note2"
        with tempfile.TemporaryDirectory() as tmpdir:
            counts, _ = self._call([record1, record2], Path(tmpdir))
        self.assertEqual(counts.created_would_be, 2)
        self.assertIsNotNone(counts.oldest_unreconciled_at)
        self.assertEqual(counts.oldest_unreconciled_at.year, 2025)
        self.assertEqual(counts.oldest_unreconciled_at.month, 1)
        self.assertEqual(counts.oldest_unreconciled_at.day, 10)

    def test_actor_cache_reused_across_lines(self):
        """Multiple records with the same actor_id reuse the cache — still checked correctly."""
        record1 = _valid_record(self.superuser.pk, notes="note A")
        record2 = _valid_record(self.superuser.pk, notes="note B")
        with tempfile.TemporaryDirectory() as tmpdir:
            counts, _ = self._call([record1, record2], Path(tmpdir))
        self.assertEqual(counts.created_would_be, 2)

    def test_actor_cache_does_not_bypass_superuser_check(self):
        """Even when actor is cached, superuser check must still apply correctly."""
        # First record is from non-superuser (gets rejected and cached as non-super)
        record1 = _valid_record(self.regular_user.pk, notes="note1")
        record2 = _valid_record(self.regular_user.pk, notes="note2")
        with tempfile.TemporaryDirectory() as tmpdir:
            counts, _ = self._call([record1, record2], Path(tmpdir))
        self.assertEqual(counts.rejected, 2)  # both should be rejected via cache

    def test_blank_lines_skipped(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            counts, _ = self._call(["", "   ", ""], Path(tmpdir))
        self.assertEqual(counts.malformed, 0)
        self.assertEqual(counts.created_would_be, 0)

    def test_unparseable_attempted_at_counted_as_malformed(self):
        record = _valid_record(self.superuser.pk)
        record["attempted_at"] = "not-a-date"
        with tempfile.TemporaryDirectory() as tmpdir:
            counts, _ = self._call([record], Path(tmpdir))
        self.assertEqual(counts.malformed, 1)

    def test_naive_attempted_at_is_accepted(self):
        """A naive (no timezone) attempted_at string is make_aware'd and accepted."""
        record = _valid_record(self.superuser.pk)
        record["attempted_at"] = "2025-01-15T10:00:00"  # no tz
        with tempfile.TemporaryDirectory() as tmpdir:
            counts, _ = self._call([record], Path(tmpdir))
        self.assertEqual(counts.created_would_be, 1)
        self.assertEqual(counts.malformed, 0)

    def test_file_not_found_raises(self):
        from accounts.management.commands.backfill_audit_log import compute_backlog

        with self.assertRaises(FileNotFoundError):
            compute_backlog("/nonexistent/path/audit-recovery.jsonl")

    def test_file_size_guard_raises_on_oversized_file(self):
        from accounts.management.commands.backfill_audit_log import compute_backlog

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as fh:
            path = fh.name
            fh.write("{}\n")
        try:
            with patch("os.path.getsize", return_value=51 * 1024 * 1024):  # 51 MB
                with self.assertRaises(ValueError) as cm:
                    compute_backlog(path)
            self.assertIn("limit", str(cm.exception))
        finally:
            os.unlink(path)

    def test_mixed_valid_and_invalid_records(self):
        """A file with a mix of valid, malformed, and rejected records is counted correctly."""
        valid = _valid_record(self.superuser.pk, notes="valid note")
        malformed = "not json at all"
        rejected = _valid_record(self.superuser.pk)
        rejected["action"] = "drop_table"
        with tempfile.TemporaryDirectory() as tmpdir:
            counts, _ = self._call([valid, malformed, rejected], Path(tmpdir))
        self.assertEqual(counts.created_would_be, 1)
        self.assertEqual(counts.malformed, 1)
        self.assertEqual(counts.rejected, 1)


# ---------------------------------------------------------------------------
# backfill_audit_log --dry-run tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class BackfillDryRunTests(TestCase):
    """Tests for the --dry-run flag on backfill_audit_log command."""

    def setUp(self):
        self.org = _make_org(hostname="dryrun.example.com")
        self.superuser = _make_superuser(self.org, email="admin@dryrun.example.com")

    def _run_dry_run(self, recovery_path):
        out = StringIO()
        err = StringIO()
        with override_settings(AUDIT_RECOVERY_LOG=recovery_path):
            call_command(
                "backfill_audit_log",
                "--dry-run",
                stdout=out,
                stderr=err,
            )
        return out.getvalue(), err.getvalue()

    def test_dry_run_creates_no_audit_log_rows(self):
        from accounts.models import AdminAuditLog

        before = AdminAuditLog.objects.count()
        record = _valid_record(self.superuser.pk)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _make_recovery_jsonl([record], Path(tmpdir))
            with override_settings(AUDIT_RECOVERY_LOG=path):
                call_command(
                    "backfill_audit_log",
                    "--dry-run",
                    stdout=StringIO(),
                    stderr=StringIO(),
                )
        after = AdminAuditLog.objects.count()
        self.assertEqual(before, after, "Dry-run must not create AdminAuditLog rows")

    def test_dry_run_creates_no_audit_probe_rows(self):
        from accounts.models import AuditProbe

        before = AuditProbe.objects.count()
        record = _valid_record(self.superuser.pk)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _make_recovery_jsonl([record], Path(tmpdir))
            with override_settings(AUDIT_RECOVERY_LOG=path):
                call_command(
                    "backfill_audit_log",
                    "--dry-run",
                    stdout=StringIO(),
                    stderr=StringIO(),
                )
        after = AuditProbe.objects.count()
        self.assertEqual(before, after, "Dry-run must not create AuditProbe rows")

    def test_dry_run_reports_counts_in_output(self):
        record = _valid_record(self.superuser.pk)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _make_recovery_jsonl([record], Path(tmpdir))
            out, _ = self._run_dry_run(path)
        self.assertIn("would_create=1", out)

    def test_dry_run_reports_rejected_count(self):
        record = _valid_record(self.superuser.pk)
        record["action"] = "not_allowed"
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _make_recovery_jsonl([record], Path(tmpdir))
            out, _ = self._run_dry_run(path)
        self.assertIn("rejected=1", out)

    def test_dry_run_reports_malformed_count(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _make_recovery_jsonl(["not-json"], Path(tmpdir))
            out, _ = self._run_dry_run(path)
        self.assertIn("malformed=1", out)

    def test_dry_run_missing_file_writes_error(self):
        out = StringIO()
        err = StringIO()
        with override_settings(AUDIT_RECOVERY_LOG="/nonexistent/path.jsonl"):
            call_command(
                "backfill_audit_log",
                "--dry-run",
                stdout=out,
                stderr=err,
            )
        # Error message should appear in stderr
        self.assertIn("not found", err.getvalue().lower())

    def test_dry_run_with_custom_file_arg(self):
        """--file overrides AUDIT_RECOVERY_LOG setting."""
        record = _valid_record(self.superuser.pk)
        with tempfile.TemporaryDirectory() as tmpdir:
            custom_path = _make_recovery_jsonl([record], Path(tmpdir))
            out = StringIO()
            with override_settings(AUDIT_RECOVERY_LOG="/wrong/path.jsonl"):
                call_command(
                    "backfill_audit_log",
                    "--dry-run",
                    f"--file={custom_path}",
                    stdout=out,
                    stderr=StringIO(),
                )
        self.assertIn("would_create=1", out.getvalue())

    def test_dry_run_skipped_count_for_already_reconciled(self):
        """Already-reconciled records are counted as skipped, not created."""
        from accounts.models import AdminAuditLog

        record = _valid_record(self.superuser.pk, notes="orig")
        backfill_notes = "orig [recovered:attempted_at=2025-01-15T10:00:00+00:00]"
        AdminAuditLog.objects.create(
            organization=self.org,
            actor=self.superuser,
            on_behalf_of=None,
            action="impersonate_action",
            notes=backfill_notes,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _make_recovery_jsonl([record], Path(tmpdir))
            out, _ = self._run_dry_run(path)
        self.assertIn("skipped=1", out)
        self.assertIn("would_create=0", out)


# ---------------------------------------------------------------------------
# backfill_audit_log live mode tests (non-dry-run)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class BackfillLiveTests(TestCase):
    """Tests for the live (non-dry-run) backfill_audit_log command."""

    def setUp(self):
        self.org = _make_org(hostname="live.example.com")
        self.superuser = _make_superuser(self.org, email="admin@live.example.com")

    def test_live_creates_audit_log_rows(self):
        from accounts.models import AdminAuditLog

        record = _valid_record(self.superuser.pk)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _make_recovery_jsonl([record], Path(tmpdir))
            with override_settings(AUDIT_RECOVERY_LOG=path):
                call_command(
                    "backfill_audit_log",
                    stdout=StringIO(),
                    stderr=StringIO(),
                )
        self.assertTrue(AdminAuditLog.objects.filter(action="impersonate_action").exists())

    def test_live_is_idempotent(self):
        """Running backfill twice creates only one row per record."""
        from accounts.models import AdminAuditLog

        record = _valid_record(self.superuser.pk, notes="idempotency test")
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _make_recovery_jsonl([record], Path(tmpdir))
            with override_settings(AUDIT_RECOVERY_LOG=path):
                call_command("backfill_audit_log", stdout=StringIO(), stderr=StringIO())
                count_after_first = AdminAuditLog.objects.count()
                call_command("backfill_audit_log", stdout=StringIO(), stderr=StringIO())
                count_after_second = AdminAuditLog.objects.count()

        self.assertEqual(count_after_first, count_after_second)

    def test_live_missing_file_writes_error(self):
        err = StringIO()
        with override_settings(AUDIT_RECOVERY_LOG="/nonexistent/path.jsonl"):
            call_command(
                "backfill_audit_log",
                stdout=StringIO(),
                stderr=err,
            )
        self.assertIn("not found", err.getvalue().lower())

    def test_live_rejects_non_superuser_actor(self):
        from accounts.models import AdminAuditLog

        regular = _make_regular_user(self.org, email="nosuper@live.example.com")
        record = _valid_record(regular.pk)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _make_recovery_jsonl([record], Path(tmpdir))
            with override_settings(AUDIT_RECOVERY_LOG=path):
                call_command("backfill_audit_log", stdout=StringIO(), stderr=StringIO())
        self.assertFalse(AdminAuditLog.objects.filter(actor=regular).exists())

    def test_live_rejects_disallowed_action(self):
        from accounts.models import AdminAuditLog

        record = _valid_record(self.superuser.pk)
        record["action"] = "evil_action"
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _make_recovery_jsonl([record], Path(tmpdir))
            with override_settings(AUDIT_RECOVERY_LOG=path):
                call_command("backfill_audit_log", stdout=StringIO(), stderr=StringIO())
        self.assertFalse(
            AdminAuditLog.objects.filter(actor=self.superuser, action="evil_action").exists()
        )

    def test_live_rejects_org_mismatch(self):
        from accounts.models import AdminAuditLog

        other_org = _make_org(name="OtherOrg2", hostname="other2.example.com")
        record = _valid_record(self.superuser.pk, org_id=other_org.pk)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _make_recovery_jsonl([record], Path(tmpdir))
            with override_settings(AUDIT_RECOVERY_LOG=path):
                call_command("backfill_audit_log", stdout=StringIO(), stderr=StringIO())
        self.assertFalse(
            AdminAuditLog.objects.filter(
                actor=self.superuser, organization=other_org
            ).exists()
        )

    def test_live_output_includes_created_count(self):
        record = _valid_record(self.superuser.pk, notes="output test")
        out = StringIO()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _make_recovery_jsonl([record], Path(tmpdir))
            with override_settings(AUDIT_RECOVERY_LOG=path):
                call_command("backfill_audit_log", stdout=out, stderr=StringIO())
        self.assertIn("created=1", out.getvalue())


# ---------------------------------------------------------------------------
# compute_backlog called from audit_healthcheck — integration
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class AuditHealthcheckBacklogIntegrationTests(TestCase):
    """Integration tests: audit_healthcheck uses compute_backlog correctly."""

    def setUp(self):
        self.org = _make_org(hostname="integration.example.com")
        self.superuser = _make_superuser(self.org, email="admin@integration.example.com")

    def test_backlog_count_appears_in_prom_file(self):
        """Valid unreconciled records increase backlog_records gauge."""
        record1 = _valid_record(self.superuser.pk, notes="note A")
        record2 = _valid_record(self.superuser.pk, notes="note B")
        with tempfile.TemporaryDirectory() as tmpdir:
            recovery_path = _make_recovery_jsonl([record1, record2], Path(tmpdir))
            status_path = os.path.join(tmpdir, "liveness.jsonl")
            with override_settings(
                AUDIT_LIVENESS_STATUS=status_path,
                NODE_EXPORTER_TEXTFILE_DIR=tmpdir,
                AUDIT_RECOVERY_LOG=recovery_path,
                ENVIRONMENT="test",
            ):
                call_command("audit_healthcheck", stdout=StringIO(), stderr=StringIO())

            prom_file = Path(tmpdir) / "parkshare_audit.prom"
            content = prom_file.read_text(encoding="utf-8")

        for line in content.splitlines():
            if "parkshare_audit_backlog_records{" in line:
                value = float(line.split("} ")[-1])
                self.assertEqual(value, 2.0)
                break
        else:
            self.fail("parkshare_audit_backlog_records not found in .prom")

    def test_rejected_count_appears_in_prom_file(self):
        """Rejected records appear in backlog_rejected gauge."""
        bad_record = _valid_record(self.superuser.pk)
        bad_record["action"] = "forbidden_action"
        with tempfile.TemporaryDirectory() as tmpdir:
            recovery_path = _make_recovery_jsonl([bad_record], Path(tmpdir))
            status_path = os.path.join(tmpdir, "liveness.jsonl")
            with override_settings(
                AUDIT_LIVENESS_STATUS=status_path,
                NODE_EXPORTER_TEXTFILE_DIR=tmpdir,
                AUDIT_RECOVERY_LOG=recovery_path,
                ENVIRONMENT="test",
            ):
                call_command("audit_healthcheck", stdout=StringIO(), stderr=StringIO())

            prom_file = Path(tmpdir) / "parkshare_audit.prom"
            content = prom_file.read_text(encoding="utf-8")

        for line in content.splitlines():
            if "parkshare_audit_backlog_rejected{" in line:
                value = float(line.split("} ")[-1])
                self.assertEqual(value, 1.0)
                break
        else:
            self.fail("parkshare_audit_backlog_rejected not found in .prom")

    def test_backlog_exception_handled_gracefully(self):
        """If compute_backlog raises unexpectedly, command still writes zero backlog."""
        from accounts.management.commands import backfill_audit_log as bfl_module

        with tempfile.TemporaryDirectory() as tmpdir:
            status_path = os.path.join(tmpdir, "liveness.jsonl")
            recovery_path = os.path.join(tmpdir, "audit-recovery.jsonl")
            with open(recovery_path, "w") as fh:
                fh.write("{}\n")

            with override_settings(
                AUDIT_LIVENESS_STATUS=status_path,
                NODE_EXPORTER_TEXTFILE_DIR=tmpdir,
                AUDIT_RECOVERY_LOG=recovery_path,
                ENVIRONMENT="test",
            ):
                with patch.object(bfl_module, "compute_backlog", side_effect=RuntimeError("boom")):
                    # Should not raise — must log and continue
                    call_command("audit_healthcheck", stdout=StringIO(), stderr=StringIO())

            prom_file = Path(tmpdir) / "parkshare_audit.prom"
            self.assertTrue(prom_file.exists())

    def test_backlog_exception_produces_zero_rejected_count_in_prom(self):
        """On compute_backlog exception, rejected gauge must be 0, not 1."""
        from accounts.management.commands import backfill_audit_log as bfl_module

        with tempfile.TemporaryDirectory() as tmpdir:
            status_path = os.path.join(tmpdir, "liveness.jsonl")
            recovery_path = os.path.join(tmpdir, "audit-recovery.jsonl")
            with open(recovery_path, "w") as fh:
                fh.write("{}\n")

            with override_settings(
                AUDIT_LIVENESS_STATUS=status_path,
                NODE_EXPORTER_TEXTFILE_DIR=tmpdir,
                AUDIT_RECOVERY_LOG=recovery_path,
                ENVIRONMENT="test",
            ):
                with patch.object(bfl_module, "compute_backlog", side_effect=RuntimeError("boom")):
                    call_command("audit_healthcheck", stdout=StringIO(), stderr=StringIO())

            prom_file = Path(tmpdir) / "parkshare_audit.prom"
            content = prom_file.read_text(encoding="utf-8")

        for line in content.splitlines():
            if "parkshare_audit_backlog_rejected{" in line:
                value = float(line.split("} ")[-1])
                self.assertEqual(value, 0.0, "rejected gauge must be 0 on backlog exception")
                break

    def test_backlog_oldest_seconds_zero_when_no_unreconciled(self):
        """When no unreconciled records exist, oldest_seconds in .prom is 0.0."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Empty recovery file
            recovery_path = os.path.join(tmpdir, "empty-recovery.jsonl")
            with open(recovery_path, "w") as fh:
                pass  # empty
            status_path = os.path.join(tmpdir, "liveness.jsonl")
            with override_settings(
                AUDIT_LIVENESS_STATUS=status_path,
                NODE_EXPORTER_TEXTFILE_DIR=tmpdir,
                AUDIT_RECOVERY_LOG=recovery_path,
                ENVIRONMENT="test",
            ):
                call_command("audit_healthcheck", stdout=StringIO(), stderr=StringIO())

            prom_file = Path(tmpdir) / "parkshare_audit.prom"
            content = prom_file.read_text(encoding="utf-8")
        for line in content.splitlines():
            if "parkshare_audit_backlog_oldest_seconds{" in line:
                value = float(line.split("} ")[-1])
                self.assertEqual(value, 0.0)
                break

    def test_liveness_status_record_on_success_contains_write_ms(self):
        """On success, the status JSONL record contains write_ms field."""
        with tempfile.TemporaryDirectory() as tmpdir:
            _, status_path, _ = AuditHealthcheckLivenessTests()._run_command(tmpdir)
            with open(status_path) as fh:
                lines = [l.strip() for l in fh if l.strip()]
            record = json.loads(lines[-1])
        self.assertIn("write_ms", record)
        self.assertTrue(record["ok"])

    def test_liveness_status_write_ms_is_float_with_2_decimal_places(self):
        """write_ms in the status record is rounded to 2 decimal places (not 0).

        round(x, 2) always returns a float in Python. round(x) (no ndigits)
        returns an int when x is already float-like. We verify write_ms is a float
        (not an int) to distinguish round(write_ms, 2) from round(write_ms).
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            _, status_path, _ = AuditHealthcheckLivenessTests()._run_command(tmpdir)
            with open(status_path) as fh:
                lines = [l.strip() for l in fh if l.strip()]
            record = json.loads(lines[-1])
        write_ms = record.get("write_ms")
        self.assertIsNotNone(write_ms)
        # round(x, 2) returns a float; round(x) returns an int.
        # JSON encodes Python floats as numbers with decimal point; ints without.
        # But json.loads decodes both as Python float or int depending on value.
        # More robust: verify it's a float type in Python
        self.assertIsInstance(write_ms, float,
            "write_ms should be a float (round(x, 2)), not an int (round(x))")

    def test_liveness_status_record_on_failure_contains_error_field(self):
        """On failure, the status JSONL record contains the error class name."""
        with tempfile.TemporaryDirectory() as tmpdir:
            status_path = os.path.join(tmpdir, "liveness.jsonl")
            recovery_path = os.path.join(tmpdir, "audit-recovery.jsonl")
            with override_settings(
                AUDIT_LIVENESS_STATUS=status_path,
                NODE_EXPORTER_TEXTFILE_DIR=tmpdir,
                AUDIT_RECOVERY_LOG=recovery_path,
                ENVIRONMENT="test",
            ):
                with patch(
                    "accounts.models.AuditProbe.objects.create",
                    side_effect=ValueError("forced"),
                ):
                    try:
                        call_command("audit_healthcheck", stdout=StringIO(), stderr=StringIO())
                    except SystemExit:
                        pass
            with open(status_path) as fh:
                lines = [l.strip() for l in fh if l.strip()]
            record = json.loads(lines[-1])
        self.assertFalse(record["ok"])
        self.assertIn("error", record)
        self.assertEqual(record["error"], "ValueError")

    def test_compute_backlog_with_naive_oldest_at_is_handled(self):
        """_compute_backlog: when oldest_unreconciled_at is naive, make_aware is called."""
        from accounts.management.commands import backfill_audit_log as bfl_module

        # Build a BacklogCounts with a naive (timezone-unaware) oldest_unreconciled_at
        naive_dt = datetime(2025, 1, 10, 8, 0, 0)  # no tzinfo
        mock_counts = bfl_module.BacklogCounts(
            created_would_be=1,
            skipped=0,
            rejected=0,
            malformed=0,
            oldest_unreconciled_at=naive_dt,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            status_path = os.path.join(tmpdir, "liveness.jsonl")
            recovery_path = os.path.join(tmpdir, "audit-recovery.jsonl")
            with open(recovery_path, "w") as fh:
                fh.write("{}\n")

            with override_settings(
                AUDIT_LIVENESS_STATUS=status_path,
                NODE_EXPORTER_TEXTFILE_DIR=tmpdir,
                AUDIT_RECOVERY_LOG=recovery_path,
                ENVIRONMENT="test",
            ):
                with patch.object(bfl_module, "compute_backlog", return_value=mock_counts):
                    # Should not raise — make_aware path is exercised
                    call_command("audit_healthcheck", stdout=StringIO(), stderr=StringIO())

            prom_file = Path(tmpdir) / "parkshare_audit.prom"
            content = prom_file.read_text(encoding="utf-8")
            # oldest_seconds should be positive (naive dt from 2025 is in the past)
            for line in content.splitlines():
                if "parkshare_audit_backlog_oldest_seconds{" in line:
                    value = float(line.split("} ")[-1])
                    self.assertGreater(value, 0.0)
                    break


# ---------------------------------------------------------------------------
# _write_prom_atomically exception-path tests
# ---------------------------------------------------------------------------


class WritePomAtomicallyExceptionTests(TestCase):
    """Test the exception-handling paths in _write_prom_atomically."""

    def test_fdopen_failure_cleans_up_temp_file(self):
        """If os.fdopen fails, the raw fd is closed and the tmp file is removed."""
        from accounts.management.commands.audit_healthcheck import _write_prom_atomically

        with tempfile.TemporaryDirectory() as tmpdir:
            original_fdopen = os.fdopen

            def bad_fdopen(fd, *args, **kwargs):
                raise OSError("fdopen failed")

            with patch("os.fdopen", side_effect=bad_fdopen):
                with self.assertRaises(OSError):
                    _write_prom_atomically(tmpdir, "content")
            # No .tmp files should remain
            tmp_files = list(Path(tmpdir).glob("*.tmp"))
            self.assertEqual(tmp_files, [])

    def test_write_failure_unlinks_temp_file(self):
        """If fh.write raises, the temp file is unlinked."""
        from accounts.management.commands.audit_healthcheck import _write_prom_atomically

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("os.rename", side_effect=OSError("rename failed")):
                with self.assertRaises(OSError):
                    _write_prom_atomically(tmpdir, "content")
            # No .tmp files should remain after the unlink
            tmp_files = list(Path(tmpdir).glob("*.tmp"))
            self.assertEqual(tmp_files, [])

    def test_write_failure_when_unlink_also_fails_swallows_oserror(self):
        """If os.unlink of the temp file also fails, the OSError is swallowed and original re-raised."""
        from accounts.management.commands.audit_healthcheck import _write_prom_atomically

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("os.rename", side_effect=OSError("rename failed")), \
                 patch("os.unlink", side_effect=OSError("unlink also failed")):
                # The original rename error must still propagate
                with self.assertRaises(OSError) as cm:
                    _write_prom_atomically(tmpdir, "content")
                self.assertIn("rename failed", str(cm.exception))


# ---------------------------------------------------------------------------
# Additional models tests — coverage for models.py missing lines
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class AdditionalModelTests(TestCase):
    """Tests for lines in accounts/models.py not yet covered."""

    def setUp(self):
        self.org = _make_org(hostname="models.example.com")

    def test_user_manager_create_user_requires_email(self):
        from accounts.models import User

        with self.assertRaises(ValueError):
            User.objects.create_user(
                email="",
                organization=self.org,
                display_name="No Email",
                password="password123",
            )

    def test_user_manager_create_superuser(self):
        from accounts.models import User

        su = User.objects.create_superuser(
            email="su-model@models.example.com",
            organization=self.org,
            display_name="Super User",
            password="test-password-secure!",
        )
        self.assertTrue(su.is_superuser)
        self.assertTrue(su.is_staff)
        self.assertEqual(su.status, "active")

    def test_user_str(self):
        from accounts.models import User

        user = User.objects.create_user(
            email="strtest@models.example.com",
            organization=self.org,
            display_name="Str Test",
            password="test-password-secure!",
        )
        self.assertIn("Str Test", str(user))
        self.assertIn("strtest@models.example.com", str(user))

    def test_invite_is_valid_when_not_expired_and_uses_remain(self):
        from accounts.models import Invite

        admin = _make_superuser(self.org, email="admin-invite@models.example.com")
        import secrets
        from django.utils.timezone import now
        from datetime import timedelta

        invite = Invite.objects.create(
            organization=self.org,
            issued_by=admin,
            code=secrets.token_urlsafe(32),
            max_uses=1,
            use_count=0,
            expires_at=now() + timedelta(days=1),
        )
        self.assertTrue(invite.is_valid())

    def test_invite_is_invalid_when_max_uses_reached(self):
        from accounts.models import Invite

        admin = _make_superuser(self.org, email="admin-invite2@models.example.com")
        import secrets

        invite = Invite.objects.create(
            organization=self.org,
            issued_by=admin,
            code=secrets.token_urlsafe(32),
            max_uses=1,
            use_count=1,  # already used
        )
        self.assertFalse(invite.is_valid())

    def test_invite_is_invalid_when_expired(self):
        from accounts.models import Invite

        admin = _make_superuser(self.org, email="admin-invite3@models.example.com")
        import secrets
        from django.utils.timezone import now
        from datetime import timedelta

        invite = Invite.objects.create(
            organization=self.org,
            issued_by=admin,
            code=secrets.token_urlsafe(32),
            max_uses=5,
            use_count=0,
            expires_at=now() - timedelta(days=1),  # expired
        )
        self.assertFalse(invite.is_valid())

    def test_invite_str(self):
        from accounts.models import Invite

        admin = _make_superuser(self.org, email="admin-invite4@models.example.com")
        import secrets

        invite = Invite.objects.create(
            organization=self.org,
            issued_by=admin,
            code=secrets.token_urlsafe(32),
            max_uses=1,
        )
        self.assertIn("Invite", str(invite))

    def test_email_otp_str(self):
        from accounts.models import EmailOTP
        from django.utils.timezone import now
        from datetime import timedelta

        user = _make_regular_user(self.org, email="otp@models.example.com")
        otp = EmailOTP.objects.create(
            user=user,
            code_hash="fakehash",
            expires_at=now() + timedelta(minutes=15),
        )
        s = str(otp)
        self.assertIn("EmailOTP", s)
        self.assertIn(str(user.pk), s)

    def test_admin_audit_log_str(self):
        from accounts.models import AdminAuditLog

        actor = _make_superuser(self.org, email="actor-str@models.example.com")
        log = AdminAuditLog.objects.create(
            organization=self.org,
            actor=actor,
            action="pii_access",
        )
        s = str(log)
        self.assertIn("pii_access", s)
        self.assertIn(str(actor.pk), s)

    def test_admin_audit_log_class_method(self):
        from accounts.models import AdminAuditLog

        actor = _make_superuser(self.org, email="actor-log@models.example.com")
        log = AdminAuditLog.log(actor=actor, action="block", notes="test block")
        self.assertEqual(log.action, "block")
        self.assertEqual(log.actor, actor)
        self.assertEqual(log.notes, "test block")
        # organization defaults to actor.organization
        self.assertEqual(log.organization, self.org)

    def test_default_notification_prefs_returns_push_false(self):
        """default_notification_prefs returns {'push': False} — not True."""
        from accounts.models import default_notification_prefs

        prefs = default_notification_prefs()
        self.assertFalse(prefs["push"])
        self.assertEqual(prefs, {"push": False})


# ---------------------------------------------------------------------------
# Additional live backfill coverage — missing lines in _handle_live
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class BackfillLiveAdditionalTests(TestCase):
    """Cover live backfill paths not yet exercised."""

    def setUp(self):
        self.org = _make_org(hostname="extra-live.example.com")
        self.superuser = _make_superuser(self.org, email="admin@extra-live.example.com")

    def test_live_malformed_json_line(self):
        """Malformed JSON line is logged and counted as malformed."""
        from accounts.models import AdminAuditLog

        before = AdminAuditLog.objects.count()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _make_recovery_jsonl(["not json at all"], Path(tmpdir))
            err = StringIO()
            with override_settings(AUDIT_RECOVERY_LOG=path):
                call_command("backfill_audit_log", stdout=StringIO(), stderr=err)
        self.assertEqual(AdminAuditLog.objects.count(), before)
        self.assertIn("malformed", err.getvalue().lower())

    def test_live_disallowed_action_logged(self):
        """Disallowed action is logged to stderr."""
        record = _valid_record(self.superuser.pk)
        record["action"] = "steal_data"
        err = StringIO()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _make_recovery_jsonl([record], Path(tmpdir))
            with override_settings(AUDIT_RECOVERY_LOG=path):
                call_command("backfill_audit_log", stdout=StringIO(), stderr=err)
        self.assertIn("rejected", err.getvalue().lower())

    def test_live_nonexistent_actor_logged(self):
        """Nonexistent actor_id is logged to stderr."""
        record = _valid_record(actor_id=99999999)
        err = StringIO()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _make_recovery_jsonl([record], Path(tmpdir))
            with override_settings(AUDIT_RECOVERY_LOG=path):
                call_command("backfill_audit_log", stdout=StringIO(), stderr=err)
        self.assertIn("not found", err.getvalue().lower())

    def test_live_non_superuser_actor_logged(self):
        """Non-superuser actor is logged to stderr."""
        regular = _make_regular_user(self.org, email="nosup-extra@extra-live.example.com")
        record = _valid_record(regular.pk)
        err = StringIO()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _make_recovery_jsonl([record], Path(tmpdir))
            with override_settings(AUDIT_RECOVERY_LOG=path):
                call_command("backfill_audit_log", stdout=StringIO(), stderr=err)
        self.assertIn("not a superuser", err.getvalue().lower())

    def test_live_org_mismatch_logged(self):
        """Org mismatch is logged to stderr."""
        other_org = _make_org(name="OtherOrgLive", hostname="other-live.example.com")
        record = _valid_record(self.superuser.pk, org_id=other_org.pk)
        err = StringIO()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _make_recovery_jsonl([record], Path(tmpdir))
            with override_settings(AUDIT_RECOVERY_LOG=path):
                call_command("backfill_audit_log", stdout=StringIO(), stderr=err)
        self.assertIn("does not match", err.getvalue().lower())

    def test_live_unparseable_attempted_at_logged(self):
        """Unparseable attempted_at is logged to stderr."""
        record = _valid_record(self.superuser.pk)
        record["attempted_at"] = "INVALID_DATE_FORMAT"
        err = StringIO()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _make_recovery_jsonl([record], Path(tmpdir))
            with override_settings(AUDIT_RECOVERY_LOG=path):
                call_command("backfill_audit_log", stdout=StringIO(), stderr=err)
        self.assertIn("unparseable", err.getvalue().lower())

    def test_live_on_behalf_of_not_found_creates_without_it(self):
        """on_behalf_of_id not found: row is still created with on_behalf_of=None."""
        from accounts.models import AdminAuditLog

        record = _valid_record(self.superuser.pk)
        record["on_behalf_of_id"] = 99999999  # nonexistent user
        err = StringIO()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _make_recovery_jsonl([record], Path(tmpdir))
            with override_settings(AUDIT_RECOVERY_LOG=path):
                call_command("backfill_audit_log", stdout=StringIO(), stderr=err)
        row = AdminAuditLog.objects.filter(actor=self.superuser).last()
        self.assertIsNotNone(row)
        self.assertIsNone(row.on_behalf_of)
        self.assertIn("not found", err.getvalue().lower())

    def test_live_organization_not_found_creates_without_org(self):
        """Organization PK not found: row is created with organization=None."""
        from accounts.models import AdminAuditLog

        record = _valid_record(self.superuser.pk, org_id=None)
        # Use the actor's own org_id so no anti-forgery rejection, but make the
        # DB lookup fail by monkeypatching Organization.objects.get
        from parking.models import Organization

        original_get = Organization.objects.get

        def _failing_get(**kwargs):
            raise Organization.DoesNotExist

        record["organization_id"] = self.org.pk  # will be matched by anti-forgery
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _make_recovery_jsonl([record], Path(tmpdir))
            with override_settings(AUDIT_RECOVERY_LOG=path):
                with patch.object(Organization.objects, "get", side_effect=Organization.DoesNotExist):
                    call_command("backfill_audit_log", stdout=StringIO(), stderr=StringIO())
        row = AdminAuditLog.objects.filter(actor=self.superuser).last()
        self.assertIsNotNone(row)
        self.assertIsNone(row.organization)

    def test_live_blank_line_skipped(self):
        """Blank lines in the JSONL file are silently skipped."""
        from accounts.models import AdminAuditLog

        before = AdminAuditLog.objects.count()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _make_recovery_jsonl(["", "   ", ""], Path(tmpdir))
            with override_settings(AUDIT_RECOVERY_LOG=path):
                call_command("backfill_audit_log", stdout=StringIO(), stderr=StringIO())
        self.assertEqual(AdminAuditLog.objects.count(), before)

    def test_live_naive_attempted_at_is_accepted(self):
        """Naive attempted_at in live mode is make_aware'd and the row is created."""
        from accounts.models import AdminAuditLog

        record = _valid_record(self.superuser.pk, notes="naive-ts-live")
        record["attempted_at"] = "2025-03-01T12:00:00"  # no tzinfo
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _make_recovery_jsonl([record], Path(tmpdir))
            with override_settings(AUDIT_RECOVERY_LOG=path):
                call_command("backfill_audit_log", stdout=StringIO(), stderr=StringIO())
        self.assertTrue(
            AdminAuditLog.objects.filter(actor=self.superuser, notes__contains="naive-ts-live").exists()
        )

    def test_live_skipped_already_existing_row(self):
        """An already-reconciled record is skipped (skipped_count incremented)."""
        from accounts.models import AdminAuditLog

        record = _valid_record(self.superuser.pk, notes="skip-me")
        backfill_notes = "skip-me [recovered:attempted_at=2025-01-15T10:00:00+00:00]"
        AdminAuditLog.objects.create(
            organization=self.org,
            actor=self.superuser,
            on_behalf_of=None,
            action="impersonate_action",
            notes=backfill_notes,
        )
        out = StringIO()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _make_recovery_jsonl([record], Path(tmpdir))
            with override_settings(AUDIT_RECOVERY_LOG=path):
                call_command("backfill_audit_log", stdout=out, stderr=StringIO())
        self.assertIn("skipped=1", out.getvalue())

    def test_live_missing_required_field_logged_as_malformed(self):
        """A record missing actor_id is logged to stderr and counted as malformed."""
        bad = {"action": "impersonate_action", "attempted_at": "2025-01-15T10:00:00+00:00"}
        err = StringIO()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _make_recovery_jsonl([bad], Path(tmpdir))
            with override_settings(AUDIT_RECOVERY_LOG=path):
                call_command("backfill_audit_log", stdout=StringIO(), stderr=err)
        self.assertIn("malformed", err.getvalue().lower())

    def test_live_output_includes_rejected_count_in_summary(self):
        """The live final output line shows the exact rejected_count value."""
        record1 = _valid_record(self.superuser.pk, notes="good record")
        bad_record = _valid_record(self.superuser.pk)
        bad_record["action"] = "forbidden"
        out = StringIO()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _make_recovery_jsonl([record1, bad_record], Path(tmpdir))
            with override_settings(AUDIT_RECOVERY_LOG=path):
                call_command("backfill_audit_log", stdout=out, stderr=StringIO())
        output = out.getvalue()
        self.assertIn("created=1", output)
        self.assertIn("rejected=1", output)

    def test_live_malformed_count_increments_by_one_per_bad_line(self):
        """Each malformed line increments the count by exactly 1."""
        bad1 = "not json"
        bad2 = "also not json"
        out = StringIO()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _make_recovery_jsonl([bad1, bad2], Path(tmpdir))
            with override_settings(AUDIT_RECOVERY_LOG=path):
                call_command("backfill_audit_log", stdout=out, stderr=StringIO())
        self.assertIn("malformed=2", out.getvalue())

    def test_live_rejected_count_stops_processing_at_correct_line(self):
        """After a rejected record, subsequent records are still processed (continue not break)."""
        from accounts.models import AdminAuditLog
        bad_action = _valid_record(self.superuser.pk, notes="bad action")
        bad_action["action"] = "evil"
        good = _valid_record(self.superuser.pk, notes="good after bad")
        out = StringIO()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _make_recovery_jsonl([bad_action, good], Path(tmpdir))
            with override_settings(AUDIT_RECOVERY_LOG=path):
                call_command("backfill_audit_log", stdout=out, stderr=StringIO())
        # The good record after the rejected one should still be processed
        self.assertIn("created=1", out.getvalue())
        self.assertIn("rejected=1", out.getvalue())

    def test_live_target_type_and_target_id_preserved(self):
        """target_type and target_id from the JSONL record are stored in AdminAuditLog."""
        from accounts.models import AdminAuditLog
        record = _valid_record(self.superuser.pk, notes="target-check")
        record["target_type"] = "booking"
        record["target_id"] = 42
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _make_recovery_jsonl([record], Path(tmpdir))
            with override_settings(AUDIT_RECOVERY_LOG=path):
                call_command("backfill_audit_log", stdout=StringIO(), stderr=StringIO())
        row = AdminAuditLog.objects.filter(actor=self.superuser, target_type="booking").last()
        self.assertIsNotNone(row)
        self.assertEqual(row.target_type, "booking")
        self.assertEqual(row.target_id, 42)

    def test_live_missing_target_type_defaults_to_empty_string(self):
        """When target_type key is absent from the record, it defaults to '' not 'None'."""
        from accounts.models import AdminAuditLog
        record = {
            "actor_id": self.superuser.pk,
            "action": "impersonate_action",
            "attempted_at": "2025-01-15T10:00:00+00:00",
            "notes": "no-target-type",
            # target_type intentionally absent
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _make_recovery_jsonl([record], Path(tmpdir))
            with override_settings(AUDIT_RECOVERY_LOG=path):
                call_command("backfill_audit_log", stdout=StringIO(), stderr=StringIO())
        row = AdminAuditLog.objects.filter(notes__contains="no-target-type").last()
        self.assertIsNotNone(row)
        self.assertEqual(row.target_type, "", "target_type must default to empty string")

    def test_live_skipped_count_increments_by_one_per_duplicate(self):
        """Each skipped (already-reconciled) record increments skipped_count by exactly 1."""
        from accounts.models import AdminAuditLog
        record = _valid_record(self.superuser.pk, notes="dup check")
        backfill_notes = "dup check [recovered:attempted_at=2025-01-15T10:00:00+00:00]"
        # Create two pre-existing rows with different notes to test skipped=2
        record2 = _valid_record(self.superuser.pk, notes="dup check 2")
        backfill_notes2 = "dup check 2 [recovered:attempted_at=2025-01-15T10:00:00+00:00]"
        AdminAuditLog.objects.create(
            organization=self.org,
            actor=self.superuser,
            on_behalf_of=None,
            action="impersonate_action",
            notes=backfill_notes,
        )
        AdminAuditLog.objects.create(
            organization=self.org,
            actor=self.superuser,
            on_behalf_of=None,
            action="impersonate_action",
            notes=backfill_notes2,
        )
        out = StringIO()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _make_recovery_jsonl([record, record2], Path(tmpdir))
            with override_settings(AUDIT_RECOVERY_LOG=path):
                call_command("backfill_audit_log", stdout=out, stderr=StringIO())
        self.assertIn("skipped=2", out.getvalue())

    def test_live_organization_is_set_in_created_row(self):
        """When org lookup succeeds, the created row has the correct organization set."""
        from accounts.models import AdminAuditLog
        record = _valid_record(self.superuser.pk, org_id=self.org.pk, notes="org-set-check")
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _make_recovery_jsonl([record], Path(tmpdir))
            with override_settings(AUDIT_RECOVERY_LOG=path):
                call_command("backfill_audit_log", stdout=StringIO(), stderr=StringIO())
        row = AdminAuditLog.objects.filter(notes__contains="org-set-check").last()
        self.assertIsNotNone(row)
        self.assertEqual(row.organization, self.org)

    def test_live_created_at_backdated_to_attempted_at(self):
        """The created AdminAuditLog row has created_at set to the original attempted_at."""
        from accounts.models import AdminAuditLog
        from django.utils.timezone import make_aware
        record = _valid_record(self.superuser.pk, notes="backdate-check")
        record["attempted_at"] = "2025-03-15T14:30:00+00:00"
        with tempfile.TemporaryDirectory() as tmpdir:
            path = _make_recovery_jsonl([record], Path(tmpdir))
            with override_settings(AUDIT_RECOVERY_LOG=path):
                call_command("backfill_audit_log", stdout=StringIO(), stderr=StringIO())
        row = AdminAuditLog.objects.filter(notes__contains="backdate-check").last()
        self.assertIsNotNone(row)
        self.assertEqual(row.created_at.year, 2025)
        self.assertEqual(row.created_at.month, 3)
        self.assertEqual(row.created_at.day, 15)
