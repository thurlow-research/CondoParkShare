<!--
AI AGENTS: If this PR was created by an AI agent (not a human), you MUST:
1. Set the title to: [AI: agent-name] Brief description
2. Replace the section below with the full ## 🤖 AI-Submitted Pull Request block
   (see docs/AGENTS.md for the required format)
Omitting the disclosure is a protocol violation caught by oversight-evaluator.
-->

## Summary

<!-- What does this PR do? One paragraph. -->

## AI Assistance

- [ ] No AI-generated code in this PR
- [ ] AI-generated code present — risk level: **LOW / MEDIUM / HIGH / CRITICAL** *(delete as applicable)*

<!-- AI-SUBMITTED PR? Replace the checkbox above with the full disclosure block:

## 🤖 AI-Submitted Pull Request

This PR was **created and submitted by an AI agent**. A human did not manually write or submit this PR.

| | |
|---|---|
| **Agent** | `[agent-name]` |
| **Model** | `[model-id]` |
| **Actor** | `hos-worker-hos[bot]` (worker bot) |
| **Supervised-by** | `@ScottThurlow` |
| **Submitted** | [YYYY-MM-DD] |
| **Step / context** | [build step N or session description] |

Human approval is required before merge.

-->

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
