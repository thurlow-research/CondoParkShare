"""
accounts management command: audit_healthcheck

Performs a synthetic write+read-back probe of the audit DB path (via AuditProbe),
computes backlog counts from the recovery JSONL (via backfill_audit_log --dry-run
logic), and emits results in two ways:

  1. Appends a one-line JSON status record to AUDIT_LIVENESS_STATUS (on the
     audit_logs volume) — the fallback path readable even if the metrics
     endpoint is down.

  2. Writes parkshare_audit.prom into NODE_EXPORTER_TEXTFILE_DIR atomically
     (temp file in same dir + os.rename) so node_exporter's textfile collector
     picks up the gauges on its next scrape without ever reading a partial file.

Signals emitted (per AUDIT-MONITORING-SPEC.md §1):

  S5 — liveness:
    parkshare_audit_liveness_ok          gauge 0/1
    parkshare_audit_liveness_age_seconds gauge (seconds since last success)

  S3a/b/c — backlog:
    parkshare_audit_backlog_records          gauge (unreconciled count)
    parkshare_audit_backlog_oldest_seconds   gauge (age of oldest unreconciled record)
    parkshare_audit_backlog_rejected         gauge
    parkshare_audit_backlog_malformed        gauge

  freshness:
    parkshare_audit_healthcheck_unixtime gauge (Unix epoch of this write)

The command exits with code 1 if the liveness probe failed so callers (cron,
systemd) can detect and alert on a broken DB path.  It never raises an
unhandled exception — every code path is wrapped in exception handling so
a DB outage produces an explicit ok=false signal rather than a missing data
point.

Scheduling: host cron on opus/faberix running
  docker compose exec -T web python manage.py audit_healthcheck
every AUDIT_LIVENESS_INTERVAL_SECONDS (default 60 s).  Do not run inside a
gunicorn worker — run as a separate process via the cron/exec path.

AuditProbe TTL: rows older than PROBE_RETAIN_COUNT most-recent are deleted
after each probe run so the table stays bounded.
"""

import json
import logging
import os
import platform
import sys
import tempfile
import time
from datetime import timezone as dt_timezone
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils.timezone import now as django_now

logger = logging.getLogger(__name__)

# Keep at most this many probe rows in AuditProbe.  The delete runs after each
# successful probe so the table never accumulates unboundedly.
_PROBE_RETAIN_COUNT = 10

# Common labels attached to every metric line.
_ENV = getattr(settings, "ENVIRONMENT", os.environ.get("DJANGO_ENV", "unknown"))
_HOST = platform.node()


def _common_labels() -> str:
    return f'env="{_ENV}",host="{_HOST}",service="parkshare-web"'


def _prom_gauge(name: str, help_text: str, value: float) -> str:
    labels = _common_labels()
    return (
        f"# HELP {name} {help_text}\n"
        f"# TYPE {name} gauge\n"
        f'{name}{{{labels}}} {value}\n'
    )


def _build_prom_text(
    liveness_ok: int,
    liveness_age_seconds: float,
    backlog_records: int,
    backlog_oldest_seconds: float,
    backlog_rejected: int,
    backlog_malformed: int,
    healthcheck_unixtime: float,
) -> str:
    lines = []
    lines.append(
        _prom_gauge(
            "parkshare_audit_liveness_ok",
            "1 if the last audit DB write+read-back probe succeeded, 0 otherwise.",
            liveness_ok,
        )
    )
    lines.append(
        _prom_gauge(
            "parkshare_audit_liveness_age_seconds",
            "Seconds since the last successful audit liveness probe.",
            liveness_age_seconds,
        )
    )
    lines.append(
        _prom_gauge(
            "parkshare_audit_backlog_records",
            "Number of unreconciled audit recovery records in the JSONL sink.",
            backlog_records,
        )
    )
    lines.append(
        _prom_gauge(
            "parkshare_audit_backlog_oldest_seconds",
            "Age in seconds of the oldest unreconciled audit recovery record.",
            backlog_oldest_seconds,
        )
    )
    lines.append(
        _prom_gauge(
            "parkshare_audit_backlog_rejected",
            "Recovery records rejected by anti-forgery checks (security signal).",
            backlog_rejected,
        )
    )
    lines.append(
        _prom_gauge(
            "parkshare_audit_backlog_malformed",
            "Malformed or unparseable lines in the audit recovery JSONL.",
            backlog_malformed,
        )
    )
    lines.append(
        _prom_gauge(
            "parkshare_audit_healthcheck_unixtime",
            "Unix timestamp of the last audit_healthcheck write (freshness guard).",
            healthcheck_unixtime,
        )
    )
    return "".join(lines)


def _write_prom_atomically(
    textfile_dir: str, content: str
) -> None:
    """
    Write *content* to parkshare_audit.prom inside *textfile_dir* using a
    temp-file + os.rename so node_exporter never reads a partial file.

    Raises OSError if the directory does not exist or is not writable.
    """
    target = Path(textfile_dir) / "parkshare_audit.prom"
    # tempfile in the same directory guarantees rename(2) is atomic
    # (same filesystem).  delete=False because we rename it ourselves.
    fd, tmp_path = tempfile.mkstemp(dir=textfile_dir, prefix=".parkshare_audit_", suffix=".prom.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.rename(tmp_path, target)
    except Exception:
        # Clean up the temp file if rename failed, then re-raise so callers
        # can log and fall through to a degraded (status-file-only) path.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _append_liveness_status(status_path: str, record: dict) -> None:
    """Append one JSON line to the liveness status JSONL file."""
    Path(status_path).parent.mkdir(parents=True, exist_ok=True)
    with open(status_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


def _read_last_success_ts(status_path: str) -> float | None:
    """
    Scan AUDIT_LIVENESS_STATUS from the end to find the most recent ok=true
    entry and return its Unix timestamp, or None if none exists.

    Used to compute liveness_age_seconds even when the current probe failed.
    """
    try:
        with open(status_path, encoding="utf-8") as fh:
            lines = fh.readlines()
    except FileNotFoundError:
        return None

    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
            if entry.get("ok"):
                return float(entry["ts"])
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue
    return None


class Command(BaseCommand):
    help = (
        "Synthetic audit DB liveness probe + backlog gauge writer "
        "(spec AUDIT-MONITORING-SPEC.md §6.3)."
    )

    def handle(self, *args, **options):
        liveness_ok = self._run_probe()
        backlog_records, backlog_oldest_seconds, backlog_rejected, backlog_malformed = (
            self._compute_backlog()
        )

        now_unix = time.time()
        status_path = settings.AUDIT_LIVENESS_STATUS
        textfile_dir = settings.NODE_EXPORTER_TEXTFILE_DIR

        if liveness_ok:
            liveness_age_seconds = 0.0
            status_record = {
                "ts": now_unix,
                "ok": True,
                "write_ms": getattr(self, "_probe_write_ms", None),
            }
        else:
            last_ok_ts = _read_last_success_ts(status_path)
            liveness_age_seconds = (
                now_unix - last_ok_ts if last_ok_ts is not None else float("inf")
            )
            # inf would produce invalid Prometheus text; cap at a large sentinel
            if liveness_age_seconds == float("inf"):
                liveness_age_seconds = 86400.0 * 365  # 1 year — "never succeeded"
            status_record = {
                "ts": now_unix,
                "ok": False,
                "error": getattr(self, "_probe_error_class", "unknown"),
            }

        try:
            _append_liveness_status(status_path, status_record)
        except OSError as exc:
            # Do not let a write failure to the status file prevent the .prom
            # write — log and continue.
            logger.error(
                "audit_healthcheck: failed to write liveness status file %s: %s",
                status_path,
                exc,
            )

        prom_text = _build_prom_text(
            liveness_ok=int(liveness_ok),
            liveness_age_seconds=liveness_age_seconds,
            backlog_records=backlog_records,
            backlog_oldest_seconds=backlog_oldest_seconds,
            backlog_rejected=backlog_rejected,
            backlog_malformed=backlog_malformed,
            healthcheck_unixtime=now_unix,
        )

        try:
            _write_prom_atomically(textfile_dir, prom_text)
        except OSError as exc:
            # The .prom write failed (directory missing, read-only, etc.).
            # Log and continue — the liveness status file is the fallback.
            logger.error(
                "audit_healthcheck: failed to write .prom file to %s: %s",
                textfile_dir,
                exc,
            )

        if not liveness_ok:
            sys.exit(1)

    def _run_probe(self) -> bool:
        """
        Write an AuditProbe row and read it back to verify the full DB round-trip.
        Deletes stale probe rows older than _PROBE_RETAIN_COUNT most-recent.

        Sets self._probe_write_ms on success, self._probe_error_class on failure.
        Returns True on success, False on any exception.
        """
        from accounts.models import AuditProbe

        try:
            t0 = time.monotonic()
            probe = AuditProbe.objects.create()
            # Read back the row just written — confirms durability + visibility.
            AuditProbe.objects.get(pk=probe.pk)
            write_ms = (time.monotonic() - t0) * 1000
            self._probe_write_ms = round(write_ms, 2)

            # Prune old probe rows so the table stays bounded.
            keep_ids = list(
                AuditProbe.objects.order_by("-created_at")
                .values_list("pk", flat=True)[:_PROBE_RETAIN_COUNT]
            )
            AuditProbe.objects.exclude(pk__in=keep_ids).delete()

            return True
        except Exception as exc:
            self._probe_error_class = type(exc).__name__
            logger.error(
                "audit_healthcheck: liveness probe failed: %s: %s",
                type(exc).__name__,
                exc,
            )
            return False

    def _compute_backlog(self) -> tuple[int, float, int, int]:
        """
        Run the dry-run backlog computation using compute_backlog() from the
        backfill command.  Returns (records, oldest_seconds, rejected, malformed).

        Falls back to all-zeros on FileNotFoundError (no recovery log yet) and
        logs a warning on any other exception so a broken JSONL never crashes
        the probe.
        """
        from accounts.management.commands.backfill_audit_log import compute_backlog

        recovery_path = settings.AUDIT_RECOVERY_LOG
        now_utc = django_now().astimezone(dt_timezone.utc)

        try:
            counts = compute_backlog(recovery_path)
        except FileNotFoundError:
            # No recovery file yet — clean slate, backlog is zero.
            return 0, 0.0, 0, 0
        except Exception as exc:
            logger.error(
                "audit_healthcheck: backlog computation failed: %s: %s",
                type(exc).__name__,
                exc,
            )
            return 0, 0.0, 0, 0

        if counts.oldest_unreconciled_at is not None:
            oldest_dt = counts.oldest_unreconciled_at
            if oldest_dt.tzinfo is None:
                from django.utils.timezone import make_aware

                oldest_dt = make_aware(oldest_dt)
            oldest_seconds = max(
                0.0, (now_utc - oldest_dt.astimezone(dt_timezone.utc)).total_seconds()
            )
        else:
            oldest_seconds = 0.0

        return (
            counts.created_would_be,
            oldest_seconds,
            counts.rejected,
            counts.malformed,
        )
