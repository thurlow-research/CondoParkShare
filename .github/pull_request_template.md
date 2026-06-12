<!--
  If this PR was submitted by AI, the [AI: agent-name] prefix is in the title
  and the 🤖 AI-Submitted section below must be filled in.
  If this is a human-authored PR, delete the AI-Submitted section entirely.
-->

## 🤖 AI-Submitted Pull Request

<!-- DELETE THIS SECTION if a human opened this PR. -->
<!-- Fill in if AI opened this PR — must appear before Summary. -->

| | |
|---|---|
| **Submitted by** | `agent-name` (or `claude` when no sub-agent) |
| **Model** | `claude-sonnet-4-6` |
| **Submitted** | YYYY-MM-DD |
| **Human review required** | Yes — *describe why: e.g. "oversight-evaluator approved; panel review pending"* |

Human approval is required before merge — branch protection enforces this.

---

## Summary

<!-- What does this PR do? One paragraph. -->

## AI Assistance

- [ ] No AI-generated code in this PR
- [ ] AI-generated code present — risk level: **LOW / MEDIUM / HIGH / CRITICAL** *(delete as applicable)*
- [ ] This PR was submitted by AI (fill in the 🤖 section above)

## Prompt Artifacts

<!-- For MEDIUM+ AI-generated code: list prompt artifact files or write 'N/A' -->

| File | Prompt artifact | Risk |
|---|---|---|
| `src/...` | `prompts/...` | MEDIUM |

## Human Review Checklist

<!-- Work through any Human Review Required flags from the Claude Code session -->

- [ ] All CRITICAL and HIGH risk items reviewed line-by-line
- [ ] Hallucination surface warnings verified (⚠️ VERIFY comments in code)
- [ ] Blast radius assessed for any destructive operations
- [ ] Open review items from prior sessions addressed (check `./scripts/prompt_audit.sh --pending`)

## Confidence

<!-- Paste the CONFIDENCE declaration from the Claude Code session, or write your own -->

> CONFIDENCE: __%
> Basis: ___

## Testing

- [ ] Existing tests pass
- [ ] New tests added for new logic
- [ ] Manually tested: ___
