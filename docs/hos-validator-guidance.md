# HOS Validator Guidance: Triaging Scores

## Scores are risk signals, not pass/fail gates

Validator composite scores indicate the *level of scrutiny warranted* — they are not binary pass/fail results. A HIGH composite score on a low-context change (e.g. adding a constant, renaming a variable, updating a comment) is expected behavior: several validators (prompt-ambiguity, complexity, portability) react to diff size and structural patterns without knowing the change is trivially safe.

**Triage rule: read the finding, not the score.**

Before acting on a HIGH score, open the per-dimension breakdown in `.claudetmp/oversight/validators/` and ask:

1. Which dimension(s) drove the score?
2. Does the finding describe a real risk in *this change*, or is it a structural signal that does not apply (e.g. complexity flagging a generated migration file)?
3. Is it reproducible? Run the validator again on the same input.

If the finding does not describe a real risk, disposition it as `noise` in the convergence ledger (see METHODOLOGY.md §"Convergence by disposition").

## Common false-positive patterns

| Situation | Typical high-scoring dimension | Why it fires | Correct disposition |
|---|---|---|---|
| Adding a single constant or enum value | prompt-ambiguity | Short diff with no surrounding context text scores high on ambiguity heuristic | `noise` — verify the constant value is correct, then move on |
| Adding a migration file | complexity (cyclomatic) | Auto-generated migration bodies contain many conditional branches | `noise` — review migration correctness, not cyclomatic score |
| Renaming a variable across many files | portability | Large diff triggers size heuristic | `noise` if rename is purely mechanical |
| Copying boilerplate config | ip_check (Level 2) | Prompt artifact absent or terse | `noise` after confirming provenance |

## When a HIGH score is a genuine gate

A HIGH static-analysis score on a Bandit finding is **always a blocking gate** — it is never noise by default. Bandit HIGH findings must be resolved (fix or explicitly accepted with a human sign-off) before merge. See METHODOLOGY.md §"Blocking gates" for the full list.

## References

- METHODOLOGY.md §"Convergence by disposition — triage/accept, not fix-everything"
- `scripts/oversight/run_validators.sh` — runs all validators and writes `summary.json`
- `.claudetmp/oversight/validators/summary.json` — per-dimension scores and weights
