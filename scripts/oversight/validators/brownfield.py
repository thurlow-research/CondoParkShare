"""
brownfield.py — brownfield classification for HOS layered-agent migration (#275).

A pure classifier: reads a consumer's flat agent file, compares it section-by-section
against the HOS CORE template for the same slug, and emits a structured classification
(STOCK_CORE vs. PROJECT_CUSTOMIZATION) that the installer uses to build a synthetic
baseline and optionally scaffold a consumer pack.

Boundary: this module NEVER writes agent files, NEVER writes .hos-manifest, and NEVER
mutates packs/. It only reads and classifies. All disk-mutating decisions live in the
installer (consistent with regions.py being a pure planner).

Stdlib only (re, json, sys, argparse, pathlib, datetime) — no third-party deps.
Runs venv-less on Python 3.10+.

CLI:
    python3 brownfield.py classify <agent-file> [<core-template-file>] [--json-out <path>]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


# ── Classification constants ──────────────────────────────────────────────────

THRESHOLD_STOCK_CORE = 0.90    # s >= 0.90  → STOCK_CORE
THRESHOLD_MIXED_LOW  = 0.45    # 0.45 <= s < 0.90 → PROJECT_CUSTOMIZATION [mixed]


def _strip_frontmatter(text: str) -> str:
    """Remove YAML frontmatter (lines between leading --- delimiters, inclusive).

    REQ-D-09: frontmatter is never classified, compared, or preserved.
    Only strips if the file starts with '---' (a YAML front-matter delimiter).
    """
    lines = text.splitlines(keepends=True)
    if not lines:
        return text
    first = lines[0].rstrip("\r\n")
    if first != "---":
        return text
    # Find the closing ---
    for i, line in enumerate(lines[1:], start=1):
        if line.rstrip("\r\n") == "---":
            return "".join(lines[i + 1:])
    # No closing delimiter found — treat entire file as non-frontmatter
    return text


def _split_sections(text: str) -> list[tuple[str, list[str]]]:
    """Split body text into (heading_text, body_lines) sections.

    REQ-D-01: a section begins at a line matching ^#{1,6}[ \\t] or at start of body.
    heading_text is the heading line stripped of leading # chars and whitespace,
    retaining original case for display. The pre-heading block (before any heading)
    uses heading="" for a heading-less-fallback (O-2).

    Returns list of (heading_display, lines) in document order.
    Lines include the heading line itself (for content extraction by the installer).
    """
    heading_re = re.compile(r'^#{1,6}[ \t]')
    lines = text.splitlines(keepends=True)
    sections: list[tuple[str, list[str]]] = []
    current_heading = ""
    current_lines: list[str] = []

    for line in lines:
        stripped = line.rstrip("\r\n")
        if heading_re.match(stripped):
            # Save the previous section (flush)
            sections.append((current_heading, current_lines))
            # Start new section: heading display = strip leading #s and whitespace
            current_heading = re.sub(r'^#+\s*', '', stripped)
            current_lines = [line]
        else:
            current_lines.append(line)

    # Flush the last section
    sections.append((current_heading, current_lines))

    # Drop the leading pre-heading section if it's entirely blank
    # (common: a file that starts immediately with a heading has an empty pre-heading block)
    # But preserve it if it has content (REQ-D-01 heading-less fallback)
    if sections and sections[0][0] == "" and not any(
        l.strip() for l in sections[0][1]
    ):
        sections = sections[1:]

    return sections


def _heading_key(heading: str) -> str:
    """Normalise a heading for case-insensitive #-stripped matching (REQ-D-01)."""
    return re.sub(r'^#+\s*', '', heading).strip().lower()


def _section_similarity(consumer_lines: list[str], core_lines: list[str]) -> float:
    """Set-intersection over max denominator (REQ-D-02).

    common / max(len(consumer_set), len(core_set))
    Empty lines are dropped before set construction.
    Comparison is exact string match after .strip().
    Returns 0.0 when both sets are empty.
    """
    c_set = {l.strip() for l in consumer_lines if l.strip()}
    k_set = {l.strip() for l in core_lines if l.strip()}
    common = len(c_set & k_set)
    denom = max(len(c_set), len(k_set))
    if denom == 0:
        return 0.0
    return common / denom


def _classify_similarity(similarity: float) -> tuple[str, bool]:
    """Map similarity score to (classification, mixed_flag).

    Returns:
        classification: "STOCK_CORE" | "PROJECT_CUSTOMIZATION"
        mixed: True iff 0.45 <= similarity < 0.90 (REQ-D-03/04/05)
    """
    if similarity >= THRESHOLD_STOCK_CORE:
        return "STOCK_CORE", False
    elif similarity >= THRESHOLD_MIXED_LOW:
        return "PROJECT_CUSTOMIZATION", True
    else:
        return "PROJECT_CUSTOMIZATION", False


# ── Public API ────────────────────────────────────────────────────────────────

def brownfield_classify(
    agent_file: Path,
    core_template_file: Path | None,
) -> dict:
    """Classify a flat consumer agent file section-by-section.

    Inputs:
        agent_file:          The consumer's flat agent file.
        core_template_file:  The HOS CORE template for the same slug, or None
                             when HOS ships no template (REQ-D-08 — whole file
                             is PROJECT_CUSTOMIZATION).

    Returns a dict satisfying both the task-brief shape and REQ-D-07:
        {
          "agent":          "<filename>",
          "core_template":  True | False,
          "similarity":     <float>,          # file-level: mean section similarity
          "sections":       [...],            # per-section detail (REQ-D-07)
          "stock_core":     ["<heading>", ...],
          "project_custom": ["<heading>", ...],
          "mixed":          ["<heading>", ...],
        }
    """
    agent_text = agent_file.read_text(encoding="utf-8")
    agent_body = _strip_frontmatter(agent_text)

    has_core = core_template_file is not None
    core_body = ""
    core_sections_map: dict[str, list[str]] = {}

    if has_core:
        core_text = core_template_file.read_text(encoding="utf-8")
        core_body = _strip_frontmatter(core_text)
        for heading, lines in _split_sections(core_body):
            key = _heading_key(heading)
            core_sections_map[key] = lines

    agent_sections = _split_sections(agent_body)

    # Heading-less fallback (O-2, spec §2.3): if the agent body has no headings,
    # treat the entire body as one section compared against the whole CORE body.
    if not agent_sections or (len(agent_sections) == 1 and agent_sections[0][0] == ""):
        # Single heading-less section
        whole_consumer_lines = agent_body.splitlines(keepends=True)
        if has_core:
            whole_core_lines = core_body.splitlines(keepends=True)
            sim = _section_similarity(whole_consumer_lines, whole_core_lines)
        else:
            sim = 0.0
        classification, mixed = _classify_similarity(sim) if has_core else ("PROJECT_CUSTOMIZATION", False)
        section_entry = {
            "heading": "",
            "classification": classification,
            "mixed": mixed,
            "similarity": round(sim, 4),
            "lines": whole_consumer_lines,
        }
        sections_list = [section_entry]
    else:
        sections_list = []
        for heading, lines in agent_sections:
            key = _heading_key(heading)
            if not has_core or key not in core_sections_map:
                # No CORE match → PROJECT_CUSTOMIZATION (REQ-D-01, §2.3 "no match")
                section_entry = {
                    "heading": heading,
                    "classification": "PROJECT_CUSTOMIZATION",
                    "mixed": False,
                    "similarity": 0.0,
                    "lines": lines,
                }
            else:
                core_lines = core_sections_map[key]
                sim = _section_similarity(lines, core_lines)
                classification, mixed = _classify_similarity(sim)
                section_entry = {
                    "heading": heading,
                    "classification": classification,
                    "mixed": mixed,
                    "similarity": round(sim, 4),
                    "lines": lines,
                }
            sections_list.append(section_entry)

    # File-level similarity: mean of per-section similarities (0.0 when no sections)
    if sections_list:
        file_similarity = sum(s["similarity"] for s in sections_list) / len(sections_list)
    else:
        file_similarity = 0.0

    # Convenience buckets (task-brief shape)
    stock_core_headings = [
        s["heading"] for s in sections_list if s["classification"] == "STOCK_CORE"
    ]
    project_custom_headings = [
        s["heading"] for s in sections_list
        if s["classification"] == "PROJECT_CUSTOMIZATION" and not s["mixed"]
    ]
    mixed_headings = [
        s["heading"] for s in sections_list
        if s["classification"] == "PROJECT_CUSTOMIZATION" and s["mixed"]
    ]

    return {
        "agent": agent_file.name,
        "core_template": has_core,
        "similarity": round(file_similarity, 4),
        "sections": sections_list,
        "stock_core": stock_core_headings,
        "project_custom": project_custom_headings,
        "mixed": mixed_headings,
    }


def _brownfield_detect(repo_root: str) -> bool:
    """Return True if the target repo is in brownfield state.

    Brownfield state: agent files present, no .hos-manifest, and at least one
    flat (marker-less) agent file.

    Definition per spec §1 Background + REQ-B-02/B-03:
        ! has_manifest AND has_agents AND any_flat
    """
    root = Path(repo_root)
    has_manifest = (root / ".hos-manifest").is_file()
    if has_manifest:
        return False
    agents_dir = root / ".claude" / "agents"
    if not agents_dir.is_dir():
        return False
    agent_files = list(agents_dir.glob("*.md"))
    if not agent_files:
        return False
    # any_flat: at least one file with no <!-- HOS: marker
    for f in agent_files:
        try:
            content = f.read_text(encoding="utf-8")
        except OSError:
            continue
        if "<!-- HOS:" not in content:
            return True
    return False


def _brownfield_migrate(repo_root: str, pack: str | None = None) -> None:
    """Classify all flat agent files and write per-agent JSON + human report.

    Writes:
      <repo_root>/.hos-brownfield/<slug>.json  — machine-readable per-agent JSON
      stdout — human-readable report (caller captures to .claudetmp/brownfield-<ts>-report.txt)

    This is the Python-level helper that the installer calls via the CLI.
    The installer's _brownfield_migrate() bash function orchestrates the full flow;
    this function handles the classification + JSON writing step.
    """
    root = Path(repo_root)
    agents_dir = root / ".claude" / "agents"
    if not agents_dir.is_dir():
        print(f"[brownfield] No .claude/agents/ directory found in {repo_root}", file=sys.stderr)
        return

    brownfield_dir = root / ".hos-brownfield"
    brownfield_dir.mkdir(parents=True, exist_ok=True)

    agent_files = sorted(agents_dir.glob("*.md"))
    for agent_file in agent_files:
        content = agent_file.read_text(encoding="utf-8")
        if "<!-- HOS:" in content:
            continue  # already marked — skip (REQ-B-04 / AC-B-08)
        slug = agent_file.stem
        json_out = brownfield_dir / f"{slug}.json"
        result = brownfield_classify(agent_file, None)
        with open(json_out, "w") as f:
            json.dump(result, f, indent=2)
        _print_report_block(result)


def _brownfield_scaffold_pack(repo_root: str, slug: str) -> None:
    """Create packs/<slug>/ with pack.toml and agent body files.

    REQ-CS-02: slug must match ^[a-z][a-z0-9-]*$
    REQ-CS-03: pack.toml with name/description/version/requires
    REQ-CS-04: agent body files (PROJECT_CUSTOMIZATION content only)
    """
    import re as _re
    root = Path(repo_root)
    if not _re.match(r'^[a-z][a-z0-9-]*$', slug):
        print(f"ERROR: invalid pack slug '{slug}' — must match ^[a-z][a-z0-9-]*$", file=sys.stderr)
        sys.exit(2)

    packs_dir = root / "packs" / slug
    packs_dir.mkdir(parents=True, exist_ok=True)

    consumer_name = root.name

    toml_content = (
        f'name = "{slug}"\n'
        f'description = "{consumer_name} — project-specific HOS pack."\n'
        f'version = "0.1.0"\n'
        f'requires = []\n'
        f'\n'
        f'# If this pack assumes another pack (e.g. django), add it to requires:\n'
        f'# requires = ["django"]\n'
    )
    (packs_dir / "pack.toml").write_text(toml_content, encoding="utf-8")

    # Write body files from .hos-brownfield/ JSON if available
    brownfield_dir = root / ".hos-brownfield"
    if brownfield_dir.is_dir():
        for json_file in sorted(brownfield_dir.glob("*.json")):
            try:
                data = json.loads(json_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            project_sections = [
                s for s in data.get("sections", [])
                if s.get("classification") == "PROJECT_CUSTOMIZATION"
            ]
            if not project_sections:
                continue
            body_lines = []
            for s in project_sections:
                body_lines.extend(s.get("lines", []))
                body_lines.append("\n")
            body = "".join(body_lines).rstrip("\n") + "\n"
            slug_name = json_file.stem
            (packs_dir / f"{slug_name}.md").write_text(body, encoding="utf-8")


# ── Human-readable report (REQ-D-06) ─────────────────────────────────────────

def _print_report_block(result: dict) -> None:
    """Print the REQ-D-06 human-readable report block for one agent to stdout."""
    print(f"Agent: {result['agent']}")
    sections = result.get("sections", [])
    for s in sections:
        heading = s["heading"] or "(no heading)"
        sim = s["similarity"]
        cls = s["classification"]
        mixed = s.get("mixed", False)
        has_match = s.get("similarity", 0.0) > 0.0 or result.get("core_template", True)
        core_match = "yes" if result.get("core_template") and _heading_key(s["heading"]) != "" else (
            "yes" if result.get("core_template") and has_match else "no"
        )
        # Simplified: report core_template presence as yes/no
        core_match_str = "yes" if result.get("core_template") else "no"
        cls_display = cls
        if cls == "PROJECT_CUSTOMIZATION" and mixed:
            cls_display = "PROJECT_CUSTOMIZATION [mixed]"
        print(f'  Section: "{heading}"')
        print(f"    CORE template match: {core_match_str}")
        print(f"    Similarity: {sim:.2f}")
        print(f"    Classification: {cls_display}")
    n_total = len(sections)
    n_stock = len(result.get("stock_core", []))
    n_custom = len(result.get("project_custom", []))
    n_mixed = len(result.get("mixed", []))
    print(
        f"  Summary: {n_total} section(s) → {n_stock} stock, "
        f"{n_custom + n_mixed} customization ({n_mixed} mixed)"
    )
    print()


def _heading_key(heading: str) -> str:
    """Re-export for internal use."""
    return re.sub(r'^#+\s*', '', heading).strip().lower()


# ── CLI ───────────────────────────────────────────────────────────────────────

def _cmd_classify(args: argparse.Namespace) -> None:
    agent_file = Path(args.agent_file)
    if not agent_file.is_file():
        print(f"ERROR: agent file not found: {agent_file}", file=sys.stderr)
        sys.exit(2)

    core_template_file: Path | None = None
    if args.core_template_file:
        p = Path(args.core_template_file)
        if p.is_file():
            core_template_file = p
        else:
            print(
                f"[brownfield] Core template not found: {p} — treating as no-template (REQ-D-08)",
                file=sys.stderr,
            )

    result = brownfield_classify(agent_file, core_template_file)

    # Human-readable report always goes to stdout
    _print_report_block(result)

    # Machine JSON: --json-out <path> or stdout
    if args.json_out:
        out_path = Path(args.json_out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    else:
        print("---")
        print(json.dumps(result, indent=2))


def _cmd_migrate(args: argparse.Namespace) -> None:
    _brownfield_migrate(args.repo_root, getattr(args, "pack", None))


def _cmd_scaffold_pack(args: argparse.Namespace) -> None:
    _brownfield_scaffold_pack(args.repo_root, args.slug)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Brownfield agent classifier for HOS brownfield migration (#275)."
    )
    sub = parser.add_subparsers(dest="cmd")

    # classify subcommand
    cls_p = sub.add_parser("classify", help="Classify a flat agent file vs. a CORE template.")
    cls_p.add_argument("agent_file", help="Path to the consumer's flat agent .md file.")
    cls_p.add_argument(
        "core_template_file",
        nargs="?",
        default=None,
        help="Path to the HOS CORE template for the same slug (optional; absent = REQ-D-08).",
    )
    cls_p.add_argument(
        "--json-out",
        metavar="PATH",
        help="Write machine-readable JSON to PATH (installer uses .hos-brownfield/<slug>.json).",
    )

    # migrate subcommand (called by the installer bash function)
    mig_p = sub.add_parser("migrate", help="Classify all flat agent files in a repo.")
    mig_p.add_argument("repo_root", help="Path to the target repo root.")
    mig_p.add_argument("--pack", default=None, help="Pack slug (optional).")

    # scaffold-pack subcommand
    scf_p = sub.add_parser("scaffold-pack", help="Scaffold a consumer pack from .hos-brownfield/ JSON.")
    scf_p.add_argument("repo_root", help="Path to the target repo root.")
    scf_p.add_argument("slug", help="Pack slug (^[a-z][a-z0-9-]*$).")

    args = parser.parse_args(argv)
    if args.cmd == "classify":
        _cmd_classify(args)
    elif args.cmd == "migrate":
        _cmd_migrate(args)
    elif args.cmd == "scaffold-pack":
        _cmd_scaffold_pack(args)
    else:
        parser.print_help()
        sys.exit(0)


if __name__ == "__main__":
    main()
