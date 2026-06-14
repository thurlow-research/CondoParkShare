#!/usr/bin/env python3
"""
ip_check.py — IP/provenance validator for the Human Oversight System.

Implements the growth path from DECISIONS.md D19:
  Level 1 (ACTIVE): Dependency license gate — flag changed dependency manifests
    whose added packages carry copyleft/unknown licenses.
  Level 2 (ACTIVE): Prompt clean-room verification — read captured prompt artifacts
    and flag phrases that reference external codebases, specific implementations,
    or copy instructions that require attribution tracking.
  Level 3 (STUB): Regurgitation lens — detect code that looks lifted from training
    data. Returns a transparent stub verdict with an explicit disclaimer.

The prompt-as-source artifact (DECISIONS.md D8) is the clean-room counter-evidence:
code regenerable from a spec that never said "copy library X" is documentary provenance.

Output: standard HOS findings schema (same as other validators).

Usage:
  python3 ip_check.py file.py [file2.py ...]
  python3 ip_check.py --prompts-dir prompts/ --changed-files "a.py b.py"
"""

from __future__ import annotations

import json
import pathlib as _hos_pl
import re
import subprocess

# self-bootstrap: ensure this file's dir (with schema.py) is importable
# regardless of caller cwd/PYTHONPATH (run_validators, run_panel, direct).
import sys
import sys as _hos_sys
import urllib.request
from pathlib import Path

_hos_sys.path.insert(0, str(_hos_pl.Path(__file__).resolve().parent))
from schema import WEIGHTS, make_finding, make_result  # noqa: E402

# ── License classification ────────────────────────────────────────────────────

COPYLEFT = {
    "GPL",
    "GPL-2.0",
    "GPL-3.0",
    "AGPL",
    "AGPL-3.0",
    "LGPL",
    "LGPL-2.1",
    "LGPL-3.0",
    "EUPL",
    "OSL",
    "MPL",
    "CDDL",
    "EPL",
    "SSPL",
}
PERMISSIVE = {
    "MIT",
    "Apache",
    "Apache-2.0",
    "BSD",
    "ISC",
    "Artistic",
    "WTFPL",
    "Unlicense",
    "CC0",
    "PSF",
    "Python-2.0",
    "Zlib",
}
UNKNOWN_MARKERS = {"", "UNKNOWN", "Proprietary", "Commercial", "See", "Other", None}

_COPYLEFT_SCORE = 0.90
_UNKNOWN_SCORE = 0.55
_PERMISSIVE_SCORE = 0.10  # still log — needs attribution notices


def classify_license(lic: str | None) -> tuple[str, float]:
    if not lic or lic.strip() in UNKNOWN_MARKERS:
        return "unknown", _UNKNOWN_SCORE
    u = lic.upper()
    for c in COPYLEFT:
        if c.upper() in u:
            return "copyleft", _COPYLEFT_SCORE
    for p in PERMISSIVE:
        if p.upper() in u:
            return "permissive", _PERMISSIVE_SCORE
    return "unknown", _UNKNOWN_SCORE


def _scancode_license(file_path: str) -> str | None:
    """
    Use ScanCode Toolkit (https://github.com/nexB/scancode-toolkit) if installed.
    More thorough than API lookup — does full red-line text comparison against a
    database of license texts. Install: pip install scancode-toolkit
    On Faberix (Ubuntu): pip install scancode-toolkit  (no sudo needed, user install)
    """
    try:
        result = subprocess.run(
            ["scancode", "--license", "--json-pp", "-", file_path],
            capture_output=True,
            text=True,
            timeout=30,
        )
        data = json.loads(result.stdout)
        files = data.get("files", [])
        for f in files:
            licenses = f.get("licenses", [])
            if licenses:
                return licenses[0].get("spdx_license_key") or licenses[0].get("key")
    except (FileNotFoundError, json.JSONDecodeError, subprocess.TimeoutExpired):
        pass
    return None


def _pypi_license(package: str) -> str | None:
    """Fallback: query PyPI API for license metadata."""
    try:
        url = f"https://pypi.org/pypi/{package}/json"
        with urllib.request.urlopen(url, timeout=5) as r:
            data = json.loads(r.read())
        return data.get("info", {}).get("license") or None
    except Exception:
        return None


def _npm_license(package: str) -> str | None:
    try:
        url = f"https://registry.npmjs.org/{package}/latest"
        with urllib.request.urlopen(url, timeout=5) as r:
            data = json.loads(r.read())
        return data.get("license") or None
    except Exception:
        return None


def _check_scancode_available() -> bool:
    try:
        return subprocess.run(["scancode", "--version"], capture_output=True).returncode == 0
    except FileNotFoundError:
        return False


_SCANCODE_AVAILABLE = _check_scancode_available()


def check_dependency_licenses(file_paths: list[str]) -> list[dict]:
    """
    Scan changed dependency manifests for newly added packages and classify
    their licenses. Returns a list of findings for packages with concerning licenses.
    """
    findings = []

    for fp in file_paths:
        p = Path(fp)
        if not p.exists():
            continue

        added_packages: list[tuple[str, str]] = []  # (name, version_spec)

        # Python — requirements.txt / requirements-*.txt / pyproject.toml
        if re.search(r"requirements.*\.txt$", p.name, re.I):
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                # Parse: package[extras]>=version ; marker
                m = re.match(r"^([A-Za-z0-9_.-]+)", line)
                if m:
                    added_packages.append((m.group(1), line))

        elif p.name in ("pyproject.toml",):
            try:
                import tomllib  # Python 3.11+

                data = tomllib.loads(p.read_text())
                deps = data.get("project", {}).get("dependencies", [])
                for d in deps:
                    m = re.match(r"^([A-Za-z0-9_.-]+)", d)
                    if m:
                        added_packages.append((m.group(1), d))
            except ImportError:
                pass  # tomllib not available on < 3.11 — skip

        # Node — package.json
        elif p.name == "package.json":
            try:
                pkg = json.loads(p.read_text(encoding="utf-8"))
                all_deps = {}
                all_deps.update(pkg.get("dependencies", {}))
                all_deps.update(pkg.get("devDependencies", {}))
                for name in all_deps:
                    added_packages.append((name, all_deps[name]))
            except json.JSONDecodeError:
                pass

        if not added_packages:
            continue

        for pkg_name, spec in added_packages:
            # Determine ecosystem from file
            is_python = p.suffix in (".txt", ".toml") or "requirements" in p.name
            is_node = p.name == "package.json"

            # Prefer ScanCode (full text comparison) over API lookup
            lic = (_scancode_license(fp) if _SCANCODE_AVAILABLE else None) or (
                _pypi_license(pkg_name)
                if is_python
                else _npm_license(pkg_name) if is_node else None
            )

            category, score = classify_license(lic)

            if category in ("copyleft", "unknown"):
                sev = "high" if category == "copyleft" else "medium"
                findings.append(
                    {
                        "file": fp,
                        "package": pkg_name,
                        "spec": spec,
                        "license": lic or "UNKNOWN",
                        "category": category,
                        "score": score,
                        "severity": sev,
                        "message": (
                            f"{pkg_name} ({lic or 'UNKNOWN license'}) — "
                            + (
                                "copyleft license may require source disclosure"
                                if category == "copyleft"
                                else "license unknown — legal review required"
                            )
                        ),
                    }
                )
            elif category == "permissive":
                # Log permissive licenses — attribution notices required
                findings.append(
                    {
                        "file": fp,
                        "package": pkg_name,
                        "spec": spec,
                        "license": lic,
                        "category": "permissive",
                        "score": score,
                        "severity": "low",
                        "message": (
                            f"{pkg_name} ({lic}) — permissive; "
                            "verify attribution notice is preserved"
                        ),
                    }
                )

    return findings


# ── Prompt clean-room verification ───────────────────────────────────────────

# Phrases that indicate the prompt referenced external code, requiring attribution review
_ATTRIBUTION_TRIGGERS = [
    r"\bcopy(?:ing)? from\b",
    r"\bbased on\b",
    r"\bport(?:ed)? from\b",
    r"\bfork(?:ed)? from\b",
    r"\btaken from\b",
    r"\blifted from\b",
    r"\bborrowed from\b",
    r"\buse the.*implementation\b",
    r"\bsame as\b.*\b(library|code|pattern|function)\b",
    r"\bfollowing.*example\b",
    r"\bverbatim\b",
    r"\bexact(ly)?\b.*copy",
]

# Phrases that suggest spec-only sourcing (clean-room positive signals)
_CLEANROOM_SIGNALS = [
    r"\baccording to\s+(spec|rfc|standard|requirement|design)\b",
    r"\bper spec\b",
    r"\bfrom scratch\b",
    r"\bspec[–-]compliant\b",
    r"\bspec section\b",
    r"\bimplements?\s+\w+\s+as defined\b",
]


def check_prompt_cleanroom(prompts_dir: str, changed_files: list[str]) -> list[dict]:
    """
    Read captured prompt artifacts for the changed files and check for:
    - Attribution triggers: phrases indicating external code was referenced
    - Absence of clean-room signals: purely spec-derived prompts
    Returns findings for prompts that warrant IP review.
    """
    findings = []
    prompts_root = Path(prompts_dir)
    if not prompts_root.exists():
        return findings

    for src_file in changed_files:
        # Derive prompt artifact path by mirroring src/ → prompts/
        p = Path(src_file)
        stem = p.with_suffix(".md")
        # Try both direct mirror and with 'prompts/' prefix
        candidates = [
            prompts_root / stem,
            prompts_root / stem.name,
            prompts_root / p.parent / stem.name,
        ]
        # Also check git trailers for explicit Prompt-Artifact: path
        try:
            trailer_out = subprocess.run(
                ["git", "log", "-10", "--format=%B", "--", src_file],
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout
            for line in trailer_out.splitlines():
                if line.startswith("Prompt-Artifact:"):
                    artifact_path = line.split(":", 1)[1].strip()
                    candidates.insert(0, Path(artifact_path))
        except Exception:
            pass

        prompt_text = None
        prompt_path = None
        for c in candidates:
            if c.exists():
                prompt_text = c.read_text(encoding="utf-8", errors="replace")
                prompt_path = str(c)
                break

        if prompt_text is None:
            continue  # no prompt artifact found for this file — flagged by evaluator compliance

        # Check for attribution triggers
        for pattern in _ATTRIBUTION_TRIGGERS:
            m = re.search(pattern, prompt_text, re.I)
            if m:
                findings.append(
                    {
                        "file": src_file,
                        "prompt_artifact": prompt_path,
                        "trigger": m.group(0),
                        "severity": "medium",
                        "category": "attribution-trigger",
                        "message": (
                            f"Prompt references external code ('{m.group(0)}'). "
                            f"Verify attribution obligations are met and license is compatible."
                        ),
                    }
                )
                break  # one finding per file

        # Clean-room signal count (positive evidence — included in output for auditors)
        cleanroom_count = sum(1 for pat in _CLEANROOM_SIGNALS if re.search(pat, prompt_text, re.I))
        if cleanroom_count >= 2 and not any(f["file"] == src_file for f in findings):
            findings.append(
                {
                    "file": src_file,
                    "prompt_artifact": prompt_path,
                    "cleanroom_signals": cleanroom_count,
                    "severity": "info",
                    "category": "cleanroom-positive",
                    "message": (
                        f"Prompt shows {cleanroom_count} clean-room signal(s) "
                        f"(spec-only sourcing). Good provenance."
                    ),
                }
            )

    return findings


# ── Regurgitation lens stub ───────────────────────────────────────────────────


def check_regurgitation_stub(file_paths: list[str]) -> dict:
    """
    Level 3: snippet-matching against FOSS code index using locality-sensitive hashing.

    ARCHITECTURE NOTE: ai-gen-code-search (AboutCode) is NOT a standalone pip package.
    It requires deploying three backend services:
      - PurlDB: package metadata database
      - MatchCode: LSH matching service
      - ScanCode.io: frontend/API layer

    There is no downloadable pre-built FOSS index; the index is built against the
    deployed stack. A hosted evaluation system exists — contact hello@aboutcode.org
    for research access. This is the planned integration path for this framework.

    Reference: https://github.com/aboutcode-org/ai-gen-code-search (v1.0.0, May 2025)
    Companion: ScanCode Toolkit (already wired into Level 1 above) provides the license
    text database that MatchCode builds alongside.

    Integration plan once API access is obtained:
      1. Receive API endpoint + credentials from AboutCode
      2. Replace stub below with REST calls to the MatchCode evaluation API
      3. Remove this stub and set integration_active = True

    IP_REGURGITATION_ENABLED env var is reserved for when the API integration lands.
    Setting it to 1 currently has no effect — it does not activate any real analysis.
    """
    enabled = __import__("os").environ.get("IP_REGURGITATION_ENABLED", "0") == "1"

    return {
        "stub": True,
        "integration_active": False,
        "planned_tool": (
            "ai-gen-code-search (AboutCode) REST API — LSH snippet matching against FOSS index"
        ),
        "status": "awaiting API access from AboutCode (hello@aboutcode.org)",
        "files_checked": len(file_paths),
        "ip_regurgitation_enabled_env": enabled,
        "message": (
            "Regurgitation lens (Level 3) is NOT YET ACTIVE — requires AboutCode API access. "
            "A clean result here is NOT evidence against code regurgitation. "
            "ScanCode is "
            + (
                "available (Level 1 active)"
                if _SCANCODE_AVAILABLE
                else "not installed — Level 1 using API fallback"
            )
            + "."
        ),
    }


# ── Main ──────────────────────────────────────────────────────────────────────


def analyse_files(
    file_paths: list[str],
    prompts_dir: str = "prompts",
) -> dict:
    dep_findings = check_dependency_licenses(file_paths)
    prompt_findings = check_prompt_cleanroom(prompts_dir, file_paths)
    regurgitation = check_regurgitation_stub(file_paths)

    all_findings = dep_findings + [f for f in prompt_findings if f["severity"] != "info"]
    cleanroom_positive = [f for f in prompt_findings if f["severity"] == "info"]

    # Score: worst-case license issue dominates
    dep_score = max((f["score"] for f in dep_findings), default=0.0)
    attr_score = (
        0.6 if any(f["category"] == "attribution-trigger" for f in prompt_findings) else 0.0
    )
    score = max(dep_score, attr_score)

    high_count = sum(1 for f in all_findings if f["severity"] == "high")
    medium_count = sum(1 for f in all_findings if f["severity"] == "medium")

    evidence = [
        make_finding(
            f.get("file", "?"),
            0,
            f["message"],
            f["severity"] if f["severity"] in ("high", "medium", "low") else "low",
        )
        for f in all_findings[:10]
    ]

    checklist = []
    for f in all_findings:
        if f["category"] == "copyleft":
            checklist.append(
                f"⚖️ {f['package']} ({f['license']}): copyleft — "
                "confirm source-disclosure obligations are met, "
                "or replace with compatible alternative"
            )
        elif f["category"] == "unknown":
            checklist.append(
                f"⚖️ {f['package']} (license unknown): legal review required before shipping"
            )
        elif f["category"] == "attribution-trigger":
            checklist.append(
                f"⚖️ {f['file']}: prompt references external code — "
                "verify attribution notice is preserved in source and docs"
            )

    if cleanroom_positive:
        checklist.append(
            f"✓ {len(cleanroom_positive)} file(s) have clean-room prompt provenance "
            "(spec-only sourcing detected)"
        )

    return make_result(
        dimension="ip_check",
        score=score,
        raw_value={
            "dependency_findings": dep_findings,
            "prompt_findings": prompt_findings,
            "regurgitation": regurgitation,
            "high_count": high_count,
            "medium_count": medium_count,
            "cleanroom_positive_count": len(cleanroom_positive),
            "note": (
                "A clean result is NOT an IP clearance. "
                "Regurgitation lens is a stub (Level 3 not yet implemented). "
                "Route any ELEVATED findings to legal/counsel — not legal advice."
            ),
        },
        weight=WEIGHTS.get("ip_check", 0.10),
        evidence=evidence,
        checklist_items=checklist,
    )


def main() -> None:
    args = sys.argv[1:]
    prompts_dir = "prompts"
    if "--prompts-dir" in args:
        idx = args.index("--prompts-dir")
        prompts_dir = args[idx + 1]
        args = args[:idx] + args[idx + 2 :]

    files = [f for f in args if Path(f).exists()]
    if not files:
        print(
            json.dumps(
                make_result(
                    "ip_check",
                    0.0,
                    {"error": "no input files"},
                    weight=WEIGHTS.get("ip_check", 0.10),
                    error="no input files",
                ),
                indent=2,
            )
        )
        return

    print(json.dumps(analyse_files(files, prompts_dir=prompts_dir), indent=2))


if __name__ == "__main__":
    main()
