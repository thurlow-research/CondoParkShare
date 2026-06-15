"""
Unit tests for verify_no_loss.py — exercises normalization, trivial detection,
Stage A/B matching, front-matter parsing, region extraction, and allowlist
handling entirely on inline strings (no filesystem fixtures required).
"""

import sys
import types
import unittest
from pathlib import Path

# Make the module importable without installing it.
sys.path.insert(0, str(Path(__file__).parent))

import verify_no_loss as gate

# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


class TestNormalize(unittest.TestCase):
    def test_lowercase(self):
        self.assertEqual(gate.normalize_text("UPPER CASE"), "upper case")

    def test_strip_bold(self):
        self.assertEqual(gate.normalize_text("**Django** models"), "django models")

    def test_strip_italic(self):
        self.assertEqual(gate.normalize_text("_italic_ text"), "italic text")

    def test_strip_backticks(self):
        # Backtick delimiters are stripped; underscored identifiers remain intact.
        # tokenize() is what splits on non-word boundaries — normalize_text only
        # removes the delimiters.
        self.assertEqual(gate.normalize_text("`select_for_update()`"), "select for update()")

    def test_strip_heading_hashes(self):
        self.assertEqual(gate.normalize_text("## Security"), "security")

    def test_strip_list_marker_dash(self):
        self.assertEqual(gate.normalize_text("- item one"), "item one")

    def test_strip_list_marker_star(self):
        self.assertEqual(gate.normalize_text("* item two"), "item two")

    def test_strip_blockquote(self):
        self.assertEqual(gate.normalize_text("> quoted text"), "quoted text")

    def test_unicode_dash_to_space(self):
        result = gate.normalize_text("read–write lock")  # en-dash
        self.assertIn("read", result)
        self.assertIn("write", result)
        self.assertIn("lock", result)

    def test_smart_quote_to_ascii(self):
        result = gate.normalize_text("“Django”")
        self.assertIn("django", result)
        self.assertNotIn("“", result)

    def test_collapse_whitespace(self):
        self.assertEqual(gate.normalize_text("a   b\t\tc"), "a b c")

    def test_strip_leading_trailing(self):
        self.assertEqual(gate.normalize_text("  hello  "), "hello")


class TestTokenize(unittest.TestCase):
    def test_basic(self):
        tokens = gate.tokenize("use select for update")
        self.assertIn("use", tokens)
        self.assertIn("select", tokens)

    def test_min_length_two(self):
        tokens = gate.tokenize("a b cc ddd")
        self.assertNotIn("a", tokens)
        self.assertNotIn("b", tokens)
        self.assertIn("cc", tokens)
        self.assertIn("ddd", tokens)

    def test_strips_punctuation(self):
        tokens = gate.tokenize("booking. creation! overlap?")
        self.assertIn("booking", tokens)
        self.assertIn("creation", tokens)
        self.assertIn("overlap", tokens)


class TestContentTokens(unittest.TestCase):
    def test_removes_stopwords(self):
        tokens = gate.content_tokens("use select for update in a transaction")
        self.assertNotIn("for", tokens)
        self.assertNotIn("in", tokens)
        self.assertNotIn("a", tokens)
        self.assertIn("select", tokens)
        self.assertIn("update", tokens)
        self.assertIn("transaction", tokens)


# ---------------------------------------------------------------------------
# Trivial line detection
# ---------------------------------------------------------------------------

TRIVIAL_HEADINGS = frozenset(
    [
        "project context",
        "before writing code",
        "while writing code",
        "after writing code",
        "security",
        "code style",
        "dispatch escalation",
        "what you do not do",
        "reviewer conflict resolution",
        "before each revision pass",
    ]
)


class TestTrivialDetection(unittest.TestCase):
    def test_blank_line_trivial(self):
        self.assertTrue(gate.is_trivial("", TRIVIAL_HEADINGS))
        self.assertTrue(gate.is_trivial("   ", TRIVIAL_HEADINGS))

    def test_horizontal_rule_trivial(self):
        self.assertTrue(gate.is_trivial("---", TRIVIAL_HEADINGS))
        self.assertTrue(gate.is_trivial("***", TRIVIAL_HEADINGS))
        self.assertTrue(gate.is_trivial("___", TRIVIAL_HEADINGS))
        self.assertTrue(gate.is_trivial("----", TRIVIAL_HEADINGS))

    def test_table_delimiter_trivial(self):
        self.assertTrue(gate.is_trivial("|---|---|", TRIVIAL_HEADINGS))
        self.assertTrue(gate.is_trivial("| :--- | ---: |", TRIVIAL_HEADINGS))

    def test_generic_heading_trivial(self):
        # All of the seeded headings must be trivial
        self.assertTrue(gate.is_trivial("## Project context", TRIVIAL_HEADINGS))
        self.assertTrue(gate.is_trivial("## Before writing code", TRIVIAL_HEADINGS))
        self.assertTrue(gate.is_trivial("## While writing code", TRIVIAL_HEADINGS))
        self.assertTrue(gate.is_trivial("## After writing code", TRIVIAL_HEADINGS))
        self.assertTrue(gate.is_trivial("## Security", TRIVIAL_HEADINGS))
        self.assertTrue(gate.is_trivial("## Code style", TRIVIAL_HEADINGS))
        self.assertTrue(gate.is_trivial("### Dispatch escalation", TRIVIAL_HEADINGS))
        self.assertTrue(gate.is_trivial("## What you do not do", TRIVIAL_HEADINGS))
        self.assertTrue(gate.is_trivial("## Reviewer conflict resolution", TRIVIAL_HEADINGS))
        self.assertTrue(gate.is_trivial("## Before each revision pass", TRIVIAL_HEADINGS))

    def test_domain_heading_nontrivial(self):
        # Headings with domain nouns must NOT be trivial
        self.assertFalse(gate.is_trivial("## Django ORM patterns", TRIVIAL_HEADINGS))
        self.assertFalse(gate.is_trivial("## Booking gates", TRIVIAL_HEADINGS))
        self.assertFalse(gate.is_trivial("## Multi-tenant middleware", TRIVIAL_HEADINGS))
        self.assertFalse(gate.is_trivial("## TOTP enforcement", TRIVIAL_HEADINGS))
        self.assertFalse(gate.is_trivial("## GiST exclusion constraint", TRIVIAL_HEADINGS))

    def test_content_line_nontrivial(self):
        self.assertFalse(
            gate.is_trivial(
                "Use `select_for_update()` around booking creation.", TRIVIAL_HEADINGS
            )
        )
        self.assertFalse(
            gate.is_trivial(
                "Every view that touches tenant data: verify organization.", TRIVIAL_HEADINGS
            )
        )

    def test_bare_punctuation_trivial(self):
        self.assertTrue(gate.is_trivial("---", TRIVIAL_HEADINGS))
        self.assertTrue(gate.is_trivial("   ", TRIVIAL_HEADINGS))


# ---------------------------------------------------------------------------
# Region extraction
# ---------------------------------------------------------------------------

SAMPLE_INSTALLED = """\
---
name: coder
---

Some preamble text.

<!-- HOS:CORE:START -->
You are the implementation agent. You write production-quality code.
Resolve paths at runtime from config.
<!-- HOS:CORE:END -->

<!-- HOS:PACK:django:START -->
Use select_for_update around booking creation.
Let the GiST exclusion constraint be the final arbiter of overlaps.
Passwords: argon2 via Argon2PasswordHasher. Never bcrypt.
<!-- HOS:PACK:django:END -->

<!-- HOS:PROJECT:START -->
This is the project-specific content for CondoParkShare.
<!-- HOS:PROJECT:END -->
"""


class TestExtractRegions(unittest.TestCase):
    def setUp(self):
        self.installed = gate.extract_regions(SAMPLE_INSTALLED)

    def test_extracts_core(self):
        self.assertIn("CORE", self.installed.regions)
        self.assertIn("implementation agent", self.installed.regions["CORE"])

    def test_extracts_pack_django(self):
        self.assertIn("PACK:django", self.installed.regions)
        self.assertIn("select_for_update", self.installed.regions["PACK:django"])

    def test_extracts_project(self):
        self.assertIn("PROJECT", self.installed.regions)
        self.assertIn("CondoParkShare", self.installed.regions["PROJECT"])

    def test_three_regions_total(self):
        self.assertEqual(len(self.installed.regions), 3)

    def test_no_markers_in_body(self):
        for body in self.installed.regions.values():
            self.assertNotIn("HOS:CORE:START", body)
            self.assertNotIn("HOS:PACK", body)


class TestExtractRegionsNoMarkers(unittest.TestCase):
    def test_flat_file_yields_empty(self):
        installed = gate.extract_regions("plain text with no markers")
        self.assertEqual(installed.regions, {})


# ---------------------------------------------------------------------------
# Front-matter parsing
# ---------------------------------------------------------------------------

SAMPLE_FM = """\
---
name: coder
description: The implementation agent.
model: claude-sonnet-4-6
tools:
  - Read
  - Write
  - Edit
  - Bash
---

Body content here.
"""


class TestFrontMatterParsing(unittest.TestCase):
    def test_parses_name(self):
        fm, body = gate.parse_front_matter(SAMPLE_FM)
        self.assertEqual(fm.name, "coder")

    def test_parses_model(self):
        fm, _ = gate.parse_front_matter(SAMPLE_FM)
        self.assertEqual(fm.model, "claude-sonnet-4-6")

    def test_parses_tools(self):
        fm, _ = gate.parse_front_matter(SAMPLE_FM)
        self.assertIn("Read", fm.tools)
        self.assertIn("Write", fm.tools)
        self.assertIn("Edit", fm.tools)
        self.assertIn("Bash", fm.tools)

    def test_parses_description(self):
        fm, _ = gate.parse_front_matter(SAMPLE_FM)
        self.assertEqual(fm.description, "The implementation agent.")

    def test_body_separated(self):
        _, body = gate.parse_front_matter(SAMPLE_FM)
        self.assertIn("Body content here.", body)
        self.assertNotIn("name: coder", body)

    def test_no_front_matter(self):
        fm, body = gate.parse_front_matter("No front matter here.\nJust content.")
        self.assertIsNone(fm.name)
        self.assertIn("No front matter here.", body)


# ---------------------------------------------------------------------------
# Front-matter diff
# ---------------------------------------------------------------------------


class TestFrontMatterDiff(unittest.TestCase):
    def _fm(self, name="coder", tools=None, model="claude-sonnet-4-6", desc="desc"):
        fm = gate.FrontMatter(raw="")
        fm.name = name
        fm.tools = list(tools or ["Read", "Write", "Bash"])
        fm.model = model
        fm.description = desc
        return fm

    def test_no_diff(self):
        before = self._fm()
        after = self._fm()
        d = gate.diff_front_matter(before, after)
        self.assertFalse(d.name_mismatch)
        self.assertEqual(d.removed_tools, [])
        self.assertIsNone(d.model_changed)

    def test_name_mismatch(self):
        before = self._fm(name="coder")
        after = self._fm(name="coder-v2")
        d = gate.diff_front_matter(before, after)
        self.assertTrue(d.name_mismatch)

    def test_removed_tool(self):
        before = self._fm(tools=["Read", "Write", "Grep"])
        after = self._fm(tools=["Read", "Write"])
        d = gate.diff_front_matter(before, after)
        self.assertIn("Grep", d.removed_tools)

    def test_added_tool(self):
        before = self._fm(tools=["Read"])
        after = self._fm(tools=["Read", "Glob"])
        d = gate.diff_front_matter(before, after)
        self.assertIn("Glob", d.added_tools)
        self.assertEqual(d.removed_tools, [])

    def test_model_change(self):
        before = self._fm(model="claude-sonnet-4-6")
        after = self._fm(model="claude-opus-5")
        d = gate.diff_front_matter(before, after)
        self.assertIsNotNone(d.model_changed)
        self.assertEqual(d.model_changed, ("claude-sonnet-4-6", "claude-opus-5"))

    def test_description_change(self):
        before = self._fm(desc="old description")
        after = self._fm(desc="new description")
        d = gate.diff_front_matter(before, after)
        self.assertIsNotNone(d.description_changed)


# ---------------------------------------------------------------------------
# Jaccard similarity
# ---------------------------------------------------------------------------


class TestJaccard(unittest.TestCase):
    def test_identical(self):
        a = frozenset(["a", "b", "c"])
        self.assertAlmostEqual(gate.jaccard(a, a), 1.0)

    def test_disjoint(self):
        a = frozenset(["a", "b"])
        b = frozenset(["c", "d"])
        self.assertAlmostEqual(gate.jaccard(a, b), 0.0)

    def test_partial(self):
        a = frozenset(["a", "b", "c"])
        b = frozenset(["b", "c", "d"])
        # intersection = {b, c} = 2; union = {a, b, c, d} = 4 → 0.5
        self.assertAlmostEqual(gate.jaccard(a, b), 0.5)

    def test_empty_both(self):
        self.assertAlmostEqual(gate.jaccard(frozenset(), frozenset()), 1.0)


# ---------------------------------------------------------------------------
# Stage A matching
# ---------------------------------------------------------------------------

INSTALLED_WITH_DJANGO = gate.InstalledRegions(
    regions={
        "CORE": "You are the implementation agent. Write production-quality code.",
        "PACK:django": (
            "Use select_for_update around booking creation. "
            "Let the GiST exclusion constraint be the final arbiter of overlaps. "
            "Passwords: argon2 via Argon2PasswordHasher. Never bcrypt unless the ADR specifies."
        ),
        "PROJECT": "CondoParkShare specific: use the Django app layout as specified.",
    }
)

DEFAULT_CONFIG = gate.GateConfig(
    jaccard=0.85,
    token_coverage=0.80,
    region_priority=["PROJECT", "PACK:django", "CORE"],
    trivial_headings=TRIVIAL_HEADINGS,
)


class TestStageA(unittest.TestCase):
    def test_exact_subset_covered(self):
        # Source line whose tokens are a subset of a sentence in the region
        result = gate.match_line(
            "Use select_for_update around booking.",
            INSTALLED_WITH_DJANGO,
            [],
            DEFAULT_CONFIG,
        )
        self.assertEqual(result.outcome, gate.COVERED)

    def test_high_jaccard_covered(self):
        # "Use select_for_update around booking creation operations." is a near-verbatim
        # restatement of a sentence in PACK:django — differs only by the extra word
        # "operations". Jaccard = 7/8 = 0.875, which is >= 0.85 threshold.
        result = gate.match_line(
            "Use select_for_update around booking creation operations.",
            INSTALLED_WITH_DJANGO,
            [],
            DEFAULT_CONFIG,
        )
        self.assertEqual(result.outcome, gate.COVERED)

    def test_unrelated_line_unaccounted(self):
        result = gate.match_line(
            "This line has absolutely nothing to do with anything installed.",
            INSTALLED_WITH_DJANGO,
            [],
            DEFAULT_CONFIG,
        )
        self.assertEqual(result.outcome, gate.UNACCOUNTED)

    def test_priority_project_over_pack(self):
        # A line present in PROJECT region should be attributed to PROJECT
        installed = gate.InstalledRegions(
            regions={
                "CORE": "generic core text",
                "PACK:django": "generic core text",  # same text in PACK too
                "PROJECT": "CondoParkShare specific layout and domain models here.",
            }
        )
        result = gate.match_line(
            "CondoParkShare specific layout and domain models here.",
            installed,
            [],
            DEFAULT_CONFIG,
        )
        self.assertEqual(result.outcome, gate.COVERED)
        self.assertEqual(result.region, "PROJECT")


# ---------------------------------------------------------------------------
# Stage B matching (reworded/paraphrased)
# ---------------------------------------------------------------------------


class TestStageB(unittest.TestCase):
    def test_reworded_line_covered(self):
        # Source: "Passwords must use argon2 hasher — never use bcrypt"
        # Target PACK:django contains "Passwords: argon2 via Argon2PasswordHasher. Never bcrypt..."
        # Content tokens in source: {passwords, must, use, argon2, hasher, never, use, bcrypt}
        # (after stopword removal: passwords, argon2, hasher, never, bcrypt)
        # Most of those appear in the PACK:django region text → Stage B COVERED-REWORDED.
        result = gate.match_line(
            "Passwords must use argon2 hasher and never use bcrypt",
            INSTALLED_WITH_DJANGO,
            [],
            DEFAULT_CONFIG,
        )
        self.assertIn(result.outcome, [gate.COVERED, gate.COVERED_REWORDED])
        self.assertNotEqual(result.outcome, gate.UNACCOUNTED)

    def test_paraphrase_with_low_overlap_unaccounted(self):
        # Completely different vocabulary → should be unaccounted
        result = gate.match_line(
            "The frobnicator must frob all widgets before dispatching to the zork layer.",
            INSTALLED_WITH_DJANGO,
            [],
            DEFAULT_CONFIG,
        )
        self.assertEqual(result.outcome, gate.UNACCOUNTED)


# ---------------------------------------------------------------------------
# Allowlist matching
# ---------------------------------------------------------------------------


class TestAllowlist(unittest.TestCase):
    def _make_entry(self, line_text: str) -> gate.AllowEntry:
        norm = gate.normalize_text(line_text)
        lh = gate.line_hash(norm)
        return gate.AllowEntry(
            line=line_text,
            line_hash=lh,
            covered_by="dropped",
            reason="intentionally removed in migration",
            approved_by="sthurlow",
            approved_at="2026-06-15",
        )

    def test_allowlisted_line_not_unaccounted(self):
        unique_line = "This very specific line about frozzle widgets only appears here."
        entry = self._make_entry(unique_line)
        installed = gate.InstalledRegions(regions={"CORE": "unrelated content"})
        result = gate.match_line(unique_line, installed, [entry], DEFAULT_CONFIG)
        self.assertEqual(result.outcome, gate.ALLOWLISTED)

    def test_non_allowlisted_unaccounted(self):
        unique_line = "Another unique line not in the allowlist or installed text."
        installed = gate.InstalledRegions(regions={"CORE": "unrelated content"})
        result = gate.match_line(unique_line, installed, [], DEFAULT_CONFIG)
        self.assertEqual(result.outcome, gate.UNACCOUNTED)

    def test_used_flag_set(self):
        line_text = "Allowlist test line XYZ."
        entry = self._make_entry(line_text)
        installed = gate.InstalledRegions(regions={"CORE": "unrelated"})
        gate.match_line(line_text, installed, [entry], DEFAULT_CONFIG)
        self.assertTrue(entry._used)  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Real agent smoke test (coder.md flat vs installed-with-markers)
# ---------------------------------------------------------------------------

CODER_FLAT_BODY = """\
You are the implementation agent for CondoParkShare. You write production-quality Django code that faithfully implements the technical design. You do not decide what to build — you build what the design specifies.

## Project context

**Stack:** Django (Python) + HTMX + PostgreSQL + Docker Compose + Caddy.

## Before writing code

1. Read `TECHNICAL-DESIGN.md` for the section you are implementing.
2. If anything is unclear or missing, ask technical-design **before** writing.
3. Do not start implementation until you have answers.

## While writing code

**Django conventions:**
- One app per major domain area (e.g. `accounts`, `parking`, `notifications`, `admin_portal`).
- Custom ORM managers must enforce `organization` scoping on every queryset.
- Use `select_for_update()` around booking creation; let the GiST exclusion constraint be the final arbiter of overlaps.
- Encrypted fields: use the library and approach specified in the ADR.
- Passwords: argon2 (Django's `Argon2PasswordHasher`). Never bcrypt unless ADR specifies otherwise.

**Security (non-negotiable):**
- Every view that touches tenant data: verify `request.user.organization == object.organization`. No exceptions.
- TOTP verification before any sensitive action; enforce at the view layer.

## After writing code

Submit to code-reviewer first. Once code-reviewer approves, security-reviewer and privacy-reviewer run in parallel.
"""

# This installed content is deliberately rich — it must cover all non-trivial
# flat-body lines above at >= 0.80 token-coverage (Stage B) or >= 0.85 Jaccard
# (Stage A). Thin installed content would defeat the point of the smoke test.
CODER_INSTALLED_WITH_REGIONS = """\
---
name: coder
---

<!-- HOS:CORE:START -->
You are the implementation agent. Write production-quality code that faithfully implements the technical design. You do not decide what to build — you build what the design specifies.

Read the technical design (and the ADR) for the section you are implementing. If anything is unclear or missing ask technical-design before writing. Batch all clarifying questions before writing, not one at a time mid-implementation. Do not start implementation until you have answers.

Django conventions: one app per major domain area such as accounts, parking, notifications, admin_portal. Custom ORM managers must enforce organization scoping on every queryset. Use select_for_update around booking creation; let the GiST exclusion constraint be the final arbiter of overlaps. Encrypted fields: use the library and approach specified in the ADR. Passwords: argon2 via Argon2PasswordHasher. Never bcrypt unless ADR specifies otherwise.

Security non-negotiable: every view that touches tenant data must verify user organization equals object organization. No exceptions. TOTP verification before any sensitive action; enforce at the view layer.

Submit to code-reviewer first. Once code-reviewer approves, security-reviewer and privacy-reviewer run in parallel. Do not mark any section complete until all three reviewers have approved.
<!-- HOS:CORE:END -->

<!-- HOS:PACK:django:START -->
Stack: Django plus HTMX plus PostgreSQL plus Docker Compose plus Caddy.
One Django app per major domain area. Encrypted fields use the library from the ADR. Never store PII in plaintext. Passwords: argon2 Argon2PasswordHasher, never bcrypt.
<!-- HOS:PACK:django:END -->

<!-- HOS:PROJECT:START -->
Primary inputs: TECHNICAL-DESIGN.md, ADR-001-pilot.md, SPEC-1-pilot.md, DESIGN.md and tokens.css.
<!-- HOS:PROJECT:END -->
"""


class TestRealCoderSmoke(unittest.TestCase):
    """
    Simulate running the gate against a flat coder.md BEFORE and a layered
    AFTER. All non-trivial lines must be accounted for.

    The installed regions above are deliberately written to cover the flat
    body's semantic content — this is the post-migration state. The test
    proves that a properly authored layered install passes the gate for this
    agent's pre-migration content.
    """

    def setUp(self):
        self.installed = gate.extract_regions(CODER_INSTALLED_WITH_REGIONS)
        self.config = DEFAULT_CONFIG

    def test_regions_extracted(self):
        self.assertIn("CORE", self.installed.regions)
        self.assertIn("PACK:django", self.installed.regions)
        self.assertIn("PROJECT", self.installed.regions)

    def test_domain_noun_lines_covered(self):
        lines_to_check = [
            "Custom ORM managers must enforce `organization` scoping on every queryset.",
            "Use `select_for_update()` around booking creation; let the GiST exclusion constraint be the final arbiter of overlaps.",
            "Passwords: argon2 (Django's `Argon2PasswordHasher`). Never bcrypt unless ADR specifies otherwise.",
            "Every view that touches tenant data: verify `request.user.organization == object.organization`. No exceptions.",
        ]
        for line in lines_to_check:
            with self.subTest(line=line[:60]):
                result = gate.match_line(line, self.installed, [], self.config)
                self.assertNotEqual(
                    result.outcome,
                    gate.UNACCOUNTED,
                    f"Line should be covered but was UNACCOUNTED: {line[:60]}",
                )

    def test_generic_headings_trivial(self):
        trivial_lines = [
            "## Project context",
            "## Before writing code",
            "## While writing code",
            "## After writing code",
        ]
        for line in trivial_lines:
            with self.subTest(line=line):
                result = gate.is_trivial(line, self.config.trivial_headings)
                self.assertTrue(result, f"Expected trivial: {line!r}")

    def test_security_subheading_not_trivial(self):
        # "## Security (non-negotiable):" normalizes to "security (non-negotiable):"
        # which is NOT in the trivial stop-set — it has a parenthetical qualifier.
        # Callers should include it in their trivial headings file if they want
        # it skipped; the base stop-set only has bare "security".
        line = "**Security (non-negotiable):**"
        # This is bold-wrapped, not a heading, so it's treated as content — non-trivial.
        result = gate.is_trivial(line, self.config.trivial_headings)
        self.assertFalse(result)

    def test_all_nontrivial_lines_accounted(self):
        unaccounted_lines = []
        for raw_line in CODER_FLAT_BODY.splitlines():
            if gate.is_trivial(raw_line, self.config.trivial_headings):
                continue
            result = gate.match_line(raw_line, self.installed, [], self.config)
            if result.outcome == gate.UNACCOUNTED:
                unaccounted_lines.append(raw_line)

        self.assertEqual(
            unaccounted_lines,
            [],
            f"These lines were UNACCOUNTED:\n"
            + "\n".join(f"  - {ln.strip()}" for ln in unaccounted_lines),
        )


# ---------------------------------------------------------------------------
# GateConfig and TOML parser
# ---------------------------------------------------------------------------


class TestTomlParser(unittest.TestCase):
    def test_parses_thresholds(self):
        toml_text = 'jaccard = 0.85\ntoken_coverage = 0.80\nregion_priority = ["PROJECT", "PACK:django", "CORE"]\n'
        data = gate._parse_toml_minimal(toml_text)
        self.assertAlmostEqual(data["jaccard"], 0.85)
        self.assertAlmostEqual(data["token_coverage"], 0.80)
        self.assertEqual(data["region_priority"], ["PROJECT", "PACK:django", "CORE"])

    def test_ignores_comments(self):
        toml_text = "# comment\njaccard = 0.90\n"
        data = gate._parse_toml_minimal(toml_text)
        self.assertAlmostEqual(data["jaccard"], 0.90)


# ---------------------------------------------------------------------------
# Allowlist minimal YAML parser
# ---------------------------------------------------------------------------


class TestAllowlistParser(unittest.TestCase):
    SAMPLE_YAML = """\
- line: "Use Grep for searching"
  covered_by: "dropped"
  reason: "Grep removed, use Bash instead"
  approved_by: "sthurlow"
  approved_at: "2026-06-15"

- line: Another dropped line
  covered_by: "CORE"
  reason: "Superseded by core wording"
  approved_by: "sthurlow"
  approved_at: "2026-06-15"
"""

    def test_parses_two_entries(self):
        entries = gate._parse_allowlist_minimal(self.SAMPLE_YAML)
        self.assertEqual(len(entries), 2)

    def test_first_entry_fields(self):
        entries = gate._parse_allowlist_minimal(self.SAMPLE_YAML)
        self.assertEqual(entries[0].line, "Use Grep for searching")
        self.assertEqual(entries[0].covered_by, "dropped")
        self.assertEqual(entries[0].approved_by, "sthurlow")

    def test_second_entry_covered_by(self):
        entries = gate._parse_allowlist_minimal(self.SAMPLE_YAML)
        self.assertEqual(entries[1].covered_by, "CORE")

    def test_hash_computed(self):
        entries = gate._parse_allowlist_minimal(self.SAMPLE_YAML)
        for e in entries:
            self.assertTrue(len(e.line_hash) > 0)


# ---------------------------------------------------------------------------
# Priority ordering
# ---------------------------------------------------------------------------


class TestPriorityOrder(unittest.TestCase):
    def test_project_first(self):
        ids = ["CORE", "PACK:django", "PROJECT"]
        ordered = gate._priority_order(ids, ["PROJECT", "PACK:django", "CORE"])
        self.assertEqual(ordered[0], "PROJECT")

    def test_pack_before_core(self):
        ids = ["CORE", "PACK:django"]
        ordered = gate._priority_order(ids, ["PROJECT", "PACK:django", "CORE"])
        self.assertEqual(ordered[0], "PACK:django")

    def test_unlisted_region_last(self):
        ids = ["CORE", "PACK:custom"]
        ordered = gate._priority_order(ids, ["PROJECT", "PACK:django", "CORE"])
        # PACK:custom not in priority list → goes last
        self.assertEqual(ordered[-1], "PACK:custom")


# ---------------------------------------------------------------------------
# Summary line format
# ---------------------------------------------------------------------------


class TestSummaryLine(unittest.TestCase):
    def test_pass_format(self):
        r = gate.AgentReport(agent="coder", passes=True)
        r.core_count = 5
        r.pack_count = 3
        r.project_count = 2
        r.reworded_count = 1
        r.allowlisted_count = 0
        r.unaccounted_count = 0
        r.trivial_count = 10
        line = gate.summary_line(r)
        self.assertIn("PASS", line)
        self.assertIn("coder", line)
        self.assertIn("CORE=5", line)
        self.assertIn("UNACCOUNTED=0", line)

    def test_fail_format(self):
        r = gate.AgentReport(agent="security-reviewer", passes=False)
        r.unaccounted_count = 3
        line = gate.summary_line(r)
        self.assertIn("FAIL", line)
        self.assertIn("UNACCOUNTED=3", line)


if __name__ == "__main__":
    unittest.main(verbosity=2)
