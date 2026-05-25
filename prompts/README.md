# Prompt Artifacts

One `.md` file per AI-generated code artifact at MEDIUM risk or above,
mirroring the `src/` directory structure.

```
src/auth/middleware.ts       ← generated file
prompts/auth/middleware.md   ← prompt artifact
```

Generate with: `./scripts/capture_prompt.sh <source-file> "<description>"`

See `AGENTS.md` for the full governance protocol.
