## Summary

<!-- What does this PR do? One paragraph. -->

## AI Assistance

- [ ] No AI-generated code in this PR
- [ ] AI-generated code present — risk level: **LOW / MEDIUM / HIGH / CRITICAL** *(delete as applicable)*

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
