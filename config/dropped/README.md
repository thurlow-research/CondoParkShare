# Per-agent dropped-line allowlists

Each file in this directory is named `<agent-slug>.allow.yml` and contains
a list of lines from the BEFORE-snapshot of that agent that are intentionally
not present in the installed layered agent. Each entry must be reviewed and
approved by a human before it is committed.

## Format

```yaml
- line: "exact text of the dropped line (from the before-snapshot)"
  covered_by: "CORE | PACK:django | PROJECT | dropped"
  reason: "one-sentence explanation of why this line was dropped"
  approved_by: "github-username"
  approved_at: "YYYY-MM-DD"

- line: "another dropped line"
  covered_by: "dropped"
  reason: "this instruction was superseded by the CORE region wording"
  approved_by: "sthurlow"
  approved_at: "2026-06-15"
```

## Rules

- `line` must match the BEFORE-snapshot text exactly (the gate normalizes before
  hashing, so minor whitespace differences are tolerated, but the semantic
  content must be the same line).
- `covered_by` identifies where the semantic content now lives, or `"dropped"`
  if it is intentionally removed.
- `reason` is mandatory and must be a human-written justification, not
  auto-generated.
- `approved_by` and `approved_at` are mandatory. The gate will WARN on any
  allowlist entry that was never matched during a run (possible stale entry).
- Tool removals (`tools:` front-matter) that are intentionally dropped must
  appear here with `covered_by: "dropped"`.

## Example: `coder.allow.yml`

```yaml
- line: "- Grep"
  covered_by: "dropped"
  reason: "Grep tool removed from coder because the PROJECT region instructs
           use of Bash/find instead; confirmed in migration review 2026-06-15."
  approved_by: "sthurlow"
  approved_at: "2026-06-15"
```
