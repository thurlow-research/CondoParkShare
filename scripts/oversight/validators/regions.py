"""
regions.py — the byte-exact region mechanism for HOS layered agent files.

An agent `.md` is composed of marker-delimited regions:

    <!-- HOS:CORE:START -->      ... generic role ...      <!-- HOS:CORE:END -->
    <!-- HOS:PACK:<name>:START -->  ... stack rules ...    <!-- HOS:PACK:<name>:END -->
    <!-- HOS:PROJECT:START -->    ... consumer's rules ... <!-- HOS:PROJECT:END -->

Canonical order is CORE -> PACK:<name> (alphabetical) -> PROJECT (recency
precedence: the most-specific layer comes last so PROJECT overrides PACK
overrides CORE). `compose()` is the ONLY writer and always emits this order.

This module is standalone and deterministic: stdlib only (hashlib, re, sys,
argparse, dataclasses). It runs in the target's venv-less context exactly the
way the installer's `_sha256` does today, and mirrors `schema.py`'s "pure
functions + a thin CLI" shape.

Binding decisions honored (docs/specs/v0.3.0-base-agents-spec.md §11/§11a):
  D6 — regions.py NEVER substitutes and never reads config.sh /
       placeholders.manifest. It hashes the bytes it is given. The installer's
       perl pass is the only substitution engine.
  D7 — `validate --placeholder-keys <csv>` flags any `{KEY}` token (for a
       passed key) inside a CORE/PACK body -> E_PLACEHOLDER_IN_CORE_PACK.
       regions.py is token-set-agnostic; it is *told* the keys.
  D8 — out-of-region prose is HOS-canonical: compose() owns/regenerates all
       out-of-region prose; only the PROJECT body is verbatim-from-disk.
       validate() does not reject out-of-region prose.

Scope note: this file implements parse / validate / compose / region_sha + the
manifest-rows / validate / region-sha / compose CLI, the pure three-way
`merge_region` decider (TD §4), and — kept here as TESTABLE pure functions so
the bash installer stays a thin caller — `plan_upgrade` (the per-file Phase
A/B core, TD §4.5), `migrate_flat` / `migrate_flat_introduced_core` (the
flat-file migration writer, TD §5/D3), and `assemble_manifest` (the full
`.hos-manifest` writer, TD §1.1/D5.6). The installer-facing `plan` / `migrate`
CLI subcommands (the THIN bash wiring) are thin wrappers over `plan_upgrade` /
`migrate_flat`: they parse args, call the existing pure functions, and serialize
the result (JSON for `plan`, raw composed bytes for `migrate`). They add NO new
logic — all decisions live in the pure functions above.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from enum import Enum

# Manifest schema version this module reads/writes (TD §1.3).
CURRENT_SCHEMA = 2

# Exit codes used by the CLI (TD §2.7).
EXIT_OK = 0
EXIT_USAGE = 1
EXIT_INVALID = 2
EXIT_REGION_ABSENT = 3
# A blocked upgrade (any drift HARDSTOP, no --squash) — TD §4.3. Distinct from
# the usage/invalid codes so the installer can tell "drift, refuse the whole
# upgrade" apart from a bad invocation; `plan` returns this when blocked.
EXIT_DRIFT = 4


# --------------------------------------------------------------------------- #
# Marker grammar (TD §2.1)
# --------------------------------------------------------------------------- #

# Pack slug: lowercase, starts alnum, then alnum or hyphen.
_PACK_SLUG = r"[a-z0-9][a-z0-9-]*"

# The strict, anchored marker form — the ONLY accepted marker. Tolerates a run
# of whitespace where the canonical form uses single spaces, so a reflowed file
# still parses; compose() always re-emits the canonical single-space form.
_MARKER_RE = re.compile(
    r"^<!--\s+HOS:(?P<id>CORE|PACK:" + _PACK_SLUG + r"|PROJECT):(?P<edge>START|END)\s+-->$"
)

# Loose "looks marker-ish" probe — anything matching this but NOT _MARKER_RE is
# a malformed marker (typo'd / wrong case), caught fail-closed by validate()
# (E_MALFORMED_MARKER) rather than silently becoming body text.
_LOOSE_MARKER_RE = re.compile(r"^\s*<!--\s*HOS:", re.IGNORECASE)


def _canonical_start(region_id: str) -> bytes:
    return b"<!-- HOS:" + region_id.encode("utf-8") + b":START -->"


def _canonical_end(region_id: str) -> bytes:
    return b"<!-- HOS:" + region_id.encode("utf-8") + b":END -->"


# --------------------------------------------------------------------------- #
# Data model (TD §2.2)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Region:
    id: str  # "CORE" | "PACK:<name>" | "PROJECT"
    name: str | None  # pack name for PACK:*, else None
    body: bytes  # bytes strictly between markers, BEFORE newline normalization
    start_line: int  # 1-based line index of the START marker (for error messages)
    end_line: int  # 1-based line index of the END marker


@dataclass
class ParsedAgent:
    front_matter: bytes = b""  # YAML front-matter block incl. delimiters, or b""
    regions: list[Region] = field(default_factory=list)  # in file order as found
    raw: bytes = b""  # original file bytes (round-trip / migration)


class ParseError(Exception):
    """Structurally impossible read (END with empty stack, EOF inside a region)."""

    def __init__(self, line: int, kind: str, msg: str):
        self.line = line
        self.kind = kind
        self.msg = msg
        super().__init__(line, kind, msg)  # forward all args (pickle/copy safe — B042)

    def __str__(self) -> str:
        return f"{self.line}:{self.kind}:{self.msg}"


@dataclass
class Result:
    ok: bool
    errors: list[tuple[int, str, str]] = field(default_factory=list)  # (line, CODE, msg)


# --------------------------------------------------------------------------- #
# Marker classification
# --------------------------------------------------------------------------- #


def _classify(line: bytes) -> tuple[str, str] | None:
    """
    Return (region_id, edge) if `line` is a strict marker, else None.

    The line is decoded as UTF-8 (markers are ASCII) and trailing whitespace
    stripped before matching, per TD §2.1 ("a marker is a whole line after
    stripping trailing whitespace").
    """
    try:
        text = line.decode("utf-8")
    except UnicodeDecodeError:
        return None
    text = text.rstrip()
    m = _MARKER_RE.match(text)
    if not m:
        return None
    return m.group("id"), m.group("edge")


def _is_loose_marker(line: bytes) -> bool:
    try:
        text = line.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return bool(_LOOSE_MARKER_RE.match(text.rstrip()))


def _region_name(region_id: str) -> str | None:
    if region_id.startswith("PACK:"):
        return region_id.split(":", 1)[1]
    return None


def _normalize_body(body: bytes) -> bytes:
    """Normalize a region body's line endings (TD §2.6).

    The single definition of body normalization, shared by `region_sha` and
    `compose` so disk bytes and manifest sha never disagree. Steps, in order:
      1. CRLF -> LF, then bare CR -> LF (so an `autocrlf` checkout or a stray
         classic-Mac CR does not register as drift — the line-ending analogue
         of D1(c)),
      2. strip all trailing newlines,
      3. append exactly one trailing `\\n`.
    """
    return body.replace(b"\r\n", b"\n").replace(b"\r", b"\n").rstrip(b"\n") + b"\n"


def _split_lines_keepends(data: bytes) -> list[bytes]:
    """Split on '\n' preserving the terminator on every line but the last.

    Unlike bytes.splitlines() this does NOT split on other unicode line
    boundaries (e.g. \\x0b, \\x0c, U+2028) which would corrupt body bytes; it
    only treats LF as a line boundary, matching how the installer reads files.
    """
    return data.splitlines(keepends=True) if data else []


# --------------------------------------------------------------------------- #
# parse (TD §2.3)
# --------------------------------------------------------------------------- #

_FRONT_MATTER_RE = re.compile(rb"^---\n(?:.*?\n)??---\n", re.DOTALL)


def parse(text: bytes) -> ParsedAgent:
    """
    Parse `text` into a ParsedAgent. Tolerant by design — does NOT enforce the
    structural invariants (that is validate()'s job) so validate() can produce
    precise diagnostics. Raises ParseError only on a structurally impossible
    read: an END with an empty stack, or EOF inside an open region.
    """
    if not isinstance(text, (bytes, bytearray)):
        raise TypeError("parse() requires bytes")
    text = bytes(text)

    # Front-matter: a leading ---\n ... \n---\n block. Markers are searched only
    # after it (TD §2.3).
    front_matter = b""
    body_offset = 0
    fm = _FRONT_MATTER_RE.match(text)
    if fm:
        front_matter = fm.group(0)
        body_offset = fm.end()

    body_text = text[body_offset:]
    lines = _split_lines_keepends(body_text)

    # Line numbering is 1-based over the whole file (front-matter counts toward
    # line numbers so error messages point at the real file line).
    fm_line_count = front_matter.count(b"\n") if front_matter else 0

    regions: list[Region] = []
    # Stack of (region_id, name, start_line_1based, body_accumulator_start_index)
    open_stack: list[tuple[str, str | None, int, list[bytes]]] = []

    for idx, raw_line in enumerate(lines):
        line_no = fm_line_count + idx + 1
        classified = _classify(raw_line)
        if classified is not None:
            region_id, edge = classified
            if edge == "START":
                open_stack.append((region_id, _region_name(region_id), line_no, []))
            else:  # END
                if not open_stack:
                    raise ParseError(
                        line_no,
                        "END_WITHOUT_START",
                        f"END marker for {region_id} with no open region",
                    )
                open_id, open_name, start_line, acc = open_stack.pop()
                # Note: a mismatched id (END that does not match the innermost
                # open START) is left for validate() to diagnose precisely as
                # unbalanced; parse stays tolerant and pairs by stack position.
                body = b"".join(acc)
                regions.append(
                    Region(
                        id=open_id,
                        name=open_name,
                        body=body,
                        start_line=start_line,
                        end_line=line_no,
                    )
                )
        else:
            # Body line — append to the innermost open region's accumulator, if
            # any. Out-of-region lines (preamble / inter-region prose) are not
            # captured into any Region (D8 — out-of-region prose is HOS-canonical
            # and regenerated by compose; never hashed).
            if open_stack:
                open_stack[-1][3].append(raw_line)

    if open_stack:
        unterminated = open_stack[-1]
        raise ParseError(
            unterminated[2],
            "EOF_IN_REGION",
            f"end of file while region {unterminated[0]} still open",
        )

    # Preserve file order as found. Because regions are emitted on END (pop),
    # nested regions (which validate() rejects) would emit inner-first; for a
    # well-formed sibling file emission order equals file order. Sort by
    # start_line to guarantee file order regardless.
    regions.sort(key=lambda r: r.start_line)

    return ParsedAgent(front_matter=front_matter, regions=regions, raw=text)


# --------------------------------------------------------------------------- #
# validate (TD §2.4, D7)
# --------------------------------------------------------------------------- #


def validate(parsed: ParsedAgent, placeholder_keys: list[str] | None = None) -> Result:
    """
    Fail-closed structural validation (TD §2.4). Every violation is an error,
    not a warning. Does NOT enforce canonical *order* — compose() reorders on
    write — only structural integrity.

    If `placeholder_keys` is given (D7), additionally flag any `{KEY}` token for
    a passed key appearing inside a CORE or PACK body
    (E_PLACEHOLDER_IN_CORE_PACK). regions.py stays token-set-agnostic: it is
    *told* the keys, never reads config.sh / placeholders.manifest (D6).
    """
    errors: list[tuple[int, str, str]] = []

    # Re-scan the raw bytes for marker integrity so we can diagnose the exact
    # lines independent of parse()'s tolerant pairing. We work on the post-
    # front-matter body to mirror parse()'s view.
    fm = _FRONT_MATTER_RE.match(parsed.raw)
    body_offset = fm.end() if fm else 0
    fm_line_count = parsed.raw[:body_offset].count(b"\n") if fm else 0
    lines = _split_lines_keepends(parsed.raw[body_offset:])

    # --- Invariant 6: marker well-formedness (loose-but-not-strict) ---------- #
    for idx, raw_line in enumerate(lines):
        line_no = fm_line_count + idx + 1
        if _classify(raw_line) is None and _is_loose_marker(raw_line):
            errors.append(
                (
                    line_no,
                    "E_MALFORMED_MARKER",
                    "line looks like an HOS marker but does not match the "
                    "strict grammar (check case, ':START'/':END', spacing)",
                )
            )

    # --- Invariants 3 & 4: balanced markers + no nesting -------------------- #
    stack: list[tuple[str, int]] = []
    for idx, raw_line in enumerate(lines):
        line_no = fm_line_count + idx + 1
        classified = _classify(raw_line)
        if classified is None:
            continue
        region_id, edge = classified
        # Invariant 7 (B1): a strict marker line that falls *inside another
        # region's span* is a literal marker in that region's body. parse() stays
        # tolerant and pairs such a line by stack position (so it never lands in a
        # `Region.body`), which is why this is detected on the raw scan rather
        # than by walking `parsed.regions[].body`. This is the DIRECT diagnostic
        # naming the cause; it may co-occur with the E_NESTED / E_UNBALANCED
        # symptom the same line also produces.
        #   - a START while a region is already open is enclosed by that region;
        #   - an END is enclosed iff an OUTER region remains open after it closes
        #     the innermost (depth >= 2). The END that closes the sole open region
        #     (depth 1) is the legitimate close, not a body line.
        in_body = bool(stack) if edge == "START" else len(stack) >= 2
        if in_body:
            errors.append(
                (
                    line_no,
                    "E_LITERAL_MARKER_IN_BODY",
                    "region bodies may not contain a literal marker line; "
                    "escape or inline it (render it inside backticks, or "
                    "break the column-0 `<!-- HOS:...-->` form)",
                )
            )
        if edge == "START":
            if stack:
                # A START while a region is already open -> nesting (forbidden).
                errors.append(
                    (
                        line_no,
                        "E_NESTED",
                        f"region {region_id} opened inside still-open "
                        f"region {stack[-1][0]} (regions must be siblings)",
                    )
                )
            stack.append((region_id, line_no))
        else:  # END
            if not stack:
                # Unreachable via the CLI (parse() raises END_WITHOUT_START on an
                # empty-stack END before validate runs); retained for direct
                # validate() callers that build a ParsedAgent without parse().
                errors.append(
                    (line_no, "E_UNBALANCED", f"END marker for {region_id} with no open START")
                )
            else:
                open_id, _ = stack.pop()
                if open_id != region_id:
                    errors.append(
                        (
                            line_no,
                            "E_UNBALANCED",
                            f"END marker for {region_id} does not match the "
                            f"open region {open_id}",
                        )
                    )
    for open_id, start_line in stack:
        errors.append((start_line, "E_UNBALANCED", f"region {open_id} opened but never closed"))

    # --- Invariants 1, 2, 5: counts + uniqueness --------------------------- #
    core_count = sum(1 for r in parsed.regions if r.id == "CORE")
    project_count = sum(1 for r in parsed.regions if r.id == "PROJECT")

    if core_count == 0:
        errors.append((0, "E_NO_CORE", "no CORE region (exactly one required)"))
    elif core_count > 1:
        dup = [r for r in parsed.regions if r.id == "CORE"][1]
        errors.append(
            (dup.start_line, "E_DUP_CORE", f"{core_count} CORE regions (exactly one required)")
        )

    if project_count > 1:
        dup = [r for r in parsed.regions if r.id == "PROJECT"][1]
        errors.append(
            (
                dup.start_line,
                "E_DUP_PROJECT",
                f"{project_count} PROJECT regions (at most one allowed)",
            )
        )

    seen_packs: dict[str, int] = {}
    for r in parsed.regions:
        if r.id.startswith("PACK:") and r.name is not None:
            if r.name in seen_packs:
                errors.append(
                    (
                        r.start_line,
                        "E_DUP_PACK",
                        f"duplicate PACK region '{r.name}' (also at line " f"{seen_packs[r.name]})",
                    )
                )
            else:
                seen_packs[r.name] = r.start_line

    # --- D7: placeholder-free CORE/PACK ------------------------------------ #
    if placeholder_keys:
        keys = [k for k in placeholder_keys if k]
        for r in parsed.regions:
            if r.id == "CORE" or r.id.startswith("PACK:"):
                try:
                    body_text = r.body.decode("utf-8", errors="replace")
                except Exception:  # pragma: no cover - decode with replace can't raise
                    continue
                for key in keys:
                    if "{" + key + "}" in body_text:
                        errors.append(
                            (
                                r.start_line,
                                "E_PLACEHOLDER_IN_CORE_PACK",
                                f"placeholder '{{{key}}}' found in {r.id} region — "
                                f"CORE/PACK must be placeholder-free (D1a/D7); "
                                f"move it to PROJECT or use runtime self-direction",
                            )
                        )

    errors.sort(key=lambda e: (e[0], e[1]))
    return Result(ok=not errors, errors=errors)


# --------------------------------------------------------------------------- #
# region_sha (TD §2.6)
# --------------------------------------------------------------------------- #


def region_sha(region_body: bytes) -> str:
    """
    sha256 over the region body with ALL line endings normalized to LF and the
    trailing newline normalized to exactly one '\\n' (TD §2.6), via the shared
    `_normalize_body` helper. This is the SAME normalization compose() writes
    between the markers, so disk and manifest never disagree — a cross-platform
    (`autocrlf`) checkout does not register as drift.

    Lowercase hex. Used for every region row in the manifest and every
    three-way comparison. (Whole-file rows keep the installer's `_sha256` over
    raw file bytes — do NOT route whole-file through region_sha.)
    """
    return hashlib.sha256(_normalize_body(region_body)).hexdigest()


# --------------------------------------------------------------------------- #
# merge_region — the three-way decision (TD §4, spec §5, ADR D2/D9)
# --------------------------------------------------------------------------- #


class Action(str, Enum):
    """The per-region merge decision (TD §4.1).

    A `str`-backed enum so each member doubles as its own action token: the
    installer-facing CLI prints `action.value` (== the bare name) on stdout, and
    callers can compare against either the member or the string. The set is
    frozen by TD §4 / spec D9:

      REFRESH       write the incoming template body; re-stamp base_sha=incoming
      KEEP          no write; re-stamp base_sha=incoming (no-op / convergent edit)
      HARDSTOP      drifted HOS-owned region — refuse the whole upgrade (4.3)
      SKIP_PROJECT  PROJECT is never compared/written (4.4)
      DROP          region retired by HOS — remove region + manifest row (D9)
    """

    REFRESH = "REFRESH"
    KEEP = "KEEP"
    HARDSTOP = "HARDSTOP"
    SKIP_PROJECT = "SKIP_PROJECT"
    DROP = "DROP"

    def __str__(self) -> str:  # so f"{action}" prints the bare token, not "Action.KEEP"
        return self.value


def merge_region(
    region_id: str,
    base_sha: str | None,
    disk_sha: str,
    incoming: str,
    *,
    squash: bool = False,
    removed: bool = False,
) -> Action:
    """Decide what to do with one region on an upgrade — the pure three-way
    decider (TD §4.2, spec §5, ADR D2/D9).

    Pure: no file reads, no writes, never touches PROJECT bytes. Given the three
    shas it returns an `Action`; the installer's Phase A collects these and
    Phase B acts on them (TD §4.5).

    Args:
        region_id:  the region id ("CORE" | "PACK:<name>" | "PROJECT").
        base_sha:   sha HOS last wrote for this region, or None when the region
                    is on disk but absent from the manifest (freshly-introduced
                    region, or a legacy v1 manifest). None is treated as
                    base != disk — unknown provenance ⇒ assume edited ⇒
                    conservative (TD §4.2 note), so it routes through row 3/4
                    (or 5/6 when removed) rather than silently refreshing.
        disk_sha:   sha of the region currently on disk. For the removed-region
                    sweep (`removed=True`) this is still the on-disk region sha
                    (the region is present on disk, retired by the template).
        incoming:   sha of the region HOS would write this upgrade. Ignored when
                    `removed=True` (the template no longer carries the region).
        squash:     opt-in explicit consent (TD §4.3). Converts HARDSTOP→REFRESH
                    for template-side drift (row 4) and HARDSTOP→DROP for an
                    edited removed region (row 6). Never affects PROJECT.
        removed:    True for the manifest-side sweep (rows 5/6) — the region is
                    in the manifest but ABSENT from the new template (HOS retired
                    it). Drives the DROP/HARDSTOP rows instead of the template
                    table.

    Returns:
        An Action. PROJECT short-circuits to SKIP_PROJECT before any comparison.
    """
    # PROJECT is never compared or written — short-circuit before the table
    # (TD §4.4). Defensive `startswith` mirrors the spec's pseudo-code; the
    # canonical id is exactly "PROJECT".
    if region_id == "PROJECT" or region_id.startswith("PROJECT"):
        return Action.SKIP_PROJECT

    # base_sha is None ⇒ treat as base != disk (assume edited / unknown
    # provenance) — TD §4.2 note. A real equality only holds when base is a sha.
    base_eq_disk = base_sha is not None and base_sha == disk_sha

    # Rows 5–6: removed-region sweep (D9). The template no longer carries the
    # region, so `incoming` is irrelevant; only base-vs-disk decides.
    if removed:
        if base_eq_disk:
            return Action.DROP  # row 5: unedited → DROP
        # row 6: edited → HARDSTOP unless --squash/--prune consents to drop.
        return Action.DROP if squash else Action.HARDSTOP

    # Rows 1–4: template-side three-way.
    disk_eq_incoming = disk_sha == incoming
    if base_eq_disk:
        # Unedited by the consumer.
        return Action.KEEP if disk_eq_incoming else Action.REFRESH  # rows 1, 2
    # Consumer-edited (or unknown provenance).
    if disk_eq_incoming:
        return Action.KEEP  # row 3: convergent edit — realign
    # row 4: genuine drift → HARDSTOP unless --squash takes HOS's version.
    return Action.REFRESH if squash else Action.HARDSTOP


# --------------------------------------------------------------------------- #
# compose (TD §2.5) — the ONLY writer
# --------------------------------------------------------------------------- #


def _canonical_order_key(region: Region) -> tuple:
    """Sort key implementing CORE -> PACK(alpha) -> PROJECT."""
    if region.id == "CORE":
        bucket = 0
        name = ""
    elif region.id.startswith("PACK:"):
        bucket = 1
        name = region.name or ""
    elif region.id == "PROJECT":
        bucket = 2
        name = ""
    else:  # pragma: no cover - parse never produces other ids
        bucket = 3
        name = region.id
    return (bucket, name)


def compose(parsed_or_regions: ParsedAgent | list[Region]) -> bytes:
    """
    Rebuild the canonical file (TD §2.5). The ONLY writer.

    Order: front-matter -> CORE -> each PACK:<name> in alphabetical name order
    -> PROJECT last. PROJECT is NOT synthesized if absent (callers that need an
    empty stub create it explicitly — installer §7.1).

    Each region is emitted as:
        <canonical START marker>\\n
        <body, line endings normalized to LF + exactly one trailing \\n>
        <canonical END marker>\\n
    with a single blank line separating regions. The body emitted is exactly
    `_normalize_body(body)` (LF-only, single trailing newline), the same bytes
    region_sha hashes — so region_sha(parse(compose(x))) == region_sha(x) for
    well-formed x, and compose writes LF-only bodies so D1 holds at write time.

    Out-of-region prose is NOT reproduced here (D8 — compose owns canonical
    output; only region bodies travel). Front-matter is reattached verbatim.
    """
    if isinstance(parsed_or_regions, ParsedAgent):
        front_matter = parsed_or_regions.front_matter
        regions = list(parsed_or_regions.regions)
    else:
        front_matter = b""
        regions = list(parsed_or_regions)

    ordered = sorted(regions, key=_canonical_order_key)

    chunks: list[bytes] = []
    if front_matter:
        chunks.append(front_matter)

    blocks: list[bytes] = []
    for r in ordered:
        normalized_body = _normalize_body(r.body)
        block = _canonical_start(r.id) + b"\n" + normalized_body + _canonical_end(r.id) + b"\n"
        blocks.append(block)

    # A single blank line separates region blocks.
    chunks.append(b"\n".join(blocks))

    return b"".join(chunks)


def make_empty_project_region(start_line: int = 0) -> Region:
    """Synthesize an empty PROJECT region (installer §7.1 empty-stub seeding).

    compose() never creates this implicitly; the installer calls this to seed a
    consumer's marked place to add content on a first install.
    """
    return Region(id="PROJECT", name=None, body=b"", start_line=start_line, end_line=start_line)


# --------------------------------------------------------------------------- #
# manifest-rows helper (TD §1.4 / §2.7)
# --------------------------------------------------------------------------- #


def manifest_rows(path: str, parsed: ParsedAgent) -> list[str]:
    """
    Produce `path\\tregion\\tsha` rows for every region, in canonical order
    (CORE -> PACK(alpha) -> PROJECT). A flat (marker-less) file yields a single
    CORE row (implicit-CORE rule) — the caller is responsible for the
    provenance gate that decides flat-file CORE vs PROJECT at migration time
    (§5); at enumeration of HOS source every file is HOS-owned so CORE is
    correct.
    """
    if not parsed.regions:
        # Implicit single CORE over the whole (marker-less) body.
        sha = region_sha(
            parsed.raw[len(parsed.front_matter) :] if parsed.front_matter else parsed.raw
        )
        return [f"{path}\tCORE\t{sha}"]

    ordered = sorted(parsed.regions, key=_canonical_order_key)
    rows = []
    for r in ordered:
        rows.append(f"{path}\t{r.id}\t{region_sha(r.body)}")
    return rows


# --------------------------------------------------------------------------- #
# plan_upgrade — the pure Phase-A decider + Phase-B composition (TD §4.5)
# --------------------------------------------------------------------------- #


@dataclass
class Plan:
    """The result of planning an upgrade for ONE agent file (TD §4.5).

    Pure data: `plan_upgrade` computes it without any file I/O. The bash
    installer is a thin caller that acts on these fields (Phase B writes).

    Fields:
        actions:           the per-region decision, in canonical order
                           (CORE -> PACK(alpha) -> PROJECT). PROJECT is recorded
                           as SKIP_PROJECT for completeness. DROP entries come
                           from the manifest-side removed sweep (D9).
        hardstops:         (region_id, reason) for every region that resolved to
                           HARDSTOP — the precise per-region drift report (§4.3).
                           Empty unless `blocked`.
        blocked:           True iff any HARDSTOP fired and `squash` was not given.
                           When blocked, `new_bytes` is None and NOTHING is
                           composed — the installer refuses the whole upgrade
                           (§4.3) and writes no file, no manifest, no release.
        new_bytes:         the composed canonical file bytes to write in Phase B,
                           or None when `blocked`. REFRESH->template body,
                           KEEP->disk body, DROP->omitted, PROJECT->disk body
                           verbatim (or an empty stub on first_install). The
                           PROJECT body in `new_bytes` is `region_sha`-identical
                           to disk — i.e. identical up to `_normalize_body` (LF +
                           single trailing newline), which is how this module
                           defines region identity; it is the never-written
                           invariant (§4.4 — assert via `region_sha`, not raw
                           bytes). NOTE: front-matter on the composed file is
                           taken from the TEMPLATE (HOS-canonical, D8); a
                           consumer's hand-edited front-matter field is not
                           preserved on upgrade.
        new_manifest_rows: the manifest rows for the composed file, in canonical
                           order, with each HOS-owned region's sha re-stamped to
                           `incoming` for REFRESH/KEEP (base_sha = incoming, the
                           realign) and the PROJECT row recorded informationally.
                           DROP regions contribute no row. None when `blocked`.
    """

    actions: list[tuple[str, Action]] = field(default_factory=list)
    hardstops: list[tuple[str, str]] = field(default_factory=list)
    blocked: bool = False
    new_bytes: bytes | None = None
    new_manifest_rows: list[str] | None = None


def plan_upgrade(
    disk_bytes: bytes,
    template_bytes: bytes,
    base_shas: dict[str, str],
    *,
    squash: bool = False,
    first_install: bool = False,
) -> Plan:
    """Plan a single agent file's upgrade — the pure Phase-A/Phase-B core (§4.5).

    NO file I/O, NO substitution (D6 — `template_bytes` is ALREADY substituted by
    the caller). Given:
      disk_bytes:    the agent bytes currently on disk in the target.
      template_bytes: the HOS new-template bytes the caller has already
                      substituted (D6 — this function never substitutes).
      base_shas:     {region_id: base_sha} from the prior manifest for THIS file
                      (the sha HOS last wrote per region). A region absent from
                      this dict has base_sha=None ⇒ merge_region treats it as
                      base != disk (assume edited / unknown provenance, §4.2).

    Returns a `Plan`. Two-phase by construction (decide-all-then-act, §4.5): all
    `merge_region` decisions are collected first; only if none HARDSTOP (or
    `squash` consents) does it compose `new_bytes`. A single HARDSTOP without
    `squash` blocks the WHOLE file — `blocked=True`, `new_bytes=None`, drift
    report in `hardstops` (§4.3: refuse the whole upgrade, change nothing).

    PROJECT short-circuits to SKIP_PROJECT and its disk body is carried verbatim
    into `new_bytes` (or an empty stub when `first_install`) — never compared,
    never written from the template (§4.4, the never-written invariant).
    """
    parsed_disk = parse(disk_bytes)
    parsed_tmpl = parse(template_bytes)

    disk_by_id = {r.id: r for r in parsed_disk.regions}
    tmpl_by_id = {r.id: r for r in parsed_tmpl.regions}

    actions: list[tuple[str, Action]] = []
    hardstops: list[tuple[str, str]] = []
    # The region bodies to emit, keyed by region id, for KEEP/REFRESH/PROJECT.
    # DROP ids are simply never added here, so compose() omits them.
    emit_body: dict[str, bytes] = {}
    # The sha to record in the manifest for each emitted HOS-owned region.
    emit_sha: dict[str, str] = {}

    # --- Template-side loop (rows 1-4 + PROJECT short-circuit) -------------- #
    # Walk the template's regions in canonical order so `actions` is ordered.
    for r in sorted(parsed_tmpl.regions, key=_canonical_order_key):
        region_id = r.id

        if region_id == "PROJECT" or region_id.startswith("PROJECT"):
            # PROJECT is never compared/written. Its body comes from disk
            # verbatim (or an empty stub on first install) — never the template.
            actions.append((region_id, Action.SKIP_PROJECT))
            continue

        incoming = region_sha(r.body)
        disk_region = disk_by_id.get(region_id)
        disk_sha = region_sha(disk_region.body) if disk_region is not None else None
        base_sha = base_shas.get(region_id)

        if disk_sha is None:
            # The region is in the template but absent on disk — a freshly
            # introduced HOS region. It cannot be "drifted" (nothing to drift
            # from); HOS writes its body. base/disk are not comparable, so this
            # is a straight REFRESH (write the incoming body, stamp incoming).
            action = Action.REFRESH
        else:
            action = merge_region(region_id, base_sha, disk_sha, incoming, squash=squash)

        actions.append((region_id, action))

        if action == Action.HARDSTOP:
            hardstops.append((region_id, _drift_reason(region_id, base_sha, disk_sha)))
        elif action == Action.REFRESH:
            emit_body[region_id] = r.body
            emit_sha[region_id] = incoming
        elif action == Action.KEEP:
            # Carry the disk body (it already matches incoming up to
            # normalization); re-stamp base_sha = incoming (the realign, §4.2).
            emit_body[region_id] = disk_region.body if disk_region is not None else r.body
            emit_sha[region_id] = incoming

    # --- Removed-region sweep (rows 5-6, D9) — manifest-side, not template -- #
    # A region in base_shas (HOS wrote it before) but ABSENT from the new
    # template was retired by HOS. `incoming` is irrelevant in this sweep.
    for region_id, base_sha in base_shas.items():
        if region_id == "PROJECT" or region_id.startswith("PROJECT"):
            continue  # PROJECT is never in the sweep (HOS never authored it).
        if region_id in tmpl_by_id:
            continue  # still shipped — handled by the template-side loop.
        disk_region = disk_by_id.get(region_id)
        if disk_region is None:
            # Already gone from disk too — nothing to drop, no row to emit.
            continue
        disk_sha = region_sha(disk_region.body)
        action = merge_region(region_id, base_sha, disk_sha, disk_sha, squash=squash, removed=True)
        actions.append((region_id, action))
        if action == Action.HARDSTOP:
            hardstops.append((region_id, _drop_reason(region_id, base_sha, disk_sha)))
        # DROP: contribute neither an emit_body nor a manifest row (the region
        # and its row are both removed — §4.5 Phase B).

    # Re-sort actions into canonical order so DROP sweep entries interleave
    # correctly with the template-side entries (stable, deterministic output).
    actions.sort(key=lambda a: _action_order_key(a[0]))

    # --- Block decision (§4.3: any HARDSTOP without squash → refuse whole) -- #
    if hardstops:
        return Plan(actions=actions, hardstops=hardstops, blocked=True)

    # --- PROJECT body: verbatim from disk, or empty stub on first install -- #
    project_disk = disk_by_id.get("PROJECT")
    if project_disk is not None:
        emit_body["PROJECT"] = project_disk.body
    elif first_install:
        emit_body["PROJECT"] = b""  # empty stub (§7.1)
    # else: no PROJECT on disk and not first install → no PROJECT region emitted.

    # --- Phase B composition: build the canonical file ---------------------- #
    out_regions: list[Region] = []
    for region_id, body in emit_body.items():
        out_regions.append(
            Region(id=region_id, name=_region_name(region_id), body=body, start_line=0, end_line=0)
        )
    # Front-matter is HOS-canonical and comes from the template (D8); compose()
    # reattaches it verbatim.
    composed = ParsedAgent(
        front_matter=parsed_tmpl.front_matter, regions=out_regions, raw=template_bytes
    )
    new_bytes = compose(composed)

    # --- Manifest rows for the composed file -------------------------------- #
    rows = _plan_manifest_rows(out_regions, emit_sha)

    return Plan(
        actions=actions,
        hardstops=[],
        blocked=False,
        new_bytes=new_bytes,
        new_manifest_rows=rows,
    )


def _action_order_key(region_id: str) -> tuple:
    """Canonical sort key for an action's region id (CORE -> PACK -> PROJECT)."""
    if region_id == "CORE":
        return (0, "")
    if region_id.startswith("PACK:"):
        return (1, region_id.split(":", 1)[1])
    if region_id == "PROJECT" or region_id.startswith("PROJECT"):
        return (2, "")
    return (3, region_id)  # pragma: no cover - parse never produces other ids


def _drift_reason(region_id: str, base_sha: str | None, disk_sha: str | None) -> str:
    """Per-region drift report line for a template-side HARDSTOP (§4.3 row 4)."""
    base_repr = base_sha if base_sha is not None else "(absent)"
    disk_repr = disk_sha if disk_sha is not None else "(absent)"
    return (
        f"{region_id} drifted (base_sha={base_repr} disk_sha={disk_repr}): "
        f"re-run with --squash to take HOS's complete version (your edit is "
        f"recoverable in the git diff), or move your edit into the PROJECT region "
        f"of this file, then re-run"
    )


def _drop_reason(region_id: str, base_sha: str | None, disk_sha: str) -> str:
    """Per-region report for a removed+edited HARDSTOP (§4.3 row 6)."""
    base_repr = base_sha if base_sha is not None else "(absent)"
    return (
        f"{region_id} retired by HOS but edited locally "
        f"(base_sha={base_repr} disk_sha={disk_sha}): re-run with --squash/--prune "
        f"to drop it (your edit is recoverable in the git diff), or move your edit "
        f"into the PROJECT region of this file, then re-run"
    )


def _plan_manifest_rows(out_regions: list[Region], emit_sha: dict[str, str]) -> list[str]:
    """Manifest rows for a composed Plan, path-less (the caller prepends path).

    Rows are `<region>\\t<sha>` in canonical order. HOS-owned regions use the
    re-stamped `incoming` sha (base_sha = incoming); PROJECT uses its on-disk
    body sha (informational only, never compared). DROP regions are absent from
    `out_regions`, so they contribute no row.
    """
    rows: list[str] = []
    for r in sorted(out_regions, key=_canonical_order_key):
        sha = emit_sha.get(r.id)
        if sha is None:
            sha = region_sha(r.body)  # PROJECT (informational) or KEEP fallback.
        rows.append(f"{r.id}\t{sha}")
    return rows


# --------------------------------------------------------------------------- #
# migrate_flat — flat-file provenance migration (TD §5, ADR D3)
# --------------------------------------------------------------------------- #


def migrate_flat(disk_bytes: bytes, *, hos_ships_agent: bool) -> bytes:
    """Wrap a flat (marker-less) agent file into a single region (TD §5.2, D3).

    Pure — no I/O. The provenance gate (D3, the load-bearing rule):
      hos_ships_agent=True  → HOS ships a same-named agent ⇒ wrap the whole body
                              as a single CORE region (legible to future
                              upgrades; the three-way merge can refresh it).
      hos_ships_agent=False → unknown provenance (a consumer's own agent) ⇒ wrap
                              the whole body as a single PROJECT region (sacred —
                              --squash can never destroy it).

    Front-matter (a leading `---\\n ... \\n---\\n` block) is preserved verbatim
    and reattached by compose(); only the post-front-matter body is wrapped.
    The result passes validate() (compose emits canonical markers).

    Idempotency note: the CALLER must only invoke this on a genuinely flat file
    (parse(...).regions == []). A file that already has markers is not flat and
    must take the §4 three-way path, not this migration (TD §5.4).
    """
    parsed = parse(disk_bytes)
    body = parsed.raw[len(parsed.front_matter) :] if parsed.front_matter else parsed.raw
    region_id = "CORE" if hos_ships_agent else "PROJECT"
    wrapped = ParsedAgent(
        front_matter=parsed.front_matter,
        regions=[Region(id=region_id, name=None, body=body, start_line=0, end_line=0)],
        raw=disk_bytes,
    )
    return compose(wrapped)


def migrate_flat_introduced_core(disk_bytes: bytes, core_template_bytes: bytes) -> bytes:
    """The newly-introduced-CORE-over-existing-flat case (TD §5.3, D3).

    When THIS release introduces an HOS CORE for a name that already exists as a
    flat consumer file: take the existing flat body → wrap as PROJECT (consumer
    keeps it verbatim), then PREPEND the fresh HOS CORE from the template. NEVER
    a lossy merge — the two bodies are layered, not combined (recency precedence
    means the consumer's PROJECT body still governs on conflict).

    `core_template_bytes` is the HOS template carrying the new CORE (already
    substituted by the caller, D6). Its CORE body is extracted and prepended; its
    front-matter becomes the file's front-matter (HOS-canonical, D8).
    """
    parsed_disk = parse(disk_bytes)
    consumer_body = (
        parsed_disk.raw[len(parsed_disk.front_matter) :]
        if parsed_disk.front_matter
        else parsed_disk.raw
    )

    parsed_tmpl = parse(core_template_bytes)
    core_regions = [r for r in parsed_tmpl.regions if r.id == "CORE"]
    if not core_regions:
        raise ValueError("core_template_bytes has no CORE region to introduce")
    core_body = core_regions[0].body

    wrapped = ParsedAgent(
        front_matter=parsed_tmpl.front_matter,
        regions=[
            Region(id="CORE", name=None, body=core_body, start_line=0, end_line=0),
            Region(id="PROJECT", name=None, body=consumer_body, start_line=0, end_line=0),
        ],
        raw=disk_bytes,
    )
    return compose(wrapped)


# --------------------------------------------------------------------------- #
# assemble_manifest — the full .hos-manifest writer (TD §1.1, ADR D5.6)
# --------------------------------------------------------------------------- #


def parse_manifest_line(line: str) -> tuple[str, str, str] | None:
    """Parse one `.hos-manifest` line into `(path, region, sha)` (TD §1.3).

    Pure. Returns None for blank lines and `#` comments (incl. the schema-version
    marker). A 3-column row is v2 (`<path>\\t<region>\\t<sha>`); a 2-column row is
    the legacy v1 format and is interpreted as `region = WHOLE` (back-compat read,
    D5.6). Anything else raises ValueError ("malformed manifest row").
    """
    stripped = line.strip("\n")
    if not stripped.strip() or stripped.lstrip().startswith("#"):
        return None
    fields = stripped.split("\t")
    if len(fields) == 3:
        return fields[0], fields[1], fields[2]
    if len(fields) == 2:
        return fields[0], "WHOLE", fields[1]
    raise ValueError(f"malformed manifest row: {line!r}")


def base_shas_for_path(manifest_text: str, path: str) -> dict[str, str]:
    """Extract `{region_id: base_sha}` for ONE file's rows from a manifest (§4.5).

    The per-file base-shas `plan_upgrade` needs. WHOLE rows (non-agent / legacy
    v1) are skipped — they have no region the three-way merge consults; a v1
    agent row therefore yields an empty dict, so its CORE plans with base_sha=None
    (unknown provenance ⇒ conservative, §4.2), which is the correct legacy
    behavior. Pure — the caller reads the manifest file.
    """
    out: dict[str, str] = {}
    for line in manifest_text.splitlines():
        parsed = parse_manifest_line(line)
        if parsed is None:
            continue
        row_path, region, sha = parsed
        if row_path == path and region != "WHOLE":
            out[region] = sha
    return out


def assemble_manifest(rows_by_file: dict[str, list[str]]) -> str:
    """Produce the full `.hos-manifest` text (TD §1.1, ADR D5.6).

    `rows_by_file` maps `path -> list of "<region>\\t<sha>" rows` (the path-less
    rows `manifest_rows`/`_plan_manifest_rows` produce, OR full
    `<path>\\t<region>\\t<sha>` rows — both are accepted; a row already carrying
    its path is used as-is). The output is:

        # hos-manifest-schema: 2
        <path>\\t<region>\\t<sha>          (LC_ALL=C-sorted body)
        ...

    The schema-version comment is written FIRST and is exempt from the sort (TD
    §1.1); the body rows are sorted deterministically (codepoint order == the
    `LC_ALL=C sort` the installer uses) for stable diffs. The text ends with a
    trailing newline.
    """
    body_rows: list[str] = []
    for path, rows in rows_by_file.items():
        for row in rows:
            # Accept both path-less ("<region>\t<sha>") and full
            # ("<path>\t<region>\t<sha>") rows; prepend the path only when absent.
            if row.count("\t") == 1:
                body_rows.append(f"{path}\t{row}")
            else:
                body_rows.append(row)

    body_rows.sort()  # codepoint order == LC_ALL=C sort (TD §1.1).
    lines = [f"# hos-manifest-schema: {CURRENT_SCHEMA}"]
    lines.extend(body_rows)
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# CLI (TD §2.7)
# --------------------------------------------------------------------------- #


def _read_file(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


def _cmd_manifest_rows(args) -> int:
    data = _read_file(args.file)
    try:
        parsed = parse(data)
    except ParseError as e:
        sys.stderr.write(f"{args.file}: parse error {e}\n")
        return EXIT_INVALID
    result = validate(parsed)
    if not result.ok:
        for line, code, msg in result.errors:
            sys.stderr.write(f"{args.file}:{line}:{code}:{msg}\n")
        return EXIT_INVALID
    for row in manifest_rows(args.file, parsed):
        sys.stdout.write(row + "\n")
    return EXIT_OK


def _cmd_validate(args) -> int:
    data = _read_file(args.file)
    keys = None
    if args.placeholder_keys:
        keys = [k.strip() for k in args.placeholder_keys.split(",") if k.strip()]
    try:
        parsed = parse(data)
    except ParseError as e:
        # A structurally impossible read is itself an invalid file.
        sys.stdout.write(f"{e.line}:{e.kind}:{e.msg}\n")
        return EXIT_INVALID
    result = validate(parsed, placeholder_keys=keys)
    if result.ok:
        return EXIT_OK
    for line, code, msg in result.errors:
        sys.stdout.write(f"{line}:{code}:{msg}\n")
    return EXIT_INVALID


def _cmd_region_sha(args) -> int:
    data = _read_file(args.file)
    try:
        parsed = parse(data)
    except ParseError as e:
        sys.stderr.write(f"{args.file}: parse error {e}\n")
        return EXIT_INVALID
    target = args.region_id
    if not parsed.regions and target == "CORE":
        body = parsed.raw[len(parsed.front_matter) :] if parsed.front_matter else parsed.raw
        sys.stdout.write(region_sha(body) + "\n")
        return EXIT_OK
    for r in parsed.regions:
        if r.id == target:
            sys.stdout.write(region_sha(r.body) + "\n")
            return EXIT_OK
    sys.stderr.write(f"{args.file}: region '{target}' not present\n")
    return EXIT_REGION_ABSENT


def _cmd_compose(args) -> int:
    data = _read_file(args.file)
    try:
        parsed = parse(data)
    except ParseError as e:
        sys.stderr.write(f"{args.file}: parse error {e}\n")
        return EXIT_INVALID
    out = compose(parsed)
    sys.stdout.buffer.write(out)
    return EXIT_OK


def _cmd_plan(args) -> int:
    """Thin wrapper over `plan_upgrade` (TD §2.7 / §4.5).

    Reads the on-disk file and the ALREADY-SUBSTITUTED template the installer
    staged (D6 — regions.py never substitutes), takes the prior manifest's
    per-region base-shas as JSON, and prints the plan as a single JSON object on
    stdout:

        {"blocked": bool,
         "actions": [[region_id, action_token], ...],
         "hardstops": [[region_id, reason], ...],
         "new_bytes_b64": <base64 of the composed file> | null,
         "new_manifest_rows": ["<region>\\t<sha>", ...] | null}

    `new_bytes` is base64-encoded so the installer can redirect arbitrary bytes
    through JSON safely. Exit EXIT_OK (0) when not blocked, EXIT_DRIFT (4) when
    blocked — so the installer can branch on the exit code alone.
    """
    disk_bytes = _read_file(args.disk_file)
    template_bytes = _read_file(args.template_file)
    try:
        base_shas = json.loads(args.base_shas) if args.base_shas else {}
    except json.JSONDecodeError as e:
        sys.stderr.write(f"--base-shas is not valid JSON: {e}\n")
        return EXIT_USAGE
    if not isinstance(base_shas, dict):
        sys.stderr.write("--base-shas must be a JSON object {region: sha}\n")
        return EXIT_USAGE

    try:
        plan = plan_upgrade(
            disk_bytes,
            template_bytes,
            base_shas,
            squash=args.squash,
            first_install=args.first_install,
        )
    except ParseError as e:
        sys.stderr.write(f"parse error: {e}\n")
        return EXIT_INVALID

    payload = {
        "blocked": plan.blocked,
        "actions": [[rid, str(action)] for rid, action in plan.actions],
        "hardstops": [[rid, reason] for rid, reason in plan.hardstops],
        "new_bytes_b64": (
            base64.b64encode(plan.new_bytes).decode("ascii") if plan.new_bytes is not None else None
        ),
        "new_manifest_rows": plan.new_manifest_rows,
    }
    sys.stdout.write(json.dumps(payload) + "\n")
    return EXIT_DRIFT if plan.blocked else EXIT_OK


def _cmd_inject_pack(args) -> int:
    """Thin wrapper that appends a PACK:<name> region to a staged template.

    Parses the staged (already-substituted) CORE template, appends a new
    Region with the supplied pack body, re-composes via compose() (which
    alpha-orders PACK regions and applies _normalize_body so the on-disk bytes
    equal the manifest sha), then validates the result.  A duplicate PACK name
    surfaces here as E_DUP_PACK from validate().  Prints to stdout by default;
    --in-place rewrites the staged file.

    Mirrors _cmd_migrate exactly for the --in-place / stdout split.
    (TD-pack §1; ADR-031 §3.2)
    """
    # Step 1: validate pack slug before opening any file (fail-fast on bad slug).
    if not re.fullmatch(_PACK_SLUG, args.name):
        sys.stderr.write(f"invalid pack name '{args.name}' — must match [a-z0-9][a-z0-9-]*\n")
        return EXIT_INVALID

    # Step 2: read staged template and body file (FileNotFoundError → main() EXIT_USAGE).
    data = _read_file(args.file)
    body = _read_file(args.body_file)

    # Step 3: parse the staged template.
    try:
        parsed = parse(data)
    except ParseError as e:
        sys.stderr.write(f"{args.file}: parse error {e}\n")
        return EXIT_INVALID

    # Step 4: append the new PACK region (start_line=0, end_line=0 — not on disk yet).
    pack_region = Region(
        id=f"PACK:{args.name}", name=args.name, body=body, start_line=0, end_line=0
    )
    regions = list(parsed.regions) + [pack_region]
    merged = ParsedAgent(front_matter=parsed.front_matter, regions=regions, raw=parsed.raw)

    # Step 5: compose (alpha-orders via _canonical_order_key; normalizes bodies).
    out = compose(merged)

    # Step 6: re-validate (duplicate pack → E_DUP_PACK; literal marker in body →
    # E_LITERAL_MARKER_IN_BODY — fail-closed on a malformed body).
    check = validate(parse(out))
    if not check.ok:
        for line, code, msg in check.errors:
            sys.stderr.write(f"{line}:{code}:{msg}\n")
        return EXIT_INVALID

    # Step 7: emit.
    if args.in_place:
        with open(args.file, "wb") as fh:
            fh.write(out)
    else:
        sys.stdout.buffer.write(out)
    return EXIT_OK


def _cmd_migrate(args) -> int:
    """Thin wrapper over `migrate_flat` (TD §2.7 / §5.2, D3).

    Wraps a flat (marker-less) agent file into a single region per the
    provenance gate: `--ships yes` (HOS ships a same-named agent) → CORE;
    `--ships no` (unknown provenance) → PROJECT. Prints the wrapped canonical
    bytes to stdout by default (the installer redirects); `--in-place` rewrites
    the file. The caller is responsible for only invoking this on a genuinely
    flat file (parse(...).regions == []) — §5.4.
    """
    disk_bytes = _read_file(args.file)
    try:
        wrapped = migrate_flat(disk_bytes, hos_ships_agent=(args.ships == "yes"))
    except ParseError as e:
        sys.stderr.write(f"parse error: {e}\n")
        return EXIT_INVALID
    if args.in_place:
        with open(args.file, "wb") as fh:
            fh.write(wrapped)
    else:
        sys.stdout.buffer.write(wrapped)
    return EXIT_OK


def _cmd_base_shas(args) -> int:
    """Thin wrapper over `base_shas_for_path` — print one file's prior base-shas.

    Reads the target's existing `.hos-manifest` and prints the
    `{region_id: base_sha}` JSON object for `<path>`, ready to feed straight into
    `plan --base-shas`. A missing manifest (first install) prints `{}` and exits
    0 — the installer treats an empty object as first_install / unknown
    provenance.
    """
    try:
        manifest_text = _read_file(args.manifest).decode("utf-8")
    except FileNotFoundError:
        sys.stdout.write("{}\n")  # no prior manifest → first install
        return EXIT_OK
    try:
        shas = base_shas_for_path(manifest_text, args.path)
    except ValueError as e:
        sys.stderr.write(f"{args.manifest}: {e}\n")
        return EXIT_INVALID
    sys.stdout.write(json.dumps(shas) + "\n")
    return EXIT_OK


def _cmd_assemble_manifest(args) -> int:
    """Thin wrapper over `assemble_manifest` — write the full `.hos-manifest`.

    Reads a JSON object `{path: ["<region>\\t<sha>", ...]}` from `--spec` (a file)
    or stdin and prints the complete schema-v2 manifest text (schema header +
    LC_ALL=C-sorted rows). Rows may be path-less or full — both accepted (§1.1).
    """
    if args.spec:
        raw = _read_file(args.spec).decode("utf-8")
    else:
        raw = sys.stdin.read()
    try:
        rows_by_file = json.loads(raw)
    except json.JSONDecodeError as e:
        sys.stderr.write(f"--spec is not valid JSON: {e}\n")
        return EXIT_USAGE
    if not isinstance(rows_by_file, dict):
        sys.stderr.write("--spec must be a JSON object {path: [rows]}\n")
        return EXIT_USAGE
    sys.stdout.write(assemble_manifest(rows_by_file))
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="regions.py",
        description="HOS region mechanism: parse/validate/compose/sha for layered agent .md files.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    mr = sub.add_parser("manifest-rows", help="emit path<TAB>region<TAB>sha rows (canonical order)")
    mr.add_argument("file")
    mr.set_defaults(func=_cmd_manifest_rows)

    va = sub.add_parser("validate", help="fail-closed structural validation (exit 2 on invalid)")
    va.add_argument("file")
    va.add_argument(
        "--placeholder-keys",
        default=None,
        help="comma-separated keys; flag any {KEY} inside CORE/PACK (D7)",
    )
    va.set_defaults(func=_cmd_validate)

    rs = sub.add_parser("region-sha", help="print the sha of one region (exit 3 if absent)")
    rs.add_argument("file")
    rs.add_argument("region_id", metavar="region-id")
    rs.set_defaults(func=_cmd_region_sha)

    co = sub.add_parser("compose", help="print canonical bytes (the only writer's output)")
    co.add_argument("file")
    co.set_defaults(func=_cmd_compose)

    pl = sub.add_parser(
        "plan",
        help="plan a one-file upgrade; print JSON, exit 4 if drift-blocked (TD §4.5)",
    )
    pl.add_argument("disk_file", metavar="disk-file", help="the agent file currently on disk")
    pl.add_argument(
        "template_file",
        metavar="substituted-template-file",
        help="the HOS template, ALREADY substituted by the installer (D6)",
    )
    pl.add_argument(
        "--base-shas",
        default="{}",
        help="JSON object {region_id: base_sha} from the prior manifest for this file",
    )
    pl.add_argument(
        "--squash",
        action="store_true",
        help="explicit consent: convert drift/removed HARDSTOP → REFRESH/DROP (TD §4.3)",
    )
    pl.add_argument(
        "--first-install",
        action="store_true",
        help="seed an empty PROJECT stub when none exists on disk (TD §7.1)",
    )
    pl.set_defaults(func=_cmd_plan)

    mi = sub.add_parser(
        "migrate",
        help="wrap a flat (marker-less) agent into one region by provenance (TD §5.2)",
    )
    mi.add_argument("file")
    mi.add_argument(
        "--ships",
        required=True,
        choices=("yes", "no"),
        help="yes → HOS ships this slug (wrap as CORE); no → unknown (wrap as PROJECT)",
    )
    mi.add_argument(
        "--in-place",
        action="store_true",
        help="rewrite the file instead of printing to stdout",
    )
    mi.set_defaults(func=_cmd_migrate)

    ij = sub.add_parser(
        "inject-pack",
        help="append a PACK:<name> region (body from --body-file) to a staged "
        "template and re-compose (TD-pack §1; ADR-031 §3.2)",
    )
    ij.add_argument(
        "file",
        metavar="staged-template-file",
        help="the staged (already-substituted) CORE template to inject into",
    )
    ij.add_argument(
        "--name",
        required=True,
        help="pack slug ([a-z0-9][a-z0-9-]*) — becomes the PACK:<name> region id",
    )
    ij.add_argument(
        "--body-file",
        required=True,
        help="raw region-body bytes for the PACK:<name> region (packs/<name>/<agent>.md)",
    )
    ij.add_argument(
        "--in-place",
        action="store_true",
        help="rewrite the staged file instead of printing to stdout",
    )
    ij.set_defaults(func=_cmd_inject_pack)

    bs = sub.add_parser(
        "base-shas",
        help="print {region: base_sha} JSON for one path from a prior manifest (TD §4.5)",
    )
    bs.add_argument("manifest", help="the target's existing .hos-manifest (missing → {})")
    bs.add_argument("path", help="the repo-relative agent path to extract rows for")
    bs.set_defaults(func=_cmd_base_shas)

    am = sub.add_parser(
        "assemble-manifest",
        help="emit the full schema-v2 .hos-manifest from a {path: [rows]} JSON spec (TD §1.1)",
    )
    am.add_argument(
        "--spec",
        default=None,
        help="JSON file {path: [rows]}; reads stdin if omitted",
    )
    am.set_defaults(func=_cmd_assemble_manifest)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except FileNotFoundError as e:
        sys.stderr.write(f"file not found: {e.filename}\n")
        return EXIT_USAGE
    except ParseError as e:  # pragma: no cover - per-command handlers catch first
        sys.stderr.write(f"parse error: {e}\n")
        return EXIT_INVALID


if __name__ == "__main__":
    sys.exit(main())
