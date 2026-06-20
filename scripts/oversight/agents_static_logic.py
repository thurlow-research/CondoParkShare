#!/usr/bin/env python3
"""agents_static_logic.py — pure classification logic for check_agents_static.sh.

SPEC-336 / Issue #336. `scripts/framework/check_agents_static.sh` is a fast,
no-AI static consistency checker for the agent pipeline. Per the #314 policy
("prefer Python for logic, shell for launch — establish testability as a code
review criterion"), this module promotes the script's deterministic
classification logic out of inline `grep` chains and an inline `python3 -c`
heredoc into named, importable, unit-testable functions.

FOUR pure functions (architect binding 2 — OQ-2 adds path-ref extraction):

  extract_path_refs(agent_text)        — section 3 line 128 `grep -oE` raw
                                          path-reference extraction.
  filter_path_ref(ref, output_docs)    — section 3 six-guard SKIP/CHECK cascade.
  extract_escalation_targets(agent_text) — section 4 inline-python escalation
                                          regex (lines 178-184), promoted.
  classify_token(token, ...)           — section 4 three-stage exclusion cascade
                                          (lines 153-172): SKIP/EXTERNAL/CHECK.

SHELL INTEGRATION (binding 3):
  - extract_path_refs / extract_escalation_targets read agent file content on
    STDIN (not a file path as argv) — eliminates the `open('$f')` shell-quoting
    hazard of the old inline heredoc.
  - filter_path_ref / classify_token take small strings on ARGV.

CONFIG (binding 4): PROJECT_NON_AGENT_TOKENS and EXTERNAL_AGENTS are sourced by
the shell from config.sh and passed in as arguments. This module NEVER sources
config.sh.

PURITY (binding 6 / R5): the four logic functions perform NO subprocess, NO
network, and NO file I/O. They are importable and unit-testable with synthetic
string inputs. Only the __main__ CLI shim performs I/O.

NO BEHAVIOR CHANGE (spec §5): every regex and every guard is a faithful port of
the current shell behavior. Same OK/FAIL/WARN lines, same exit codes.
"""

from __future__ import annotations

import re
import sys

# Classification result tokens — printed to stdout for the shell to branch on.
SKIP = "SKIP"
CHECK = "CHECK"
EXTERNAL = "EXTERNAL"

# Section 3 extraction pattern — faithful port of the shell `grep -oE` at line 128:
#   `[A-Za-z][A-Za-z0-9_./-]+/[A-Za-z0-9_./-]+\.(md|yaml|html|css|sh|py|json)[^`]*`
# The whole path (the text between the backticks) is the single capturing group;
# the extension alternation is a NON-capturing group so re.findall returns the
# full path string, not just the extension.
_PATH_REF_RE = re.compile(
    r"`("
    r"[A-Za-z][A-Za-z0-9_./-]+/[A-Za-z0-9_./-]+"
    r"\.(?:md|yaml|html|css|sh|py|json)"
    r"[^`]*"
    r")`"
)

# Section 4 escalation pattern — byte-for-byte the inline `python3 -c` regex
# (spec §R1, current script lines 181). The (?i:...) group makes ONLY the verb
# alternation case-insensitive; the captured agent name stays lowercase-anchored.
_ESCALATION_RE = re.compile(
    r"(?i:escalat\w+\s+to|invok\w+|receives?\s+from|notif\w+)"
    r"[^`]*`([a-z][a-z0-9_-]+)`"
)


def extract_path_refs(agent_text: str) -> list[str]:
    """Extract backtick-quoted path references from agent file text (OQ-2 / R3).

    Faithful port of the section-3 shell pipeline (line 128):
        grep -oE '`...path...`' | tr -d '`' | grep -v '^http'

    Returns the raw reference strings in document order, backticks already
    stripped (the capture group is the inside of the backticks), with any
    http-prefixed reference dropped. Does NOT apply the SKIP/CHECK cascade —
    that is filter_path_ref's job. Empty/no-match text returns [].

    Pure: no I/O; does not mutate input.
    """
    refs = _PATH_REF_RE.findall(agent_text or "")
    return [r for r in refs if not r.startswith("http")]


def _clean_ref(ref: str) -> str:
    """Reproduce the shell `tr -d '`"' | sed 's/#.*//' | xargs` cleaning.

    1. Strip all backtick and double-quote characters.
    2. Truncate at the first '#' (drop the anchor fragment).
    3. xargs-equivalent: trim whitespace and take the first shell word; an
       all-whitespace / empty input yields "" (matches xargs on empty input).
    """
    cleaned = ref.replace("`", "").replace('"', "")
    cleaned = cleaned.split("#", 1)[0]
    parts = cleaned.split()
    return parts[0] if parts else ""


def filter_path_ref(ref: str, output_docs: set[str]) -> str:
    """Decide whether a path reference should be existence-checked (R3).

    Faithful port of the section-3 six-guard cascade (shell lines 112-122).
    Returns SKIP if, after cleaning, ANY of the following holds; else CHECK:
      1. starts with 'http'            (line 113)
      2. is empty                      (line 114)
      3. contains no '/'  (bare name)  (line 115)
      4. starts with '{'  (template)   (line 116)
      5. starts with 'PROJECT/'        (line 117)
      6. is in output_docs (exempt)    (lines 119-121)

    The existence test ([[ -e ]]) stays in shell; this function never touches
    disk. Pure.
    """
    cleaned = _clean_ref(ref)
    if cleaned.startswith("http"):
        return SKIP
    if cleaned == "":
        return SKIP
    if "/" not in cleaned:
        return SKIP
    if cleaned.startswith("{"):
        return SKIP
    if cleaned.startswith("PROJECT/"):
        return SKIP
    if cleaned in (output_docs or set()):
        return SKIP
    return CHECK


def extract_escalation_targets(agent_text: str) -> list[str]:
    """Extract candidate escalation-target agent names from agent text (R1).

    Faithful promotion of the inline `python3 -c` regex (shell lines 178-184).
    Returns every match of the escalation pattern across the full text, in
    document order (AC2). No-match returns []. No classification is performed
    here — that is classify_token's job.

    Pure: no I/O; does not mutate input.
    """
    return _ESCALATION_RE.findall(agent_text or "")


def _split_alternation(pipe_joined: str) -> set[str]:
    """Split a pipe-joined ERE alternation string into an exact-match set.

    Reproduces the shell's anchored `grep -qE "^($LIST)$"` semantics: membership
    is exact equality against each alternative. Empty fragments are dropped so an
    empty alternation string ("") yields an empty set (never {""}), matching the
    shell's `[[ -n "$LIST" ]]` short-circuit.
    """
    if not pipe_joined:
        return set()
    return {tok for tok in pipe_joined.split("|") if tok}


def classify_token(
    token: str,
    known_agents: set,
    non_agent_tokens: str,
    known_labels: str,
    known_short_agents: str,
    external_agents: str,
) -> str:
    """Classify an escalation-target token: SKIP / EXTERNAL / CHECK (R2).

    Faithful port of the section-4 three-stage exclusion cascade (shell lines
    153-172). The list parameters are the shell's pipe-joined ERE alternation
    strings, matched with EXACT equality to reproduce the anchored `^($LIST)$`
    grep semantics (substring / regex search would over-match, e.g. 'superhuman'
    must NOT match 'human').

    Cascade (in order):
      1. token in non_agent_tokens                          -> SKIP  (line 153)
      2. token in known_labels                              -> SKIP  (line 161)
      3. token NOT in known_short_agents AND no '-' in token -> SKIP (lines 164-166)
      4. external_agents non-empty AND token in it          -> EXTERNAL (169-171)
      5. otherwise                                          -> CHECK

    `known_agents` is accepted but the CHECK existence test against it stays in
    shell (`grep -qx`); CHECK is the signal to run it. Pure: no I/O.
    """
    non_agent = _split_alternation(non_agent_tokens)
    labels = _split_alternation(known_labels)
    short_agents = _split_alternation(known_short_agents)
    external = _split_alternation(external_agents)

    if token in non_agent:
        return SKIP
    if token in labels:
        return SKIP
    if token not in short_agents and "-" not in token:
        return SKIP
    if external and token in external:
        return EXTERNAL
    return CHECK


# --------------------------------------------------------------------------- #
# CLI shim — the ONLY place in this module that performs I/O (binding 6).      #
#   extract-path-refs            stdin: agent text  -> one ref per line        #
#   extract-escalation-targets   stdin: agent text  -> one name per line       #
#   filter-path-ref <ref> [output_doc...]           -> SKIP|CHECK              #
#   classify-token <token> <known_agents_pipe> <non_agent> <labels> \          #
#                  <short_agents> <external>         -> SKIP|EXTERNAL|CHECK     #
# Result is on STDOUT (not the exit code); the shell branches on the text,     #
# mirroring how it branched on grep output. Exit 2 on usage error.            #
# --------------------------------------------------------------------------- #
def _usage(msg: str) -> int:
    sys.stderr.write(f"agents_static_logic.py: {msg}\n")
    return 2


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        return _usage("missing subcommand")
    cmd, rest = argv[0], argv[1:]

    if cmd == "extract-path-refs":
        for ref in extract_path_refs(sys.stdin.read()):
            sys.stdout.write(ref + "\n")
        return 0

    if cmd == "extract-escalation-targets":
        for name in extract_escalation_targets(sys.stdin.read()):
            sys.stdout.write(name + "\n")
        return 0

    if cmd == "filter-path-ref":
        if not rest:
            return _usage("filter-path-ref requires <ref>")
        ref = rest[0]
        output_docs = set(rest[1:])
        sys.stdout.write(filter_path_ref(ref, output_docs) + "\n")
        return 0

    if cmd == "classify-token":
        if len(rest) != 6:
            return _usage(
                "classify-token requires 6 args: "
                "<token> <known_agents> <non_agent_tokens> "
                "<known_labels> <known_short_agents> <external_agents>"
            )
        token, known_agents_pipe = rest[0], rest[1]
        non_agent, labels, short_agents, external = rest[2], rest[3], rest[4], rest[5]
        known_agents = _split_alternation(known_agents_pipe)
        sys.stdout.write(
            classify_token(
                token, known_agents, non_agent, labels, short_agents, external
            )
            + "\n"
        )
        return 0

    return _usage(f"unknown subcommand: {cmd}")


if __name__ == "__main__":
    raise SystemExit(main())
