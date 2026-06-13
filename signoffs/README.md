# Validation-suite sign-offs

This directory holds **committed** sign-off stamps — one per agent in the
validation suite. They are what the sign-off gate
(`scripts/oversight/signoff_gate.py`) checks before a change can merge (CI) or
deploy (`scripts/deploy.sh`).

Unlike the ephemeral review register under `.claudetmp/signoffs/` (gitignored,
inter-agent scratch), these stamps are tracked in git on purpose: the gate's
clock is the **git commit timestamp**, which only exists for committed files.

## Files

`signoffs/<role>.stamp` — one per role key in
`contract/step-manifest.yaml` → `role_mappings`:

| role | agent |
|---|---|
| `code-review` | code-reviewer |
| `security` | security-reviewer |
| `privacy` | privacy-reviewer |
| `test-unit` | unit-test |
| `test-system` | system-test |
| `process` | pm-agent |
| `infra` | infra-reviewer |
| `ui` | ui-reviewer |
| `a11y` | a11y-reviewer |

The required set is the **union of every step's `required_signoffs`** in the
manifest — i.e. the whole validation suite. The manifest is the single source of
truth; add a role there and the gate requires it here.

## Stamp format

```
role: security
agent: security-reviewer
status: APPROVED
signed_at: 2026-06-13T15:10:00Z   # informational — the gate uses git commit time
head_at_signing: a1b2c3d
note: Clean review. No cross-tenant leaks.
```

`status` must be `APPROVED`, `CONDITIONAL`, or `NOT_APPLICABLE`. A
`NOT_APPLICABLE` stamp still has to be re-committed after later changes — a role
can never silently fall behind the code.

## How an agent signs off

```bash
scripts/oversight/sign_off.sh security --status APPROVED --note "..."
git add signoffs/security.stamp
git commit -m "sign-off: security APPROVED"
```

## Why the timestamp logic works

The commit timestamp is set when `git commit` runs, not when the file is written
to disk. So:

1. Make changes (not yet committed).
2. Run the validation suite → stamps written to disk (not yet committed).
3. `git add -A && git commit` — changed files **and** stamps share commit time `T`.
4. Push.
5. Gate: changed files (`T`) vs stamps (`T`) → same timestamp → **PASS**.

Two-commit variant also passes: commit code at `T1`, commit stamps at `T2 > T1`.

The only case that fails (correctly): sign off at `T1`, then commit *new* changes
at `T2 > T1` without re-signing → the gate sees a file newer than a stamp → **FAIL**.
