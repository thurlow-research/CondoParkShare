#!/usr/bin/env python3
"""
No-loss verification gate for HOS agent migration.

After a layered install, asserts every non-trivial line of each BEFORE-snapshot
flat agent is accounted for in the installed agent's regions or an allowlist.

Exit 0 = all agents pass. Exit 1 = one or more agents block.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MARKER_RE = re.compile(
    r"^<!--\s+HOS:(CORE|PACK:[a-z0-9][a-z0-9-]*|PROJECT):(START|END)\s+-->$"
)

REGION_PRIORITY = ["PROJECT", "PACK:django", "CORE"]

STOPWORDS = frozenset(
    [
        "a", "an", "the", "and", "or", "of", "in", "to", "for", "with",
        "on", "at", "by", "as", "is", "are", "was", "were", "be", "been",
        "being", "have", "has", "had", "do", "does", "did", "will", "would",
        "could", "should", "may", "might", "must", "shall", "can", "need",
        "that", "this", "these", "those", "it", "its", "not", "no", "from",
        "into", "about", "than", "when", "if", "all", "each", "any", "both",
        "more", "most", "other", "such", "only", "same", "so", "yet", "but",
        "also", "up", "out", "use", "used", "using", "via", "per",
    ]
)

# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


@dataclass
class GateConfig:
    jaccard: float = 0.85
    token_coverage: float = 0.80
    region_priority: list[str] = field(default_factory=lambda: list(REGION_PRIORITY))
    trivial_headings: frozenset[str] = field(default_factory=frozenset)


def load_config(config_dir: Path) -> GateConfig:
    gate_toml = config_dir / "gate.toml"
    trivial_file = config_dir / "trivial-headings.txt"

    jaccard = 0.85
    token_coverage = 0.80
    region_priority = list(REGION_PRIORITY)

    if gate_toml.exists():
        # tomllib is stdlib in Python 3.11+; fall back to manual parse for older.
        try:
            import tomllib  # type: ignore[import]

            with open(gate_toml, "rb") as fh:
                data = tomllib.load(fh)
        except ImportError:
            data = _parse_toml_minimal(gate_toml.read_text())

        jaccard = float(data.get("jaccard", jaccard))
        token_coverage = float(data.get("token_coverage", token_coverage))
        if "region_priority" in data:
            region_priority = list(data["region_priority"])

    trivial: set[str] = set()
    if trivial_file.exists():
        for raw in trivial_file.read_text(encoding="utf-8").splitlines():
            stripped = raw.strip()
            if stripped and not stripped.startswith("#"):
                trivial.add(stripped.lower())

    return GateConfig(
        jaccard=jaccard,
        token_coverage=token_coverage,
        region_priority=region_priority,
        trivial_headings=frozenset(trivial),
    )


def _parse_toml_minimal(text: str) -> dict:
    """Parse the flat subset of TOML used in gate.toml (no sections needed)."""
    result: dict = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        if val.startswith("[") and val.endswith("]"):
            items = [
                v.strip().strip('"').strip("'")
                for v in val[1:-1].split(",")
                if v.strip()
            ]
            result[key] = items
        elif val.startswith('"') or val.startswith("'"):
            result[key] = val.strip('"').strip("'")
        else:
            try:
                result[key] = float(val)
            except ValueError:
                result[key] = val
    return result


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

_MD_STRIP_RE = re.compile(
    r"(\*\*|__|\*|_|`{1,3})"  # bold/italic/code spans
    r"|^\s*[-*+]\s+"           # unordered list markers (line-start)
    r"|^\s*\d+\.\s+"           # ordered list markers
    r"|^\s*#+\s*"              # heading hashes
    r"|^\s*>\s*"               # blockquote markers
    r"|^\s*[|:]+[-| :]+[|:]+\s*$"  # table delimiter rows
    r"|^\s*[-_*]{3,}\s*$",     # horizontal rules
    re.MULTILINE,
)

_UNICODE_DASH_RE = re.compile(r"[–—‒−]")  # en-dash, em-dash, etc.
_SMART_QUOTE_RE = re.compile(r"[‘’“”«»]")
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    """Lowercase, strip MD syntax, normalize dashes/quotes/whitespace."""
    # Unicode normalization
    text = unicodedata.normalize("NFKD", text)
    # Replace unicode dashes with space
    text = _UNICODE_DASH_RE.sub(" ", text)
    # Replace smart quotes with ASCII
    text = _SMART_QUOTE_RE.sub("'", text)
    # Strip MD syntax
    text = _MD_STRIP_RE.sub(" ", text)
    # Lowercase
    text = text.lower()
    # Collapse whitespace
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text


def tokenize(text: str) -> frozenset[str]:
    """Return word set from normalized text (one-or-more alphabetic/digit chars)."""
    return frozenset(w for w in re.findall(r"[a-z0-9]+", text) if len(w) >= 2)


def content_tokens(text: str) -> frozenset[str]:
    """Tokens after removing stopwords — used for Stage B coverage ratio."""
    return tokenize(text) - STOPWORDS


def line_hash(normalized: str) -> str:
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Trivial-line detection
# ---------------------------------------------------------------------------

_BARE_PUNCT_RE = re.compile(r"^[-|: *_]{0,10}$")
_HEADING_RE = re.compile(r"^#+\s+(.*)")


def is_trivial(raw_line: str, trivial_headings: frozenset[str]) -> bool:
    stripped = raw_line.strip()
    if not stripped:
        return True
    # Markdown horizontal rules
    if re.match(r"^(\-{3,}|\*{3,}|_{3,})$", stripped):
        return True
    # Bare punctuation / table delimiter
    if _BARE_PUNCT_RE.match(stripped):
        return True
    # Table delimiter rows (|---|---|)
    if re.match(r"^\|?[\s\-:|]+\|[\s\-:|]+", stripped):
        return True
    # Headings
    m = _HEADING_RE.match(stripped)
    if m:
        heading_text = normalize_text(m.group(1))
        if heading_text in trivial_headings:
            return True
        # A heading is trivial only if it's in the stop-set.
        # Headings with domain nouns (not in stop-set) are NON-trivial.
        return False
    return False


# ---------------------------------------------------------------------------
# Front-matter parsing
# ---------------------------------------------------------------------------


@dataclass
class FrontMatter:
    raw: str
    name: Optional[str] = None
    tools: list[str] = field(default_factory=list)
    model: Optional[str] = None
    description: Optional[str] = None


def parse_front_matter(text: str) -> tuple[FrontMatter, str]:
    """
    Split YAML front-matter (between first two ---) from body.
    Returns (FrontMatter, body_text).
    """
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return FrontMatter(raw=""), text

    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break

    if end is None:
        return FrontMatter(raw=""), text

    fm_lines = lines[1:end]
    body = "\n".join(lines[end + 1 :])

    fm = FrontMatter(raw="\n".join(fm_lines))
    in_tools = False
    for ln in fm_lines:
        if re.match(r"^name:\s*(.+)", ln):
            fm.name = re.match(r"^name:\s*(.+)", ln).group(1).strip()
            in_tools = False
        elif re.match(r"^model:\s*(.+)", ln):
            fm.model = re.match(r"^model:\s*(.+)", ln).group(1).strip()
            in_tools = False
        elif re.match(r"^description:\s*(.+)", ln):
            fm.description = re.match(r"^description:\s*(.+)", ln).group(1).strip()
            in_tools = False
        elif re.match(r"^tools:", ln):
            in_tools = True
        elif in_tools and re.match(r"^\s+-\s+(\S+)", ln):
            m = re.match(r"^\s+-\s+(\S+)", ln)
            fm.tools.append(m.group(1).strip())
        elif ln and not ln.startswith(" ") and not ln.startswith("\t"):
            in_tools = False

    return fm, body


# ---------------------------------------------------------------------------
# Region extraction from installed (after) agents
# ---------------------------------------------------------------------------


@dataclass
class InstalledRegions:
    regions: dict[str, str]  # region_id -> text content


def extract_regions(text: str) -> InstalledRegions:
    """Extract HOS region blocks by marker pairs."""
    regions: dict[str, str] = {}
    current_id: Optional[str] = None
    current_lines: list[str] = []

    for line in text.splitlines():
        m = MARKER_RE.match(line.rstrip())
        if m:
            region_id = m.group(1)
            boundary = m.group(2)
            if boundary == "START":
                current_id = region_id
                current_lines = []
            elif boundary == "END" and current_id == region_id:
                regions[region_id] = "\n".join(current_lines)
                current_id = None
                current_lines = []
        elif current_id is not None:
            current_lines.append(line)

    return InstalledRegions(regions=regions)


# ---------------------------------------------------------------------------
# Sentence splitting for Stage A matching
# ---------------------------------------------------------------------------


def split_sentences(text: str) -> list[str]:
    """Split text into candidate sentences for token-set matching."""
    # Split on sentence-ending punctuation followed by whitespace, or on newlines.
    sentences = re.split(r"(?<=[.!?])\s+|\n+", text)
    return [s.strip() for s in sentences if s.strip()]


def jaccard(a: frozenset, b: frozenset) -> float:
    if not a and not b:
        return 1.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


# ---------------------------------------------------------------------------
# Allowlist loading
# ---------------------------------------------------------------------------


@dataclass
class AllowEntry:
    line: str
    line_hash: str
    covered_by: str
    reason: str
    approved_by: str
    approved_at: str
    _used: bool = field(default=False, repr=False)


def load_allowlist(config_dir: Path, agent_slug: str) -> list[AllowEntry]:
    allow_file = config_dir / "dropped" / f"{agent_slug}.allow.yml"
    if not allow_file.exists():
        return []

    entries: list[AllowEntry] = []
    text = allow_file.read_text(encoding="utf-8")

    # Minimal YAML list parser: each item starts with "- line:" and has
    # indented sub-keys. No pyyaml dependency.
    try:
        import yaml  # type: ignore[import]

        raw_list = yaml.safe_load(text) or []
        for item in raw_list:
            normalized = normalize_text(str(item.get("line", "")))
            lh = line_hash(normalized)
            entries.append(
                AllowEntry(
                    line=str(item.get("line", "")),
                    line_hash=lh,
                    covered_by=str(item.get("covered_by", "")),
                    reason=str(item.get("reason", "")),
                    approved_by=str(item.get("approved_by", "")),
                    approved_at=str(item.get("approved_at", "")),
                )
            )
    except ImportError:
        entries = _parse_allowlist_minimal(text)

    return entries


def _parse_allowlist_minimal(text: str) -> list[AllowEntry]:
    """Parse the flat YAML list structure without pyyaml."""
    entries: list[AllowEntry] = []
    current: dict[str, str] = {}

    for raw_line in text.splitlines():
        stripped = raw_line.rstrip()
        if stripped.startswith("- line:"):
            if current:
                entries.append(_make_allow_entry(current))
                current = {}
            val = stripped[len("- line:") :].strip()
            current["line"] = val.strip('"').strip("'")
        elif re.match(r"^\s+(covered_by|reason|approved_by|approved_at):", stripped):
            m = re.match(r"^\s+(\w+):\s*(.*)", stripped)
            if m:
                current[m.group(1)] = m.group(2).strip().strip('"').strip("'")

    if current:
        entries.append(_make_allow_entry(current))

    return entries


def _make_allow_entry(d: dict) -> AllowEntry:
    normalized = normalize_text(d.get("line", ""))
    return AllowEntry(
        line=d.get("line", ""),
        line_hash=line_hash(normalized),
        covered_by=d.get("covered_by", ""),
        reason=d.get("reason", ""),
        approved_by=d.get("approved_by", ""),
        approved_at=d.get("approved_at", ""),
    )


# ---------------------------------------------------------------------------
# Line matching (Stage A + Stage B)
# ---------------------------------------------------------------------------

# Coverage outcome labels
COVERED = "COVERED"
COVERED_REWORDED = "COVERED-REWORDED"
ALLOWLISTED = "ALLOWLISTED"
UNACCOUNTED = "UNACCOUNTED"


@dataclass
class LineResult:
    raw: str
    normalized: str
    outcome: str
    region: Optional[str] = None
    score: Optional[float] = None
    allow_entry: Optional[AllowEntry] = None


def match_line(
    raw_line: str,
    installed: InstalledRegions,
    allowlist: list[AllowEntry],
    config: GateConfig,
) -> LineResult:
    norm = normalize_text(raw_line)
    src_tokens = tokenize(norm)
    src_content = content_tokens(norm)
    lh = line_hash(norm)

    # Stage A: exact subset or Jaccard >= threshold against any sentence in any region.
    best_stage_a_region: Optional[str] = None
    best_stage_a_score: float = 0.0

    # Iterate regions in priority order so attribution goes to highest-priority region.
    ordered_region_ids = _priority_order(list(installed.regions.keys()), config.region_priority)

    for region_id in ordered_region_ids:
        region_text = installed.regions[region_id]
        sentences = split_sentences(region_text)
        for sentence in sentences:
            sent_norm = normalize_text(sentence)
            sent_tokens = tokenize(sent_norm)
            # Subset test: source tokens are a subset of sentence tokens (source is contained)
            if src_tokens and src_tokens.issubset(sent_tokens):
                return LineResult(raw=raw_line, normalized=norm, outcome=COVERED, region=region_id, score=1.0)
            # Jaccard test
            j = jaccard(src_tokens, sent_tokens)
            if j >= config.jaccard:
                # Take highest priority region match
                if best_stage_a_region is None:
                    best_stage_a_region = region_id
                    best_stage_a_score = j

    if best_stage_a_region is not None:
        return LineResult(
            raw=raw_line,
            normalized=norm,
            outcome=COVERED,
            region=best_stage_a_region,
            score=best_stage_a_score,
        )

    # Stage B: best single region covering >= token_coverage of source content tokens.
    best_b_region: Optional[str] = None
    best_b_score: float = 0.0

    for region_id in ordered_region_ids:
        region_text = installed.regions[region_id]
        region_norm = normalize_text(region_text)
        region_tokens = content_tokens(region_norm)
        if not src_content:
            # No content tokens after stopword removal — treat as trivial match
            if region_tokens:
                best_b_region = region_id
                best_b_score = 1.0
                break
            continue
        covered = src_content & region_tokens
        score = len(covered) / len(src_content)
        if score > best_b_score:
            best_b_score = score
            best_b_region = region_id

    if best_b_score >= config.token_coverage and best_b_region is not None:
        return LineResult(
            raw=raw_line,
            normalized=norm,
            outcome=COVERED_REWORDED,
            region=best_b_region,
            score=best_b_score,
        )

    # Check allowlist by normalized-line hash.
    for entry in allowlist:
        if entry.line_hash == lh:
            entry._used = True  # type: ignore[attr-defined]
            return LineResult(
                raw=raw_line,
                normalized=norm,
                outcome=ALLOWLISTED,
                region=entry.covered_by or None,
                allow_entry=entry,
            )

    return LineResult(raw=raw_line, normalized=norm, outcome=UNACCOUNTED)


def _priority_order(region_ids: list[str], priority: list[str]) -> list[str]:
    """Return region_ids sorted by priority list (highest priority first); unlisted last."""
    def rank(rid: str) -> int:
        for i, p in enumerate(priority):
            if rid == p:
                return i
        return len(priority)
    return sorted(region_ids, key=rank)


# ---------------------------------------------------------------------------
# Front-matter diff
# ---------------------------------------------------------------------------


@dataclass
class FrontMatterDiff:
    name_mismatch: bool = False
    removed_tools: list[str] = field(default_factory=list)
    added_tools: list[str] = field(default_factory=list)
    model_changed: Optional[tuple[str, str]] = None  # (before, after)
    description_changed: Optional[tuple[str, str]] = None


def diff_front_matter(before: FrontMatter, after: FrontMatter) -> FrontMatterDiff:
    d = FrontMatterDiff()
    if before.name and after.name and before.name != after.name:
        d.name_mismatch = True
    elif before.name and not after.name:
        d.name_mismatch = True

    before_tools = set(before.tools)
    after_tools = set(after.tools)
    d.removed_tools = sorted(before_tools - after_tools)
    d.added_tools = sorted(after_tools - before_tools)

    if before.model and after.model and before.model != after.model:
        d.model_changed = (before.model, after.model)

    if before.description and after.description and before.description != after.description:
        d.description_changed = (before.description, after.description)

    return d


# ---------------------------------------------------------------------------
# Per-agent verification
# ---------------------------------------------------------------------------


@dataclass
class AgentReport:
    agent: str
    passes: bool
    blocking_reasons: list[str] = field(default_factory=list)

    trivial_count: int = 0
    core_count: int = 0
    pack_count: int = 0
    project_count: int = 0
    reworded_count: int = 0
    allowlisted_count: int = 0
    unaccounted_count: int = 0

    fm_diff: Optional[FrontMatterDiff] = None
    line_results: list[LineResult] = field(default_factory=list)
    unused_allowlist: list[AllowEntry] = field(default_factory=list)


def verify_agent(
    before_path: Path,
    after_path: Path,
    config: GateConfig,
    config_dir: Path,
) -> AgentReport:
    agent_slug = before_path.stem
    report = AgentReport(agent=agent_slug, passes=False)

    before_text = before_path.read_text(encoding="utf-8")
    allowlist = load_allowlist(config_dir, agent_slug)

    before_fm, before_body = parse_front_matter(before_text)

    if not after_path.exists():
        report.blocking_reasons.append(f"after-file missing: {after_path}")
        return report

    after_text = after_path.read_text(encoding="utf-8")
    after_fm, after_body = parse_front_matter(after_text)

    # Front-matter diff
    fm_diff = diff_front_matter(before_fm, after_fm)
    report.fm_diff = fm_diff

    if fm_diff.name_mismatch:
        report.blocking_reasons.append(
            f"name mismatch: before={before_fm.name!r} after={after_fm.name!r}"
        )

    if fm_diff.removed_tools:
        # Check allowlist for tools
        for tool in fm_diff.removed_tools:
            tool_norm = f"tool {tool.lower()}"
            tool_lh = line_hash(tool_norm)
            matched = any(e.line_hash == tool_lh for e in allowlist)
            if not matched:
                report.blocking_reasons.append(f"tool removed (not allowlisted): {tool!r}")

    # Extract installed regions from the after-file
    installed = extract_regions(after_text)

    if not installed.regions:
        # After-file has no regions — treat entire body as one implicit CORE region.
        installed = InstalledRegions(regions={"CORE": after_body})

    # Match each non-trivial line from before-body
    for raw_line in before_body.splitlines():
        if is_trivial(raw_line, config.trivial_headings):
            report.trivial_count += 1
            continue

        result = match_line(raw_line, installed, allowlist, config)
        report.line_results.append(result)

        if result.outcome == COVERED:
            region = result.region or "CORE"
            if region == "PROJECT":
                report.project_count += 1
            elif region.startswith("PACK:"):
                report.pack_count += 1
            else:
                report.core_count += 1
        elif result.outcome == COVERED_REWORDED:
            report.reworded_count += 1
        elif result.outcome == ALLOWLISTED:
            report.allowlisted_count += 1
        elif result.outcome == UNACCOUNTED:
            report.unaccounted_count += 1

    # Warn on unused allowlist entries
    report.unused_allowlist = [e for e in allowlist if not e._used]  # type: ignore[attr-defined]

    # Pass condition: no unaccounted lines, no blocking front-matter issues
    has_unaccounted = report.unaccounted_count > 0
    has_blocking = bool(report.blocking_reasons)
    report.passes = not has_unaccounted and not has_blocking

    return report


# ---------------------------------------------------------------------------
# Output serialization
# ---------------------------------------------------------------------------


def report_to_json(r: AgentReport) -> dict:
    lines_out = []
    for lr in r.line_results:
        lines_out.append(
            {
                "raw": lr.raw,
                "normalized": lr.normalized,
                "outcome": lr.outcome,
                "region": lr.region,
                "score": lr.score,
            }
        )

    fm_diff_out = None
    if r.fm_diff:
        fm_diff_out = {
            "name_mismatch": r.fm_diff.name_mismatch,
            "removed_tools": r.fm_diff.removed_tools,
            "added_tools": r.fm_diff.added_tools,
            "model_changed": list(r.fm_diff.model_changed) if r.fm_diff.model_changed else None,
            "description_changed": list(r.fm_diff.description_changed)
            if r.fm_diff.description_changed
            else None,
        }

    return {
        "agent": r.agent,
        "passes": r.passes,
        "blocking_reasons": r.blocking_reasons,
        "counts": {
            "trivial": r.trivial_count,
            "core": r.core_count,
            "pack": r.pack_count,
            "project": r.project_count,
            "reworded": r.reworded_count,
            "allowlisted": r.allowlisted_count,
            "unaccounted": r.unaccounted_count,
        },
        "fm_diff": fm_diff_out,
        "unused_allowlist": [
            {
                "line": e.line,
                "covered_by": e.covered_by,
                "reason": e.reason,
            }
            for e in r.unused_allowlist
        ],
        "lines": lines_out,
    }


def report_to_markdown(r: AgentReport) -> str:
    lines: list[str] = []
    status = "PASS" if r.passes else "FAIL"
    lines.append(f"# Agent: {r.agent} — {status}\n")

    lines.append("## Counts\n")
    lines.append(f"| Metric | Count |")
    lines.append(f"|--------|-------|")
    lines.append(f"| CORE | {r.core_count} |")
    lines.append(f"| PACK | {r.pack_count} |")
    lines.append(f"| PROJECT | {r.project_count} |")
    lines.append(f"| Reworded | {r.reworded_count} |")
    lines.append(f"| Allowlisted | {r.allowlisted_count} |")
    lines.append(f"| UNACCOUNTED | {r.unaccounted_count} |")
    lines.append(f"| Trivial (skipped) | {r.trivial_count} |")
    lines.append("")

    if r.blocking_reasons:
        lines.append("## BLOCKING Issues\n")
        for reason in r.blocking_reasons:
            lines.append(f"- {reason}")
        lines.append("")

    if r.fm_diff:
        d = r.fm_diff
        lines.append("## Front-matter diff\n")
        if d.name_mismatch:
            lines.append(f"- **BLOCKING** name mismatch")
        if d.removed_tools:
            lines.append(f"- Removed tools: {d.removed_tools}")
        if d.added_tools:
            lines.append(f"- Added tools: {d.added_tools} (info only)")
        if d.model_changed:
            lines.append(f"- Model changed: {d.model_changed[0]} -> {d.model_changed[1]} (info only)")
        if d.description_changed:
            lines.append(f"- Description changed (info only)")
        lines.append("")

    if r.unused_allowlist:
        lines.append("## WARN: Unused allowlist entries\n")
        for e in r.unused_allowlist:
            lines.append(f"- {e.line!r} (covered_by={e.covered_by!r})")
        lines.append("")

    unaccounted = [lr for lr in r.line_results if lr.outcome == UNACCOUNTED]
    if unaccounted:
        lines.append("## UNACCOUNTED lines\n")
        for lr in unaccounted:
            lines.append(f"- `{lr.raw.strip()}`")
        lines.append("")

    reworded = [lr for lr in r.line_results if lr.outcome == COVERED_REWORDED]
    if reworded:
        lines.append("## COVERED-REWORDED lines\n")
        for lr in reworded:
            lines.append(f"- region={lr.region} score={lr.score:.2f}: `{lr.raw.strip()}`")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Summary line format
# ---------------------------------------------------------------------------

def summary_line(r: AgentReport) -> str:
    status = "PASS" if r.passes else "FAIL"
    return (
        f"{status:4s}  {r.agent:<30s}  "
        f"CORE={r.core_count} PACK={r.pack_count} PROJECT={r.project_count} "
        f"reworded={r.reworded_count} allowlisted={r.allowlisted_count} "
        f"UNACCOUNTED={r.unaccounted_count} trivial={r.trivial_count}"
    )


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------


def run(
    before_dir: Path,
    after_dir: Path,
    config_dir: Path,
    output_dir: Path,
) -> int:
    config = load_config(config_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    before_agents = sorted(before_dir.glob("*.md"))
    if not before_agents:
        print(f"WARN: no .md files found in before-dir {before_dir}", file=sys.stderr)
        return 0

    all_pass = True
    reports: list[AgentReport] = []

    for before_path in before_agents:
        agent_slug = before_path.stem
        after_path = after_dir / before_path.name
        report = verify_agent(before_path, after_path, config, config_dir)
        reports.append(report)

        # Write outputs
        (output_dir / f"{agent_slug}.json").write_text(
            json.dumps(report_to_json(report), indent=2), encoding="utf-8"
        )
        (output_dir / f"{agent_slug}.md").write_text(
            report_to_markdown(report), encoding="utf-8"
        )

        print(summary_line(report))

        if not report.passes:
            all_pass = False
            for reason in report.blocking_reasons:
                print(f"  BLOCKING: {reason}", file=sys.stderr)
            unaccounted = [lr for lr in report.line_results if lr.outcome == UNACCOUNTED]
            for lr in unaccounted:
                print(f"  UNACCOUNTED: {lr.raw.strip()}", file=sys.stderr)

        if report.unused_allowlist:
            for e in report.unused_allowlist:
                print(f"  WARN [{agent_slug}]: unused allowlist entry: {e.line!r}", file=sys.stderr)

    print()
    if all_pass:
        print("PASS — all agents accounted for")
        return 0
    else:
        failed = [r.agent for r in reports if not r.passes]
        print(f"FAIL — {len(failed)} agent(s) blocked: {', '.join(failed)}")
        return 1


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="No-loss verification gate for HOS agent migration."
    )
    parser.add_argument(
        "--before",
        type=Path,
        default=Path(".claudetmp/before-snapshot/.claude/agents"),
        help="Directory containing before-snapshot flat agent .md files",
    )
    parser.add_argument(
        "--after",
        type=Path,
        default=Path(".claude/agents"),
        help="Directory containing installed (after) agent .md files",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config"),
        help="Config directory containing gate.toml and trivial-headings.txt",
    )

    args = parser.parse_args(argv)

    output_dir = Path(".claudetmp/migration/coverage")

    return run(
        before_dir=args.before,
        after_dir=args.after,
        config_dir=args.config,
        output_dir=output_dir,
    )


if __name__ == "__main__":
    sys.exit(main())
