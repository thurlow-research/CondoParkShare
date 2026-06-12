# Oversight Audit Trail

This directory is the committed, persistent audit trail for the Human Oversight System (HOS). It lives on the **current branch** — oversight evidence travels with the code it covers. When a step merges to main, its audit record merges too.

---

## Structure

```
audit/
  oversight-log.jsonl               ← complete append-only event log (machine-queryable)
  YYYY-MM-DD-step-{N}-{name}-{TIER}.md  ← per-step timestamped summary (human-readable)
  escalations/
    YYYY-MM-DD-step-{N}-{type}.md   ← human authorization records for CRITICAL steps
  panel-runs/
    YYYY-MM-DD-pr{N}-panel-run.md   ← panel run records with finding summaries
```

Example directory listing that tells the story at a glance:
```
2026-06-09-step-01-scaffold-LOW.md
2026-06-10-step-02-multitenant-HIGH.md
2026-06-11-step-03-auth-CRITICAL.md
escalations/2026-06-11-step-03-human-auth.md
panel-runs/2026-06-11-pr7-panel-run.md
2026-06-12-step-04-datamodel-HIGH.md
2026-06-13-step-06-booking-gates-CRITICAL.md
```

The risk tier in the filename (`-CRITICAL`, `-HIGH`) lets an auditor immediately see which steps warranted the highest scrutiny without opening anything.

---

## Files

### `oversight-log.jsonl`
Complete, append-only structured event log. One JSON object per line, self-contained. Used for automated queries and longitudinal research analysis.

```bash
# All events for step 3
jq 'select(.step == 3)' audit/oversight-log.jsonl

# Escaped defect rate
jq 'select(.event == "panel-run") | {step, escaped_defects}' audit/oversight-log.jsonl

# All sign-offs across all steps
jq 'select(.event == "sign-off") | {step, role, status, iterations}' audit/oversight-log.jsonl

# Human escalations
jq 'select(.event == "human-authorization" or .event == "evaluator-decision")' audit/oversight-log.jsonl
```

### `YYYY-MM-DD-step-{N}-{name}-{TIER}.md`
Human-readable summary generated at step completion. Contains:
- Step metadata (tier, composite score, build step name)
- Sign-off record (who approved what, iterations taken, findings summary)
- Test results (coverage %, mutant score)
- Second review outcome (vendor, verdict, finding count)
- Evaluator decision (PROCEED/CONDITIONAL/ESCALATE + reasoning)
- Conditional items if any (what the human must verify before merge)

### `escalations/YYYY-MM-DD-step-{N}-{type}.md`
Human authorization records for CRITICAL steps. Contains the human's explicit decision text, date, and what they reviewed. Created by the human before the oversight-evaluator runs.

### `panel-runs/YYYY-MM-DD-pr{N}-panel-run.md`
Summary of each cross-vendor panel run: vendors used, finding counts, escaped defects identified, PR thread count.

---

## Audit guarantee

This directory and its contents are committed to the **current branch** as part of each build step completion. The `oversight-log.jsonl` is append-only — line deletions or modifications are visible in `git log -- audit/oversight-log.jsonl`. An auditor can verify both presence and integrity of the audit trail using standard git tooling with no additional infrastructure.

The timestamped filenames make the directory browsable in chronological order and immediately communicate the risk posture of each step.

---

*Full event schema: `OVERSIGHT-CONTRACT.md §1` in the HumanOversightSystem repo.*
