# HOS override ledger

Durable record of where CondoParkShare's `.claude/agents/` intentionally diverge
from the HOS framework templates, and why. **HOS upgrades do not preserve agent
front-matter** (`description`/`tools`/`model` come from the HOS template), and
agents outside HOS's managed set are not migrated at all — so divergences must be
recorded here and re-applied after every `hos_install.sh` run.

Re-apply after any upgrade with:

```bash
bash scripts/hos/postinstall-restore-frontmatter.sh
```

That script is idempotent and also **asserts** each override is in place — it
exits non-zero if a future upgrade silently reverted one.

| Agent | Override | Reason | Remediation | Approved |
|---|---|---|---|---|
| `a11y-reviewer` | Grant **all tools** (remove the `tools:` restriction) so the agent can reach the **Chrome DevTools MCP** for live accessibility audits. HOS v0.3.x's template restricts it to `Read/Grep/Glob/Bash`. | Live DevTools audits (rendered contrast, focus order, Lighthouse, HTMX-swap focus) are a **CPS build gate**, not a nicety — static template analysis cannot compute them. Front-matter is not preserved on upgrade, so the restriction returns every upgrade. | `postinstall-restore-frontmatter.sh` removes the `tools:` block (restoring the all-tools grant) + asserts it. | Scott Thurlow, 2026-06-15 (tool-grant = security-relevant) |
| `dep-mapper` | PROJECT region carries **synthesized** CPS Django blast-radius tracing (Organization fan-in, dual managers, middleware order). | CPS never authored a dep-mapper override (v0.1.0 was byte-identical to CORE); there is no django dep-mapper pack, and the generic mapper self-reports LOW confidence on tenant-isolation wiring. The synthesized depth is the only Django intelligence. **Drift risk:** it names concrete symbols — verify against the tree before relying on it; re-check when middleware/managers/the booking migration change. | Content lives in `packs/condoparkshare/dep-mapper.md` → its PROJECT region (survives upgrades via `SKIP_PROJECT`). | architect-reviewed, 2026-06-15 |
| `deploy-verify` | **Unmanaged** — CPS-only agent, not in HOS's 24-slug set. Stays flat; not migrated to the region model. | No HOS equivalent. Will diverge from evolving CORE conventions until the v0.4.0 consumer-pack mechanism can adopt it. Functional today. | Pack content pre-staged at `packs/condoparkshare/deploy-verify.md` for a drop-in v0.4.0 adoption. | architect-reviewed, 2026-06-15 |

| `scripts/oversight/gates/portability_check.sh` | Line 10 comment uses `C:/Users/<name>/` (forward slashes) instead of HOS CORE's `C:\Users\<name>\` (backslashes). | The backslash form matches the gate's own Windows path pattern `[A-Za-z]:\\Users\\`, causing the gate to flag its own script when run with `--all` (CWE false positive). Fixed in CPS PR #113. HOS filed as HOS#303 but not yet merged upstream as of v0.3.8. Must be re-applied after each HOS upgrade until HOS merges the fix. | `postinstall-restore-frontmatter.sh` will need updating to assert this line, or watch for HOS to absorb it. | Scott Thurlow, 2026-06-16 |

## Upstream (HOS framework) follow-ups noted during migration
- `a11y-reviewer`: the generic `aria-required`/required-field-marking check has no explicit home in CORE or the django pack — candidate gap to raise in HumanOversightSystem (not a CPS loss).
