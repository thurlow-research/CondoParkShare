#!/usr/bin/env python3
"""
prompt_audit_risk.py — Prompt provenance and ambiguity risk validator.

Reads captured prompt artifacts for changed files and scores two dimensions:

1. AMBIGUITY — how unclear was the spec/prompt?
   High ambiguity → the coder made assumptions that nobody validated.
   Signals: question marks, hedging language, TBDs, conditional specs.
   Also draws from process signals: pm-agent escalation count, design iteration count.

2. FIDELITY SURFACE — structural signals that the prompt-code relationship
   may be weak. This is the structural complement to the semantic prompt-fidelity
   subagent (which does deeper reasoning). This script is deterministic.

   Signals:
   - Prompt artifact missing for MEDIUM+ change (cannot verify intent)
   - Prompt is very short relative to code complexity (spec was thin)
   - Code adds behavior not mentioned in prompt (unexplained additions count)

Designed to be called by the risk-assessor before reviewer chain starts.
The semantic prompt-fidelity subagent (higher effort) is called separately at MEDIUM+.

Usage:
  python3 prompt_audit_risk.py file.py [file2.py ...]
  python3 prompt_audit_risk.py --prompts-dir prompts/ file.py
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
from pathlib import Path

_hos_sys.path.insert(0, str(_hos_pl.Path(__file__).resolve().parent))
from schema import WEIGHTS, make_result, normalize  # noqa: E402

# ── Ambiguity signals ─────────────────────────────────────────────────────────

# Each phrase adds to the ambiguity score
_AMBIGUITY_PATTERNS = [
    (r"\?", 0.3, "question mark (unresolved question)"),
    (r"\bTBD\b|\bTODO\b|\bFIXME\b", 0.5, "TBD/TODO/FIXME in prompt"),
    (r"\bprobably\b|\bmaybe\b|\bperhaps\b|\bpossibly\b", 0.4, "hedging language"),
    (r"\bassume\b|\bpresumably\b|\bi think\b|\bnot sure\b", 0.5, "assumption language"),
    (r"\bunclear\b|\bambiguous\b|\bunderspecified\b", 0.6, "explicit ambiguity marker"),
    (r"\bif (?:possible|applicable|needed|relevant)\b", 0.3, "conditional requirement"),
    (r"\bor (?:maybe|perhaps|alternatively)\b", 0.3, "alternative requirement"),
    (r"\betc\.?\b|\band so on\b|\band similar\b", 0.4, "open-ended enumeration (etc.)"),
    (r"\bI don\'?t know\b|\bnot specified\b|\bneeds clarification\b", 0.7, "explicit uncertainty"),
]

# Signals that indicate a CLEAR, well-specified prompt (reduce ambiguity score)
_CLARITY_PATTERNS = [
    (r"\bexactly\b|\bprecisely\b|\bspecifically\b", -0.2, "precision marker"),
    (r"\bper spec\b|\bper rfc\b|\baccording to §", -0.3, "spec citation"),
    (r"\bmust\b|\bshall\b|\brequired\b", -0.1, "normative language"),
    (r"\btest case[s]?\b|\bunit test\b", -0.2, "tests specified"),
]


def score_prompt_ambiguity(prompt_text: str) -> tuple[float, list[str]]:
    """
    Score ambiguity of a prompt on 0 (crystal clear) → 1 (very ambiguous).
    Returns (score, list of signal descriptions found).
    """
    raw = 0.0
    signals = []

    for pattern, weight, label in _AMBIGUITY_PATTERNS:
        matches = len(re.findall(pattern, prompt_text, re.I))
        if matches:
            contribution = weight * min(matches, 5)  # cap at 5 instances
            raw += contribution
            signals.append(f"{label} ×{matches}")

    for pattern, weight, label in _CLARITY_PATTERNS:
        if re.search(pattern, prompt_text, re.I):
            raw += weight
            signals.append(f"{label} (reduces ambiguity)")

    # Normalize: raw > 3.0 → score 1.0; raw < 0.5 → score < 0.2
    score = normalize(max(0.0, raw), 0, 4.0)
    return score, signals


def score_fidelity_surface(
    prompt_text: str,
    code_text: str,
) -> tuple[float, list[str]]:
    """
    Structural fidelity signals — deterministic, not semantic.
    Identifies situations where the prompt-code relationship is structurally weak.
    """
    signals = []
    score = 0.0

    prompt_words = len(prompt_text.split())
    code_lines = code_text.count("\n")

    # Very short prompt relative to code complexity
    if prompt_words > 0 and code_lines > 0:
        ratio = code_lines / max(prompt_words, 1)
        if ratio > 5:
            score += 0.4
            signals.append(f"code/prompt ratio {ratio:.1f} — spec may be thin for this code volume")
        elif ratio > 2:
            score += 0.2
            signals.append(f"code/prompt ratio {ratio:.1f} — modest spec coverage")

    # Count function definitions in code that aren't mentioned in prompt
    func_names = re.findall(r"def (\w+)\s*\(", code_text)
    mentioned_in_prompt = [f for f in func_names if f.lower() in prompt_text.lower()]
    unmentioned = [
        f
        for f in func_names
        if f not in mentioned_in_prompt and not f.startswith(("_", "test_", "setUp", "tearDown"))
    ]
    if unmentioned:
        count = len(unmentioned)
        score += min(0.5, count * 0.1)
        signals.append(
            f"{count} function(s) in code not mentioned in prompt: "
            f"{', '.join(unmentioned[:5])}{'...' if count > 5 else ''}"
        )

    return min(1.0, score), signals


def get_prompt_artifact(file_path: str, prompts_dir: str) -> tuple[str | None, str | None]:
    """
    Find the prompt artifact for a source file.
    Priority: git trailer Prompt-Artifact: > prompts/ mirror > None
    Returns (prompt_text, prompt_path).
    """
    # Check git trailers first
    try:
        out = subprocess.run(
            ["git", "log", "-5", "--format=%B", "--", file_path],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout
        for line in out.splitlines():
            if line.startswith("Prompt-Artifact:"):
                artifact_path = line.split(":", 1)[1].strip()
                p = Path(artifact_path)
                if p.exists():
                    return p.read_text(encoding="utf-8", errors="replace"), str(p)
    except Exception:
        pass

    # Mirror src/ → prompts/
    p = Path(file_path)
    for candidate in [
        Path(prompts_dir) / p.with_suffix(".md"),
        Path(prompts_dir) / p.name.replace(p.suffix, ".md"),
    ]:
        if candidate.exists():
            return candidate.read_text(encoding="utf-8", errors="replace"), str(candidate)

    return None, None


def get_process_ambiguity(step: str | None = None) -> tuple[float, list[str]]:
    """
    Query process signals that indicate ambiguity: pm-agent escalations (spec-gap issues),
    architect iteration count from temp files, design iteration count.
    """
    signals = []
    score = 0.0

    # Count spec-gap GitHub issues
    try:
        result = subprocess.run(
            [
                "gh",
                "issue",
                "list",
                "--label",
                "spec-gap",
                "--state",
                "all",
                "--limit",
                "50",
                "--json",
                "number,title",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        issues = json.loads(result.stdout)
        count = len(issues)
        if count > 0:
            score += min(0.5, count * 0.1)
            signals.append(f"{count} spec-gap issue(s) on record — historical spec ambiguity")
    except Exception:
        pass

    # Check architect design iteration temp files for this step
    if step:
        import glob

        pattern = f".claudetmp/design/architect-{step}-*.md"
        files = sorted(glob.glob(pattern), reverse=True)[:1]
        for f in files:
            try:
                content = Path(f).read_text()
                m = re.search(r"^iteration:\s*(\d+)", content, re.M)
                if m and int(m.group(1)) >= 3:
                    iters = int(m.group(1))
                    score += min(0.4, (iters - 2) * 0.1)
                    signals.append(
                        f"architect design review took {iters} iterations — design was ambiguous"
                    )
            except Exception:
                pass

    return min(0.8, score), signals


def analyse_files(
    file_paths: list[str],
    prompts_dir: str = "prompts",
    step: str | None = None,
) -> dict:
    from schema import WEIGHTS, make_finding, make_result

    all_ambiguity_signals: list[str] = []
    all_fidelity_signals: list[str] = []
    evidence = []
    checklist: list[str] = []

    max_ambiguity = 0.0
    max_fidelity = 0.0
    missing_artifacts: list[str] = []

    for fp in file_paths:
        if not Path(fp).exists():
            continue
        try:
            code_text = Path(fp).read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        prompt_text, prompt_path = get_prompt_artifact(fp, prompts_dir)

        if prompt_text is None:
            missing_artifacts.append(fp)
            continue

        # Ambiguity scoring
        amb_score, amb_signals = score_prompt_ambiguity(prompt_text)
        max_ambiguity = max(max_ambiguity, amb_score)
        all_ambiguity_signals.extend(amb_signals)

        if amb_score > 0.5:
            evidence.append(
                make_finding(
                    fp,
                    0,
                    f"High prompt ambiguity (score={amb_score:.2f}): {'; '.join(amb_signals[:3])}",
                    "medium" if amb_score < 0.75 else "high",
                )
            )
            checklist.append(
                f"{Path(fp).name}: prompt was ambiguous — verify the coder's interpretation "
                f"matches what was intended. Key signals: {', '.join(amb_signals[:2])}"
            )

        # Fidelity surface scoring
        fid_score, fid_signals = score_fidelity_surface(prompt_text, code_text)
        max_fidelity = max(max_fidelity, fid_score)
        all_fidelity_signals.extend(fid_signals)

        if fid_signals:
            evidence.append(
                make_finding(
                    fp,
                    0,
                    f"Fidelity surface: {'; '.join(fid_signals[:2])}",
                    "medium" if fid_score > 0.4 else "low",
                )
            )

    # Process-level ambiguity signals — only meaningful when there are files to score.
    # With no input files there is no authoring context to assess, so process signals
    # would produce a non-zero score from unrelated repo history (e.g. existing
    # spec-gap issues), making the empty-input case a false positive.
    if file_paths:
        proc_score, proc_signals = get_process_ambiguity(step)
        all_ambiguity_signals.extend(proc_signals)
        max_ambiguity = max(
            max_ambiguity, proc_score * 0.5
        )  # process signals weight less than prompt signals
    else:
        proc_signals = []

    if missing_artifacts:
        evidence.append(
            make_finding(
                missing_artifacts[0],
                0,
                f"{len(missing_artifacts)} file(s) have no prompt artifact — "
                "cannot verify authoring intent",
                "medium",
            )
        )
        checklist.append(
            f"{len(missing_artifacts)} file(s) missing prompt artifacts: "
            f"{', '.join(Path(f).name for f in missing_artifacts[:3])}. "
            "Run: ./scripts/capture_prompt.sh for each MEDIUM+ change."
        )

    # Composite score
    composite = max(max_ambiguity, max_fidelity * 0.7)

    return make_result(
        dimension="prompt_ambiguity",
        score=composite,
        raw_value={
            "ambiguity_score": round(max_ambiguity, 3),
            "fidelity_surface_score": round(max_fidelity, 3),
            "ambiguity_signals": list(set(all_ambiguity_signals)),
            "fidelity_signals": list(set(all_fidelity_signals)),
            "process_signals": proc_signals,
            "missing_artifacts": missing_artifacts,
            "files_with_artifacts": len(file_paths) - len(missing_artifacts),
        },
        weight=WEIGHTS.get("prompt_ambiguity", 0.07),
        evidence=evidence,
        checklist_items=checklist,
    )


def main() -> None:
    args = sys.argv[1:]
    prompts_dir = "prompts"
    step = None

    if "--prompts-dir" in args:
        i = args.index("--prompts-dir")
        prompts_dir = args[i + 1]
        args = args[:i] + args[i + 2 :]
    if "--step" in args:
        i = args.index("--step")
        step = args[i + 1]
        args = args[:i] + args[i + 2 :]

    files = [f for f in args if Path(f).exists()]
    if not files:
        print(
            json.dumps(
                make_result(
                    "prompt_ambiguity",
                    0.0,
                    {"error": "no input files"},
                    weight=WEIGHTS.get("prompt_ambiguity", 0.07),
                    error="no input files",
                ),
                indent=2,
            )
        )
        return

    print(json.dumps(analyse_files(files, prompts_dir=prompts_dir, step=step), indent=2))


if __name__ == "__main__":
    main()
