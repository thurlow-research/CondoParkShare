"""
audit_log.py — canonical per-entry audit-record helper (SPEC-888 / TD-888 P1).

The audit trail is moving from a single append-only file
(audit/oversight-log.jsonl) to a directory of write-once, one-event-per-file,
month-sharded records under audit/log/<YYYY>/<MM>/. Because git merges files
independently, two branches that each write an audit event under distinct
filenames can never textually conflict — eliminating the conflict at its source
rather than syncing around it (#861).

This module is the single source of truth for the record grammar, the canonical
byte serialization, and the two load-bearing seams:

  - the WRITER (`write_event`)  : event dict -> record path, written write-once.
  - the READ-SHIM (`read_stream`): glob + sort + concatenate the directory back
                                   into the legacy chronological JSONL stream.

Both the Bash facade (scripts/oversight/lib/audit_log.sh) and the migration
script (P4) delegate serialization here, so Bash and Python writers are
byte-identical by construction (SPEC R2 / T5).

Record grammar (SPEC §6):
    audit/log/<YYYY>/<MM>/<ts>-<event>-<hash>.json
      <ts>   colon-free UTC "YYYY-MM-DDTHHMMSSZ" (fixed-width, lexical=chrono)
      <event> the record's `event` field, slugified (legibility only)
      <hash>  first 12 hex of sha256(canonical_bytes(event)) (collision-proof)

Canonical bytes (load-bearing for cross-writer reproducibility, SPEC §6):
    UTF-8 of json.dumps(event, sort_keys=True, separators=(",", ":"),
                        ensure_ascii=False)  followed by a single "\n".

Refinement over TD §2.3 (within SPEC §6): the filename <ts> is derived from the
event's OWN `timestamp` field when present, falling back to a single now() read.
This guarantees the filename timestamp and the record's timestamp can never
drift, and lets the P4 migration preserve each historical event's real time with
no special threading. An explicit `ts=` argument still overrides both.

CLI (used by the Bash facade and the P4 migration):
    python3 -m scripts.oversight.lib.audit_log write [root] [--ts TS]   # stdin=event JSON -> prints relpath
    python3 -m scripts.oversight.lib.audit_log read  [root]             # prints the ordered JSONL stream
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

# Colon-free UTC grammar: "YYYY-MM-DDTHHMMSSZ" (SPEC §6 — NTFS-valid, glob-safe).
_TS_FMT = "%Y-%m-%dT%H%M%SZ"
_TS_GRAMMAR = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{6}Z$")
_SLUG_RE = re.compile(r"[^a-z0-9]+")

# Directory that holds the per-entry records, relative to a repo root.
_LOG_SUBDIR = ("audit", "log")


def canonical_bytes(event: dict) -> bytes:
    """The exact bytes written to disk AND hashed for the filename.

    The only place serialization is defined, so the filename hash always
    matches the file content (self-consistency, idempotency).
    """
    text = json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return (text + "\n").encode("utf-8")


def normalize_ts(raw: str) -> str:
    """Return the colon-free grammar timestamp for an ISO-8601 UTC instant.

    Accepts the grammar form unchanged, or any ISO-8601 timestamp (with colons,
    fractional seconds, or an explicit offset) and renders it to UTC
    "YYYY-MM-DDTHHMMSSZ". Raises ValueError on an unparseable value (the caller
    — live writer or migration — is expected to fail loud, never fabricate).
    """
    s = raw.strip()
    if _TS_GRAMMAR.match(s):
        return s
    # Tolerate a trailing "Z" on Python versions whose fromisoformat predates it.
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime(_TS_FMT)


def _now_ts() -> str:
    return datetime.now(timezone.utc).strftime(_TS_FMT)


def _slug(event: dict) -> str:
    raw = str(event.get("event", "event")).lower()
    return _SLUG_RE.sub("-", raw).strip("-") or "event"


def _resolve_ts(event: dict, ts: Optional[str]) -> str:
    """Filename timestamp precedence: explicit ts > event['timestamp'] > now()."""
    if ts is not None:
        return normalize_ts(ts)
    raw = event.get("timestamp")
    if isinstance(raw, str) and raw.strip():
        return normalize_ts(raw)
    return _now_ts()


def record_relpath(event: dict, ts: str) -> str:
    """Record path relative to audit/log: "<YYYY>/<MM>/<ts>-<slug>-<hash12>.json".

    <ts> must already be the colon-free grammar form; <YYYY>/<MM> are sliced from
    it (NOT re-read from a clock) so the shard path and filename are derived from
    the one authoritative instant.
    """
    ts = normalize_ts(ts)
    yyyy, mm = ts[0:4], ts[5:7]
    digest = hashlib.sha256(canonical_bytes(event)).hexdigest()[:12]
    return f"{yyyy}/{mm}/{ts}-{_slug(event)}-{digest}.json"


def _log_dir(root: str) -> Path:
    return Path(root).joinpath(*_LOG_SUBDIR)


def write_event(event: dict, *, root: str = ".", ts: Optional[str] = None) -> str:
    """Write `event` as a write-once record; return its path relative to audit/log.

    Idempotent: re-writing a path whose bytes already match is a no-op. A byte
    mismatch at an existing path is a hard error (a 12-hex SHA-256 collision on
    non-identical content — not expected at our volumes; fail loud).
    """
    ts = _resolve_ts(event, ts)
    relpath = record_relpath(event, ts)
    abspath = _log_dir(root) / relpath
    data = canonical_bytes(event)

    if abspath.exists():
        existing = abspath.read_bytes()
        if existing != data:
            raise RuntimeError(
                f"audit record hash collision on non-identical content: {abspath}"
            )
        return relpath  # idempotent no-op

    abspath.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive create makes the write-once contract hold under concurrency; an
    # identical-content race is the idempotent no-op above.
    try:
        with abspath.open("xb") as fh:
            fh.write(data)
    except FileExistsError:
        if abspath.read_bytes() != data:
            raise RuntimeError(
                f"audit record hash collision on non-identical content: {abspath}"
            )
    return relpath


def read_stream(root: str = ".") -> Iterator[bytes]:
    """Yield each record's bytes in chronological (= lexical path) order.

    Reconstructs the legacy JSONL event stream from the directory. Total: an
    absent or empty audit/log directory yields nothing and is not an error,
    preserving the "missing log -> empty output, exit 0" contract readers rely
    on today (e.g. step_range.sh).
    """
    log_dir = _log_dir(root)
    if not log_dir.is_dir():
        return
    for path in sorted(log_dir.rglob("*.json"), key=lambda p: p.as_posix()):
        if path.is_file():
            yield path.read_bytes()


# --------------------------------------------------------------------------- #
# CLI — the seam the Bash facade and the P4 migration delegate to.
# --------------------------------------------------------------------------- #

def _usage(stream=sys.stderr) -> None:
    print(
        "Usage:\n"
        "  python3 -m scripts.oversight.lib.audit_log write [root] [--ts TS]"
        "   # stdin=event JSON -> prints record relpath\n"
        "  python3 -m scripts.oversight.lib.audit_log read  [root]"
        "             # prints the ordered JSONL stream",
        file=stream,
    )


def _main(argv: list[str]) -> int:
    if not argv:
        _usage()
        return 2

    cmd = argv[0]
    rest = argv[1:]
    root = "."
    ts: Optional[str] = None
    i = 0
    while i < len(rest):
        arg = rest[i]
        if arg == "--ts":
            if i + 1 >= len(rest):
                _usage()
                return 2
            ts = rest[i + 1]
            i += 2
            continue
        root = arg
        i += 1

    if cmd == "write":
        event = json.loads(sys.stdin.read())
        if not isinstance(event, dict):
            print("write: event JSON must be an object", file=sys.stderr)
            return 2
        print(write_event(event, root=root, ts=ts))
        return 0

    if cmd == "read":
        out = sys.stdout.buffer
        for record in read_stream(root):
            out.write(record)
        out.flush()
        return 0

    _usage()
    return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
