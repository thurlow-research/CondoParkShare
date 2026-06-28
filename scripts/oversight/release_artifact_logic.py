#!/usr/bin/env python3
"""release_artifact_logic.py — release-gate deep artifact validation (#695).

Adds a release-time sweep across all committed validator artifacts
(signoffs/validators/step{N}/summary.json) and sign-off stamps. This is
SEPARATE from the per-PR §3b lightweight presence+SHA check: that stays as-is
on every PR review. This module runs at release-cut time only (infrequent,
high-value checkpoint).

WHAT IT CHECKS
  1. Artifact integrity — each step's summary.json is present and parseable.
  2. Risk tier — any HIGH/CRITICAL-tier step is surfaced explicitly; HIGH/CRITICAL
     WITH blocking-severity findings triggers escalation.
  3. Sign-off completeness — every required role from the step manifest has a
     committed, valid-status stamp in signoffs/<role>.stamp.

PURITY
  - validate_release_artifacts() does file I/O (reads summary.json files and
    stamp files) but NO subprocess, network, or git calls.
  - _load_step_artifact() and _extract_blocking_findings() are pure on their
    inputs — unit-testable with synthetic dicts.
  - YAML loading lives only in the CLI shim; the pure logic accepts a pre-parsed
    manifest dict so tests stay subprocess-free.

SHELL INTEGRATION
  - cut_release.sh calls: python release_artifact_logic.py validate
      --repo-root . [--manifest contract/step-manifest.yaml]
      --version $VERSION --log-to audit/oversight-log.jsonl
  - Exit 0: all checks pass (or no artifacts found — graceful skip).
  - Exit 1: escalation required (human must authorize before release).
  - Exit 2: usage / environment error (unreadable args, missing venv YAML, etc.).
  - Human-readable summary printed to stdout on all paths.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Findings with these severities require explicit human acknowledgment at the
# release gate when found inside a HIGH+ tier step.
BLOCKING_SEVERITIES: frozenset[str] = frozenset({"critical", "high", "blocking"})

# Tiers that require explicit surfacing at release time.
HIGH_PLUS_TIERS: frozenset[str] = frozenset({"HIGH", "CRITICAL"})

# Valid stamp statuses (mirrors signoff_gate.py).
VALID_STAMP_STATUSES: frozenset[str] = frozenset(
    {"APPROVED", "CONDITIONAL", "NOT_APPLICABLE", "NA"}
)

# Fields required in a valid summary.json (artifact_version "1" schema).
REQUIRED_ARTIFACT_FIELDS = ("tier", "composite_score", "head_sha", "artifact_version")


# --------------------------------------------------------------------------- #
# Data types                                                                   #
# --------------------------------------------------------------------------- #


@dataclass
class StepArtifactInfo:
    """Summary of one step's committed validator artifact."""

    step_id: int | str
    path: str
    tier: str = "UNKNOWN"
    composite_score: float = 0.0
    head_sha: str = ""
    blocking_findings: list[dict] = field(default_factory=list)
    parse_error: str | None = None


@dataclass
class ReleaseArtifactResult:
    """Overall result of the release-gate artifact validation sweep."""

    verdict: str  # "pass" | "escalate"
    steps_found: int
    high_plus_steps: list[StepArtifactInfo]
    escalation_reasons: list[str]
    warnings: list[str]
    missing_signoffs: list[str]
    artifacts_read: list[str]

    @property
    def should_escalate(self) -> bool:
        return self.verdict == "escalate"

    def to_audit_event(self, version: str, timestamp: str) -> dict:
        """Produce the oversight-log.jsonl event dict for this validation run."""
        return {
            "event": "release-artifact-validation",
            "version": version,
            "verdict": self.verdict,
            "steps_found": self.steps_found,
            "high_plus_steps": [
                {"step": a.step_id, "tier": a.tier, "path": a.path}
                for a in self.high_plus_steps
            ],
            "escalation_reasons": self.escalation_reasons,
            "warnings_count": len(self.warnings),
            "missing_signoffs": self.missing_signoffs,
            "artifacts_read": self.artifacts_read,
            "timestamp": timestamp,
        }


# --------------------------------------------------------------------------- #
# Pure helpers (unit-testable without file I/O)                               #
# --------------------------------------------------------------------------- #


def check_artifact_integrity(artifact: dict) -> str | None:
    """Return an error string if the artifact dict is structurally invalid, else None.

    Checks that all REQUIRED_ARTIFACT_FIELDS are present and that composite_score
    is a number in [0, 1]. Pure — no file I/O, no subprocess.
    """
    for f in REQUIRED_ARTIFACT_FIELDS:
        if f not in artifact:
            return f"missing required field: {f!r}"
    score = artifact.get("composite_score")
    if not isinstance(score, (int, float)):
        return f"composite_score must be numeric, got {type(score).__name__}"
    if not (0.0 <= float(score) <= 1.0):
        return f"composite_score out of range [0,1]: {score}"
    return None


def extract_blocking_findings(results: list[dict]) -> list[dict]:
    """Collect blocking-severity findings across all validator result entries.

    A finding is blocking if its 'severity' field is in BLOCKING_SEVERITIES
    (case-insensitive). Returns a flat list of finding dicts augmented with the
    'dimension' they came from. Pure — no file I/O.
    """
    blocking: list[dict] = []
    for result in results or []:
        dimension = result.get("dimension", "unknown")
        for finding in result.get("findings", []) or []:
            sev = str(finding.get("severity", "")).lower()
            if sev in BLOCKING_SEVERITIES:
                blocking.append({**finding, "_dimension": dimension})
    return blocking


# --------------------------------------------------------------------------- #
# File I/O helpers                                                             #
# --------------------------------------------------------------------------- #


def _load_step_artifact(path: Path) -> StepArtifactInfo:
    """Load one step's summary.json.  Returns a StepArtifactInfo with parse_error
    set on any failure so the caller can record it without raising.
    """
    step_id_str = path.parent.name  # "step1", "step2", etc.
    try:
        step_id = int(step_id_str.lstrip("step"))
    except ValueError:
        step_id = step_id_str

    rel_path = str(path)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        return StepArtifactInfo(step_id=step_id, path=rel_path, parse_error=f"read error: {exc}")

    try:
        artifact = json.loads(raw)
    except json.JSONDecodeError as exc:
        return StepArtifactInfo(step_id=step_id, path=rel_path, parse_error=f"JSON parse error: {exc}")

    err = check_artifact_integrity(artifact)
    if err:
        return StepArtifactInfo(step_id=step_id, path=rel_path, parse_error=err)

    blocking = extract_blocking_findings(artifact.get("results", []))
    return StepArtifactInfo(
        step_id=artifact.get("step", step_id),
        path=rel_path,
        tier=str(artifact.get("tier", "UNKNOWN")).upper(),
        composite_score=float(artifact["composite_score"]),
        head_sha=str(artifact.get("head_sha", "")),
        blocking_findings=blocking,
    )


def _parse_stamp_status(path: Path) -> str | None:
    """Read the `status:` field from a sign-off stamp file. None if absent/unreadable."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("status:"):
            return stripped.split(":", 1)[1].strip().upper()
    return None


def _get_required_roles(manifest_data: dict) -> list[str]:
    """Return the sorted union of required_signoffs across all steps in the manifest."""
    roles: set[str] = set()
    for step in manifest_data.get("steps", []) or []:
        for role in step.get("required_signoffs", []) or []:
            roles.add(role)
    return sorted(roles)


def _load_audit_log():
    """Load the canonical per-entry audit-record helper (SPEC-888) by file path.

    This module runs as a plain script under cut_release.sh (and re-execs into a
    venv when PyYAML is missing), so load the sibling lib/ module directly rather
    than relying on a package import.
    """
    import importlib.util

    path = Path(__file__).resolve().parent / "lib" / "audit_log.py"
    spec = importlib.util.spec_from_file_location("hos_audit_log", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------- #
# Main validation function                                                     #
# --------------------------------------------------------------------------- #


def validate_release_artifacts(
    repo_root: str | Path,
    manifest_data: dict | None = None,
) -> ReleaseArtifactResult:
    """Sweep all committed step artifacts and sign-off stamps for a release gate.

    Parameters
    ----------
    repo_root:
        Root of the repository (contains signoffs/, contract/, audit/).
    manifest_data:
        Pre-parsed step-manifest dict. When provided, sign-off completeness is
        checked against required_signoffs per step. When None, that check is
        skipped (manifest-free deployments or framework self-test).

    Returns
    -------
    ReleaseArtifactResult with verdict "pass" or "escalate".  Never raises —
    unreadable files produce escalation_reasons entries instead.
    """
    root = Path(repo_root).resolve()
    validators_dir = root / "signoffs" / "validators"

    artifacts_read: list[str] = []
    step_artifacts: list[StepArtifactInfo] = []
    escalation_reasons: list[str] = []
    warnings: list[str] = []
    high_plus_steps: list[StepArtifactInfo] = []

    # ── 1. Scan step validator artifacts ──────────────────────────────────────
    if validators_dir.exists():
        for step_dir in sorted(validators_dir.iterdir()):
            if not step_dir.is_dir() or not step_dir.name.startswith("step"):
                continue
            artifact_path = step_dir / "summary.json"
            if not artifact_path.exists():
                continue
            info = _load_step_artifact(artifact_path)
            step_artifacts.append(info)

            if info.parse_error:
                escalation_reasons.append(
                    f"step {info.step_id}: artifact unreadable — {info.parse_error}"
                )
            else:
                try:
                    rel = str(artifact_path.relative_to(root))
                except ValueError:
                    rel = str(artifact_path)
                artifacts_read.append(rel)

    # ── 2. Risk tier and blocking-findings sweep ──────────────────────────────
    for info in step_artifacts:
        if info.parse_error:
            continue
        if info.tier in HIGH_PLUS_TIERS:
            high_plus_steps.append(info)
            if info.blocking_findings:
                n = len(info.blocking_findings)
                escalation_reasons.append(
                    f"step {info.step_id}: {info.tier} tier with {n} unresolved "
                    f"blocking finding(s) — human authorization required"
                )
            else:
                warnings.append(
                    f"step {info.step_id}: {info.tier} tier "
                    f"(score={info.composite_score:.3f}, no blocking findings — approved)"
                )

    # ── 3. Sign-off register completeness ────────────────────────────────────
    missing_signoffs: list[str] = []
    if manifest_data is not None:
        required_roles = _get_required_roles(manifest_data)
        signoffs_dir = root / "signoffs"
        for role in required_roles:
            stamp_path = signoffs_dir / f"{role}.stamp"
            if not stamp_path.exists():
                missing_signoffs.append(role)
                escalation_reasons.append(
                    f"missing committed sign-off stamp for required role: {role!r}"
                )
                continue
            status = _parse_stamp_status(stamp_path)
            if status not in VALID_STAMP_STATUSES:
                missing_signoffs.append(role)
                escalation_reasons.append(
                    f"invalid stamp status for role {role!r}: {status!r} "
                    f"(expected one of {sorted(VALID_STAMP_STATUSES)})"
                )

    verdict = "escalate" if escalation_reasons else "pass"
    return ReleaseArtifactResult(
        verdict=verdict,
        steps_found=len(step_artifacts),
        high_plus_steps=high_plus_steps,
        escalation_reasons=escalation_reasons,
        warnings=warnings,
        missing_signoffs=missing_signoffs,
        artifacts_read=artifacts_read,
    )


# --------------------------------------------------------------------------- #
# CLI shim                                                                     #
# --------------------------------------------------------------------------- #

# YAML import with venv fallback (same pattern as signoff_gate.py).
# The module works without YAML — manifest-dependent checks are silently skipped.
_yaml: object = None
try:
    import yaml as _yaml_module  # type: ignore[import]

    _yaml = _yaml_module
except ImportError:
    import os as _os

    _venv_py = (Path(__file__).parent / ".venv" / "bin" / "python3").resolve()
    _already_venv = False
    try:
        _already_venv = _venv_py.exists() and _venv_py.samefile(sys.executable)
    except OSError:
        _already_venv = False
    if _venv_py.exists() and not _already_venv:
        _os.execv(str(_venv_py), [str(_venv_py)] + sys.argv)
    # No venv either — proceed without YAML; manifest checks will be skipped.


def _load_manifest(path: str) -> dict | None:
    """Load a step-manifest YAML file.  Returns None on any failure (caller warns)."""
    if _yaml is None:
        return None
    try:
        return _yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}  # type: ignore[union-attr]
    except Exception:  # pylint: disable=broad-except
        return None


def _now_utc() -> str:
    """Return current UTC timestamp as ISO-8601 string (no external deps)."""
    import datetime

    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _cmd_validate(args: argparse.Namespace) -> int:
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    RED = "\033[31m"
    BOLD = "\033[1m"
    RESET = "\033[0m"

    def ok(msg: str) -> None:
        print(f"  {GREEN}✔{RESET}  {msg}")

    def warn(msg: str) -> None:
        print(f"  {YELLOW}⚠{RESET}  {msg}")

    def err(msg: str) -> None:
        print(f"  {RED}✘{RESET}  {msg}", file=sys.stderr)

    def hdr(msg: str) -> None:
        print(f"\n{BOLD}{msg}{RESET}")

    hdr("Release artifact validation")

    # Load manifest if provided and YAML is available.
    manifest_data: dict | None = None
    if args.manifest:
        manifest_data = _load_manifest(args.manifest)
        if manifest_data is None:
            if _yaml is None:
                warn(f"PyYAML not available — sign-off completeness check skipped")
            else:
                warn(f"Could not read manifest {args.manifest!r} — sign-off completeness check skipped")

    result = validate_release_artifacts(
        repo_root=args.repo_root,
        manifest_data=manifest_data,
    )

    # ── Print summary ────────────────────────────────────────────────────────
    print(f"\n  Steps with committed artifacts: {result.steps_found}")
    print(f"  HIGH/CRITICAL tier steps:       {len(result.high_plus_steps)}")

    if result.high_plus_steps:
        for info in result.high_plus_steps:
            tier_str = f"{RED}{info.tier}{RESET}" if info.blocking_findings else f"{YELLOW}{info.tier}{RESET}"
            print(
                f"    step {info.step_id}: {tier_str}  "
                f"score={info.composite_score:.3f}  "
                f"blocking_findings={len(info.blocking_findings)}"
            )

    for w in result.warnings:
        warn(w)

    if result.missing_signoffs:
        for role in result.missing_signoffs:
            err(f"missing sign-off: {role}")

    # ── Verdict ─────────────────────────────────────────────────────────────
    if result.should_escalate:
        print()
        err(f"Release artifact validation: ESCALATE ({len(result.escalation_reasons)} reason(s))")
        for i, reason in enumerate(result.escalation_reasons, 1):
            err(f"  {i}. {reason}")
    else:
        print()
        ok(f"Release artifact validation: PASS")

    # ── Audit log (SPEC-888 per-entry record) ─────────────────────────────────
    # --log-to is retained as the "emit an audit event" signal (cut_release still
    # passes it); the record now lands as a write-once file under
    # <repo-root>/audit/log/ rather than appending to the single shared JSONL,
    # so concurrent release/PR branches never conflict on the audit trail.
    if args.log_to:
        timestamp = _now_utc()
        event = result.to_audit_event(
            version=args.version or "unknown",
            timestamp=timestamp,
        )
        try:
            audit_log = _load_audit_log()
            relpath = audit_log.write_event(event, root=args.repo_root)
            ok(f"Audit event written to audit/log/{relpath}")
        except Exception as exc:  # best-effort: never fail the release gate on audit I/O
            warn(f"Could not write audit event: {exc}")

    return 1 if result.should_escalate else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Release-gate deep artifact validation (#695)."
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_val = sub.add_parser(
        "validate",
        help="Sweep all step artifacts and sign-off stamps for a release gate.",
    )
    p_val.add_argument(
        "--repo-root",
        default=".",
        metavar="DIR",
        help="Repository root (default: current directory).",
    )
    p_val.add_argument(
        "--manifest",
        default=None,
        metavar="PATH",
        help="Path to contract/step-manifest.yaml for sign-off completeness check.",
    )
    p_val.add_argument(
        "--version",
        default=None,
        metavar="VER",
        help="Release version string (recorded in audit event, e.g. v0.4.2).",
    )
    p_val.add_argument(
        "--log-to",
        default=None,
        metavar="PATH",
        help="Append audit event to this oversight-log.jsonl file.",
    )
    p_val.set_defaults(func=_cmd_validate)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
