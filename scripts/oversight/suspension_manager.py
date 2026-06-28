#!/usr/bin/env python3
"""suspension_manager.py — manage contract/gate-suspension.md.

Implements the low-friction / high-audit levers for the unified suspension
mechanism (HOS#62), governed by the RATCHET PRINCIPLE
(research/findings/ratchet-principle.md):

    The system may auto-TIGHTEN (re-enable a gate). It may never auto-LOOSEN
    (suspend one). This module contains NO code path that writes a SUSPENDED
    line — it only ever removes them. Only a human may suspend.

Levers:
  --census       Print active suspensions; warn on past `review-by`; emit a
                 `suspension-census` audit event (the health metric).
  --check        For each AUTO-CHECKABLE suspended gate, run its gate script
                 and record pass/fail in the pass-history.
  --auto-remove  Remove suspensions that have passed N consecutive checks and
                 are eligible (pure script gate, not [pinned], auto-remove on).
                 Appends to the Re-enable log and emits `gate-auto-reenabled`.
  (default)      Run census + check + auto-remove in sequence.

Config (env or scripts/framework/config.sh):
  SUSPENSION_AUTO_REMOVE        true|false  (default true)
  SUSPENSION_AUTO_REMOVE_RUNS   int         (default 3 consecutive passes)

Stdlib only — no third-party imports, so no venv dependency.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SUSPENSION_FILE = "contract/gate-suspension.md"
HISTORY_FILE = ".claudetmp/oversight/suspension-history.jsonl"


def _load_audit_log():
    """Load the canonical per-entry audit-record helper by file path.

    suspension_manager runs as a plain script (sys.path[0] = its own dir), so a
    package import is not reliable here; load the sibling lib/ module directly.
    """
    path = Path(__file__).resolve().parent / "lib" / "audit_log.py"
    spec = importlib.util.spec_from_file_location("hos_audit_log", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_AUDIT_LOG = _load_audit_log()

# Gates that can be auto-removed: PURE script gates whose passing genuinely
# means the gate is satisfied. `security` is deliberately excluded — it has a
# reviewer-role counterpart that a passing security_scan cannot satisfy, so it
# is only ever nudged, never auto-removed.
AUTO_CHECKABLE_GATES: dict[str, str] = {
    "lint": "scripts/oversight/gates/lint_check.sh",
    "secrets": "scripts/oversight/gates/secret_scan.sh",
    "types": "scripts/oversight/gates/type_check.sh",
    "template-refs": "scripts/oversight/gates/template_refs_check.sh",
    "portability": "scripts/oversight/gates/portability_check.sh",
    "django": "scripts/oversight/gates/django_check.sh",
}

_SUSPENDED_RE = re.compile(
    r"^SUSPENDED:\s*(?P<gate>[a-z0-9-]+)"
    r"(?P<flags>(?:\s+\[pinned\]|\s+review-by:\s*\d{4}-\d{2}-\d{2})*)\s*$"
)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class Suspension:
    def __init__(self, gate: str, pinned: bool, review_by: str | None, raw_line: str):
        self.gate = gate
        self.pinned = pinned
        self.review_by = review_by
        self.raw_line = raw_line


def parse_suspensions(text: str) -> list[Suspension]:
    """Parse SUSPENDED: lines from the 'Currently suspended' section.

    Only lines outside HTML comments are considered (the template's examples
    live inside <!-- --> and must be ignored).
    """
    out: list[Suspension] = []
    in_comment = False
    for line in text.splitlines():
        stripped = line.strip()
        # Track comment blocks crudely but correctly for our single-line needs.
        if "<!--" in stripped and "-->" not in stripped:
            in_comment = True
            continue
        if "-->" in stripped:
            in_comment = False
            continue
        if in_comment:
            continue
        m = _SUSPENDED_RE.match(stripped)
        if not m:
            continue
        flags = m.group("flags") or ""
        pinned = "[pinned]" in flags
        rb = re.search(r"review-by:\s*(\d{4}-\d{2}-\d{2})", flags)
        out.append(Suspension(m.group("gate"), pinned, rb.group(1) if rb else None, line))
    return out


def acknowledged_security(text: str) -> bool:
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("#"):
            continue
        if s.replace(" ", "").lower().startswith("security-suspension-acknowledged:yes"):
            return True
    return False


def _iter_noncomment_lines(text: str):
    """Yield stripped lines that are outside HTML comment blocks and not '#' comments.

    Mirrors parse_suspensions()'s crude-but-correct comment tracking — the template
    keeps its examples inside <!-- --> and as '#'-prefixed lines, and those must be
    ignored (a human enables a field by un-commenting it).
    """
    in_comment = False
    for line in text.splitlines():
        stripped = line.strip()
        if "<!--" in stripped and "-->" not in stripped:
            in_comment = True
            continue
        if "-->" in stripped:
            in_comment = False
            continue
        if in_comment:
            continue
        if stripped.startswith("#"):
            continue
        yield stripped


_TRUTHY = ("1", "true", "yes", "on")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_per_step_scope(text: str) -> tuple[bool, list[str]]:
    """Parse `per_step_scope:` and `steps:` from a gate-suspension file (SPEC-83 R1).

    Returns (per_step_scope, steps). HTML-commented and '#'-commented lines are
    ignored (template examples). Both block-list (`  - step-3`) and inline
    (`steps: [step-3, step-4]`) forms are accepted. Step IDs are returned verbatim
    (byte-exact, case-sensitive — the caller matches with `==`).
    """
    per_step = False
    steps: list[str] = []
    in_block_list = False
    for stripped in _iter_noncomment_lines(text):
        low = stripped.lower()
        if low.replace(" ", "").startswith("per_step_scope:"):
            val = stripped.split(":", 1)[1].strip().lower()
            per_step = val in _TRUTHY
            in_block_list = False
            continue
        if low.startswith("steps:"):
            rest = stripped.split(":", 1)[1].strip()
            if rest.startswith("[") and rest.endswith("]"):
                # inline form: steps: [step-3, step-4]
                inner = rest[1:-1].strip()
                if inner:
                    steps.extend(
                        s.strip().strip("'\"") for s in inner.split(",") if s.strip()
                    )
                in_block_list = False
            elif rest:
                # `steps: step-3` single inline value (no brackets)
                steps.append(rest.strip("'\""))
                in_block_list = False
            else:
                in_block_list = True  # block-list entries follow on subsequent lines
            continue
        if in_block_list:
            if stripped.startswith("- "):
                steps.append(stripped[2:].strip().strip("'\""))
                continue
            in_block_list = False  # non-list line ends the block
    return per_step, steps


def validate_per_step_scope(text: str, step_id: str) -> dict:
    """Classify the per-step scope fields against a step ID (SPEC-83 R2.2a / R1.6).

    Returns:
      {per_step_scope: bool, malformed: bool, covers_step: bool, steps: [str]}

    - per_step_scope False (blanket/absent) → not malformed, not covering.
    - per_step_scope True + empty/absent steps → malformed True (R1.6 distinct FAIL).
    - per_step_scope True + non-empty steps → covers_step = (step_id in steps), exact match.

    Does NOT check the tier or security-suspension-acknowledged — that stays with the
    evaluator (R2.3). Pure classifier of the scope fields.
    """
    per_step, steps = parse_per_step_scope(text)
    malformed = per_step and not steps
    covers = per_step and not malformed and step_id in steps
    return {
        "per_step_scope": per_step,
        "malformed": malformed,
        "covers_step": covers,
        "steps": steps,
    }


def check_grandfathered_until(text: str, today: str | None = None) -> dict:
    """Resolve the `grandfathered_until:` transition date (SPEC-83 R3).

    Reuses the `review-by:` date idiom: lexicographic YYYY-MM-DD comparison against
    today. `today` defaults to _today() (injectable for tests).

    Returns {present: bool, date: str|None, status: str} where status is one of:
      "absent"    — no grandfathered_until line → no grandfathering (FAIL applies)
      "future"    — date strictly after today  → WARN + CONDITIONAL_PROCEED
      "expired"   — date today or in the past  → FAIL (period over)
      "malformed" — value not YYYY-MM-DD       → FAIL (fail-closed)
    """
    today = today or _today()
    for stripped in _iter_noncomment_lines(text):
        if stripped.lower().replace(" ", "").startswith("grandfathered_until:"):
            val = stripped.split(":", 1)[1].strip()
            if not _DATE_RE.match(val):
                return {"present": True, "date": val or None, "status": "malformed"}
            # strictly-future = grandfathered; today or past = expired (period over)
            status = "future" if val > today else "expired"
            return {"present": True, "date": val, "status": status}
    return {"present": False, "date": None, "status": "absent"}


def _read_config_bool(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None:
        cfg = Path("scripts/framework/config.sh")
        if cfg.exists():
            m = re.search(rf"^{name}=[\"']?(\w+)[\"']?", cfg.read_text(), re.M)
            if m:
                val = m.group(1)
    if val is None:
        return default
    return str(val).strip().lower() in ("1", "true", "yes", "on")


def _read_config_int(name: str, default: int) -> int:
    val = os.environ.get(name)
    if val is None:
        cfg = Path("scripts/framework/config.sh")
        if cfg.exists():
            m = re.search(rf"^{name}=[\"']?(\d+)[\"']?", cfg.read_text(), re.M)
            if m:
                val = m.group(1)
    try:
        return int(val) if val is not None else default
    except (TypeError, ValueError):
        return default


def load_history() -> list[dict]:
    p = Path(HISTORY_FILE)
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def record_check(gate: str, passed: bool) -> None:
    p = Path(HISTORY_FILE)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as f:
        f.write(json.dumps({"gate": gate, "passed": passed, "timestamp": _now()}) + "\n")


def consecutive_passes(history: list[dict], gate: str) -> int:
    """Count trailing consecutive passes for `gate` (most recent first)."""
    n = 0
    for entry in reversed([h for h in history if h.get("gate") == gate]):
        if entry.get("passed"):
            n += 1
        else:
            break
    return n


def emit_audit(event: dict) -> None:
    """Write `event` as a write-once per-entry audit record (SPEC-888, #888 P2).

    The record lands under audit/log/<YYYY>/<MM>/ so two branches never touch the
    same file — the audit-log merge conflict is removed at its source rather than
    synced around. No-op when audit/ is absent (the original guard is preserved).
    """
    if Path("audit").is_dir():
        event = {**event, "timestamp": _now()}
        _AUDIT_LOG.write_event(event, root=".")


def run_gate(script: str) -> bool:
    """Run a gate script in --all mode; True if it passes (exit 0)."""
    if not Path(script).exists():
        return False
    try:
        r = subprocess.run(["bash", script, "--all"], capture_output=True, text=True, timeout=300)
        return r.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def cmd_census(suspensions: list[Suspension], quiet: bool = False) -> None:
    gates = [s.gate for s in suspensions]
    if not quiet:
        print(f"Active suspensions: {len(gates)}")
        for s in suspensions:
            extra = []
            if s.pinned:
                extra.append("pinned")
            if s.review_by:
                overdue = s.review_by < _today()
                extra.append(f"review-by {s.review_by}" + (" ⚠ OVERDUE" if overdue else ""))
            tag = f" ({', '.join(extra)})" if extra else ""
            print(f"  - {s.gate}{tag}")
    emit_audit(
        {"event": "suspension-census", "active_suspensions": len(gates), "suspended_gates": gates}
    )


def cmd_check(suspensions: list[Suspension]) -> None:
    for s in suspensions:
        script = AUTO_CHECKABLE_GATES.get(s.gate)
        if not script:
            continue  # not auto-checkable (reviewer role or dual gate) — skip
        passed = run_gate(script)
        record_check(s.gate, passed)
        print(f"  check {s.gate}: {'pass' if passed else 'fail'}")


def cmd_auto_remove(text: str, suspensions: list[Suspension]) -> str:
    """Return the (possibly modified) suspension-file text with eligible
    suspensions removed. NEVER adds a SUSPENDED line (ratchet)."""
    if not _read_config_bool("SUSPENSION_AUTO_REMOVE", True):
        print("auto-remove disabled (SUSPENSION_AUTO_REMOVE=false) — nudging only")
    auto = _read_config_bool("SUSPENSION_AUTO_REMOVE", True)
    needed = _read_config_int("SUSPENSION_AUTO_REMOVE_RUNS", 3)
    history = load_history()

    lines = text.splitlines(keepends=True)
    removed: list[str] = []
    for s in suspensions:
        eligible = s.gate in AUTO_CHECKABLE_GATES and not s.pinned
        passes = consecutive_passes(history, s.gate)
        if eligible and passes >= needed:
            if auto:
                lines = [ln for ln in lines if ln.rstrip("\n") != s.raw_line.rstrip("\n")]
                removed.append(s.gate)
                emit_audit(
                    {
                        "event": "gate-auto-reenabled",
                        "gate": s.gate,
                        "consecutive_passes": passes,
                    }
                )
                print(f"  auto-removed: {s.gate} (passed {passes} consecutive checks)")
            else:
                print(f"  NUDGE: {s.gate} now passes ({passes}×) — you may remove it")
        elif s.gate in AUTO_CHECKABLE_GATES and passes >= needed and s.pinned:
            print(f"  NUDGE: {s.gate} now passes but is [pinned] — remove manually")

    new_text = "".join(lines)
    if removed:
        new_text = _append_reenable_log(new_text, removed)
    return new_text


def _append_reenable_log(text: str, gates: list[str]) -> str:
    """Append rows to the Re-enable log table."""
    rows = "".join(
        f"| {g} | {_today()} | auto-removed after consecutive passes | suspension-manager |\n"
        for g in gates
    )
    if "## Re-enable log" in text:
        # Insert after the table header if present, else after the heading.
        return text.rstrip() + "\n" + rows
    return text.rstrip() + "\n\n## Re-enable log\n\n" + rows


def _read_reason_category(suspension_file: str) -> str:
    """Read reason_category from gate-suspension.md; return 'unspecified' if absent."""
    try:
        text = Path(suspension_file).read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            low = line.strip().lower()
            if low.startswith("reason_category:"):
                val = line.split(":", 1)[1].strip().strip("'\"").upper()
                if val and not val.startswith("["):  # skip template placeholders
                    return val
    except (OSError, UnicodeDecodeError):
        pass
    return "unspecified"


def cmd_emit_audit(
    gate: str,
    authorized_by: str | None,
    step: str | None = None,
    suspension_file: str = SUSPENSION_FILE,
) -> int:
    """Append a `gate-suspended` event to audit/oversight-log.jsonl.

    Canonical home for the audit JSON that check_suspension.sh used to build by
    hand with printf (HOS#337). Now includes the three OVERSIGHT-CONTRACT §6a
    fields deferred from #337: step, suspension_file, reason_category (#397).
    No-op when audit/ is absent (the guard lives in emit_audit). Best-effort:
    always exits 0.
    """
    emit_audit(
        {
            "event": "gate-suspended",
            "gate": gate,
            "authorized_by": authorized_by or "unknown",
            "step": step or "unknown",
            "suspension_file": suspension_file,
            "reason_category": _read_reason_category(suspension_file),
        }
    )
    return 0


def cmd_is_suspended(gate: str) -> int:
    """Exit 0 if gate is currently suspended, exit 1 otherwise.

    Used by run_gates.sh to populate the 'suspended' field in gate-results.json
    without sourcing the bash check_suspension.sh helper.
    """
    susp_path = Path(SUSPENSION_FILE)
    if not susp_path.exists():
        return 1
    suspensions = parse_suspensions(susp_path.read_text())
    return 0 if any(s.gate == gate for s in suspensions) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--census", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--auto-remove", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument(
        "--is-suspended",
        metavar="GATE",
        help="Exit 0 if GATE is currently suspended, exit 1 otherwise.",
    )
    parser.add_argument(
        "--emit-audit",
        action="store_true",
        help="Append a gate-suspended audit event (use with --gate / --authorized-by).",
    )
    parser.add_argument("--gate", metavar="GATE", help="Gate name for --emit-audit.")
    parser.add_argument(
        "--authorized-by",
        metavar="VALUE",
        help="Authorizer string for --emit-audit (default: unknown).",
    )
    parser.add_argument(
        "--step",
        metavar="STEP_ID",
        default=None,
        help="Build step the suspension applies to, for --emit-audit (#397).",
    )
    parser.add_argument(
        "--suspension-file",
        metavar="PATH",
        default=SUSPENSION_FILE,
        help=f"Path to the suspension record file (default: {SUSPENSION_FILE}).",
    )
    args = parser.parse_args()

    # Point query — does not need the suspension file to exist to parse.
    if args.is_suspended:
        return cmd_is_suspended(args.is_suspended)

    # Audit emission — does not need the suspension file to exist; the caller
    # already knows the gate is suspended by the time it emits.
    if args.emit_audit:
        if not args.gate:
            print("--emit-audit requires --gate", file=sys.stderr)
            return 2
        return cmd_emit_audit(
            args.gate,
            args.authorized_by,
            step=args.step,
            suspension_file=args.suspension_file,
        )

    susp_path = Path(SUSPENSION_FILE)
    if not susp_path.exists():
        if not args.quiet:
            print("No contract/gate-suspension.md — nothing to manage.")
        return 0

    text = susp_path.read_text()
    suspensions = parse_suspensions(text)

    run_all = not (args.census or args.check or args.auto_remove)

    if args.census or run_all:
        cmd_census(suspensions, quiet=args.quiet)
    if args.check or run_all:
        cmd_check(suspensions)
    if args.auto_remove or run_all:
        new_text = cmd_auto_remove(text, suspensions)
        if new_text != text:
            susp_path.write_text(new_text)

    return 0


if __name__ == "__main__":
    sys.exit(main())
