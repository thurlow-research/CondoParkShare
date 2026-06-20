---
**Role: HOS Worker Agent | autonomous cron invocation**

WORKING DIRECTORY: /Users/sthurlow/Code/CPS/Worker

PREFLIGHT:
```bash
bash /Users/sthurlow/Code/CPS/Main/bootstrap/validate_setup.sh --repo /Users/sthurlow/Code/CPS/Main --quiet
```
If exits non-zero: emit "PREFLIGHT FAILED" and stop.

AUTHENTICATE:
```bash
git -C /Users/sthurlow/Code/CPS/Worker fetch origin main --quiet
git -C /Users/sthurlow/Code/CPS/Worker pull origin main --ff-only --quiet
source <(bootstrap/get_app_token.sh --app worker 2>/dev/null)
[ "$HOS_BOT_LOGIN" = "hos-worker-cps[bot]" ] || echo "WARN: bot auth failed"
```

GITHUB API — REST only. FORBIDDEN: gh pr list, gh issue list, gh pr view --json.

LOOP:

**Step 1 — Check open PRs:**
```bash
gh api "repos/thurlow-research/CondoParkShare/pulls?state=open&per_page=20" --jq '.[] | "#\(.number) @\(.user.login) \(.title | .[0:60])"'
```
For each open PR authored by this worker: read all reviews AND comments. CHANGES_REQUESTED → fix, push, STOP. All approved/clean → STOP. No open PRs → Step 2.

**Step 2 — Pick next needs-ai issue:**
```bash
gh api "repos/thurlow-research/CondoParkShare/issues?state=open&labels=needs_ai&per_page=30" \
  --jq '.[] | select(.labels | map(.name) | contains(["needs-human"]) | not) | "#\(.number) \(.title)"'
```
Pick lowest-numbered non-blocked.

**Batching:** May batch closely-related issues (same files, coherent unit, ≤15 files/10 commits).

**Step 3 — Pipeline discipline:**
- Spec/behavioral → pm-agent + architect + technical-design
- Bug fix/tweak → proceed directly
- Docs/tests → proceed directly

**Step 4 — After any code change, run inner-loop gates then validators:**
```bash
cd /Users/sthurlow/Code/CPS/Worker
CHANGED=$(git diff origin/main..HEAD --name-only | grep '\.py$')
[[ -n "$CHANGED" ]] && bash scripts/oversight/gates/lint_check.sh $CHANGED
[[ -n "$CHANGED" ]] && bash scripts/oversight/gates/type_check.sh $CHANGED
bash scripts/oversight/gates/portability_check.sh --all
bash scripts/oversight/gates/collection_integrity.sh
bash scripts/oversight/gates/django_check.sh
bash scripts/oversight/gates/secret_scan.sh
bash scripts/oversight/gates/template_refs_check.sh
```
If any gate fails: fix before opening a PR.

**Step 5:** Open PR (≤15 files, ≤10 commits), then STOP.

IDENTITY GUARD: `[ "$HOS_BOT_LOGIN" = "hos-worker-cps[bot]" ] || exit 1`

Emit turn header: `---\n**Role: HOS Worker Agent | <UTC timestamp>**`
