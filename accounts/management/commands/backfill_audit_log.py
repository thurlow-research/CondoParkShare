"""
accounts management command: backfill_audit_log

Reads the audit-recovery JSONL file and idempotently creates missing
AdminAuditLog rows.

Each line in the file must be a JSON object with the fields emitted by
ImpersonationMiddleware's recovery logger:
  organization_id, actor_id, on_behalf_of_id, action,
  target_type, target_id, notes, attempted_at

Dedupe key: (actor_id, on_behalf_of_id, action, notes). The backfill
command stores notes in the format:
  "<original notes> [recovered:attempted_at=<ISO-8601>]"
so that a row created by backfill carries a deterministic fingerprint that
exactly matches the recovery record on a second run.

Usage:
  python manage.py backfill_audit_log
  python manage.py backfill_audit_log --file /path/to/audit-recovery.jsonl

Concurrency note: multiple Gunicorn workers may append to the same JSONL
file concurrently. We rely on POSIX O_APPEND semantics — each JSON record
is a single small write (well under 4 KB) so kernel append is atomic and
lines from different workers will not interleave. Malformed lines are the
backstop for any edge-case partial write.
"""

import json
from datetime import datetime, timezone as dt_timezone

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils.timezone import make_aware

_ALLOWED_ACTIONS = {"impersonate_action"}


class Command(BaseCommand):
    help = "Backfill AdminAuditLog from the audit-recovery JSONL file."

    def add_arguments(self, parser):
        parser.add_argument(
            "--file",
            dest="file",
            default=None,
            help=(
                "Path to the recovery JSONL file. "
                "Defaults to the AUDIT_RECOVERY_LOG setting."
            ),
        )

    def handle(self, *args, **options):
        from accounts.models import AdminAuditLog, User
        from parking.models import Organization

        recovery_path = options["file"] or settings.AUDIT_RECOVERY_LOG

        created_count = 0
        skipped_count = 0
        rejected_count = 0
        malformed_count = 0

        try:
            with open(recovery_path, encoding="utf-8") as fh:
                lines = fh.readlines()
        except FileNotFoundError:
            self.stderr.write(
                self.style.ERROR(f"Recovery file not found: {recovery_path}")
            )
            return

        for line_no, raw_line in enumerate(lines, start=1):
            raw_line = raw_line.strip()
            if not raw_line:
                continue

            try:
                record = json.loads(raw_line)
                actor_id = int(record["actor_id"])
                on_behalf_of_id = record.get("on_behalf_of_id")
                if on_behalf_of_id is not None:
                    on_behalf_of_id = int(on_behalf_of_id)
                action = str(record["action"])
                notes = str(record.get("notes", ""))
                attempted_at_str = str(record["attempted_at"])
                organization_id = record.get("organization_id")
                if organization_id is not None:
                    organization_id = int(organization_id)
                target_type = str(record.get("target_type", ""))
                raw_target_id = record.get("target_id")
                target_id = int(raw_target_id) if raw_target_id is not None else None
            except (KeyError, ValueError, TypeError) as exc:
                self.stderr.write(
                    self.style.WARNING(
                        f"Line {line_no}: malformed record ({exc}): {raw_line[:120]}"
                    )
                )
                malformed_count += 1
                continue

            # --- Anti-forgery: allowlist action ---
            if action not in _ALLOWED_ACTIONS:
                self.stderr.write(
                    self.style.WARNING(
                        f"Line {line_no}: rejected — disallowed action {action!r}"
                    )
                )
                rejected_count += 1
                continue

            try:
                attempted_at = datetime.fromisoformat(attempted_at_str)
                if attempted_at.tzinfo is None:
                    attempted_at = make_aware(attempted_at)
                else:
                    attempted_at = attempted_at.astimezone(dt_timezone.utc)
            except ValueError as exc:
                self.stderr.write(
                    self.style.WARNING(
                        f"Line {line_no}: unparseable attempted_at {attempted_at_str!r} ({exc})"
                    )
                )
                malformed_count += 1
                continue

            try:
                actor = User.objects.get(pk=actor_id)
            except User.DoesNotExist:
                self.stderr.write(
                    self.style.WARNING(
                        f"Line {line_no}: actor pk={actor_id} not found — skipping"
                    )
                )
                malformed_count += 1
                continue

            # --- Anti-forgery: actor must be a superuser ---
            if not actor.is_superuser:
                self.stderr.write(
                    self.style.WARNING(
                        f"Line {line_no}: rejected — actor pk={actor_id} is not a superuser"
                    )
                )
                rejected_count += 1
                continue

            # --- Anti-forgery: no cross-tenant rows ---
            if organization_id is not None and actor.organization_id != organization_id:
                self.stderr.write(
                    self.style.WARNING(
                        f"Line {line_no}: rejected — actor org {actor.organization_id} "
                        f"does not match record org {organization_id}"
                    )
                )
                rejected_count += 1
                continue

            # Backfill notes: embed attempted_at so that a second run can
            # dedupe by matching the exact notes string rather than relying
            # on created_at (which is set at insert time, not incident time).
            backfill_notes = f"{notes} [recovered:attempted_at={attempted_at_str}]"

            # Dedupe: skip if a row with the backfill notes fingerprint already exists.
            already_exists = AdminAuditLog.objects.filter(
                actor_id=actor_id,
                on_behalf_of_id=on_behalf_of_id,
                action=action,
                notes=backfill_notes,
            ).exists()

            if already_exists:
                skipped_count += 1
                continue

            on_behalf_of = None
            if on_behalf_of_id is not None:
                try:
                    on_behalf_of = User.objects.get(pk=on_behalf_of_id)
                except User.DoesNotExist:
                    self.stderr.write(
                        self.style.WARNING(
                            f"Line {line_no}: on_behalf_of pk={on_behalf_of_id} not found "
                            "— creating entry without on_behalf_of"
                        )
                    )

            organization = None
            if organization_id is not None:
                try:
                    organization = Organization.objects.get(pk=organization_id)
                except Organization.DoesNotExist:
                    pass

            obj = AdminAuditLog.objects.create(
                organization=organization,
                actor=actor,
                on_behalf_of=on_behalf_of,
                action=action,
                target_type=target_type,
                target_id=target_id,
                notes=backfill_notes,
            )
            # created_at is auto_now_add so create() cannot set it; use update()
            # to backdate the row to the original incident time.
            AdminAuditLog.objects.filter(pk=obj.pk).update(created_at=attempted_at)
            created_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"backfill_audit_log: created={created_count} skipped={skipped_count} "
                f"rejected={rejected_count} malformed={malformed_count}"
            )
        )
