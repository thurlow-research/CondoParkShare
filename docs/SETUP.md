# Setting Up the Agent Pipeline Framework in a New Project

This guide walks through applying the CondoParkShare agent pipeline framework to a new project repository. The framework is the agent definitions, validation scripts, and oversight tooling — not the application code.

For customization guidance after setup, see `docs/CUSTOMIZATION.md`.
For the agent roles and pipeline structure, see `docs/AGENTS.md`.

---

## Prerequisites

### Required
- **Claude Code** CLI or desktop app (authenticated)
- A project **git repository** — the framework is applied to a repo root
- **Python 3.10+** — used by the oversight validators

### For AI-powered validation (optional but recommended)
- **agy** (Antigravity/Gemini CLI) — cross-vendor consistency reviewer
- **codex** (OpenAI CLI) — adversarial gap-finder

Install both with:
```bash
bash scripts/setup_clis.sh auth   # authenticate existing installs
# or
bash tools/setup-build-mac.sh     # full machine bootstrap (macOS)
bash tools/setup-build.sh         # full machine bootstrap (Linux)
```

Check availability:
```bash
agy --version    # should return a version number
codex --version  # should return a version number
```

---

## Step 1 — Copy the framework files

From the CondoParkShare repo, run `install.sh` targeting your new project:

```bash
# In the CondoParkShare repo:
bash scripts/framework/install.sh \
  --source /path/to/CondoParkShare \
  --target /path/to/your-new-project
```

The install script will:
1. Create required directories in the target project
2. Copy all 23 agent files from `.claude/agents/`
3. Copy the 6 framework scripts from `scripts/framework/`
4. **Ask you** for project-specific values (see Step 2)
5. Write `scripts/framework/config.sh` with your answers
6. Run `check_agents_static.sh` to confirm the installation is clean

> **Re-running:** `install.sh` is idempotent. Run it again to pick up framework updates — it reads your existing `config.sh` values and only prompts for fields that are new or changed. It never overwrites agent files you have already customized.

---

## Step 2 — Answer the configuration questions

During the install, you will be prompted for four values. These are saved to `scripts/framework/config.sh` and used by every framework script.

| Value | What it is | Example |
|---|---|---|
| `PROJECT_NAME` | Human-readable name shown in AI review prompts | `"MyApp"` |
| `PROJECT_STACK` | Tech stack description for the reviewers | `"Rails 7 + PostgreSQL"` |
| `PROJECT_NON_AGENT_TOKENS` | Pipe-separated hostnames/services in your agent files that aren't agent names — the static checker needs these to avoid false positives | `"myserver\|mydb\|staging-host"` |
| `DESIGN_PACK_PATH` | Path to your design system doc, relative to repo root — included in AI review | `"docs/design-system/DESIGN.md"` or blank if none |

Edit `scripts/framework/config.sh` directly at any time, or re-run `install.sh` to update values interactively.

---

## Step 3 — Add your design pack (if you have one)

`ux-designer` and `ui-reviewer` both require a design pack. If your project has a design system:

1. Copy or create your design pack directory (e.g., `Specs/design-pack/`).
2. At minimum, the pack needs:
   - `DESIGN.md` — design rules, color tokens, component specs, voice/tone
   - `css/tokens.css` — CSS custom properties for every design token
   - `style-guide.html` — rendered component reference (open in a browser)
   - `feedback-states.html` — error/warning/success/info states
3. Update the path references in `ux-designer.md` and `ui-reviewer.md` to match your actual path.
4. Set `DESIGN_PACK_PATH` in `config.sh` to point to your `DESIGN.md`.

If you have no design system yet, `ux-designer` will create one from scratch during the initial design audit.

---

## Step 4 — Verify the installation

Invoke the `framework-setup-validator` agent in Claude Code:

```
Invoke framework-setup-validator
```

It will run through this checklist and report anything missing:
- All required directories exist
- All 23 agent files are present
- Framework scripts are executable
- `config.sh` has non-placeholder values
- `agy` and `codex` CLIs are available (warns if not — validation still works without them, but AI review is skipped)

If everything is green: `framework-setup-validator` prints "Framework is correctly installed."

---

## Step 5 — Customize agents for your project

Before running the project start sequence, update these agents with project-specific content:

| Agent | What to update |
|---|---|
| `pm-agent` | Spec file paths (`Specs/SPEC-1-pilot.md` → your spec files); pilot scope description |
| `architect` | Stack description; deployment host/URL; ADR output path |
| `technical-design` | Stack-specific design items (replace Django/GiST/HTMX with your equivalents) |
| `coder` | Build order (replace §12 of SPEC-1 reference); stack conventions (replace Django idioms) |
| `ux-designer` | Design pack file paths |
| `ui-reviewer` | Design pack file paths; project-specific component rules |
| `infra-reviewer` | Canonical URL; deployment host; infra stack (replace Compose/Caddy checks if needed) |
| `deploy-verify` | Production URL; alias domains; backup paths |

See `docs/CUSTOMIZATION.md` for detailed guidance on each agent and what to change.

After customizing, validate:
```bash
bash scripts/framework/run_framework_validation.sh --static-only
```

---

## Step 6 — Run the project start sequence

With the framework installed and agents customized, invoke agents in this order before writing any code:

```bash
# In Claude Code, invoke each agent in sequence:

# 1. PM agent — spec review and human Q&A
# "Invoke pm-agent for initial spec review"
# Wait for pm-agent to complete Q&A and write docs/pm/CONFIRMED-REQUIREMENTS.md

# 2. UX designer — design pack audit
# "Invoke ux-designer for initial design audit"
# Wait for ux-designer to fill gaps and write docs/design/UX-DESIGN-READINESS.md

# 3. Architect — technical architecture
# "Invoke architect for initial architecture review"
# Answer architect's questions; wait for docs/architecture/ADR-001-pilot.md

# 4. Technical design — detailed spec
# "Invoke technical-design to produce the technical design"
# Iterates with architect until approved; produces docs/design/TECHNICAL-DESIGN.md
```

**Gate:** Do not start build step 1 until `docs/design/TECHNICAL-DESIGN.md` is architect-approved.

---

## Step 7 — Validate before first commit

Before committing anything (including the customized agent files), run the full framework validation:

```bash
bash scripts/framework/run_framework_validation.sh
```

This runs:
1. `check_agents_static.sh` — confirms agent file references are valid and escalation paths resolve
2. `validate_agents.sh` — agy and codex review the agent definitions for consistency and gaps

Fix any blocking findings before committing.

---

## Ongoing: before every commit

After any change to agent files, pipeline docs, or application code:

```bash
# See what needs reviewing:
bash scripts/framework/run_post_change_sweep.sh

# Then in Claude Code:
# "Run post-change sweep"
```

The sweep agent categorizes your diff and drives all relevant reviews (code-reviewer, security-reviewer, ui-reviewer, etc.) in the correct dependency order.

---

## Reference: directory structure after install

```
your-project/
├── .claude/
│   └── agents/                  ← 23 agent definition files
├── docs/
│   ├── AGENTS.md                ← pipeline documentation
│   ├── OVERSIGHT-RUNBOOK.md     ← operational runbook
│   ├── SETUP.md                 ← this file
│   ├── CUSTOMIZATION.md         ← customization guide
│   ├── pm/                      ← pm-agent output (created at project start)
│   ├── architecture/            ← architect output
│   └── design/                  ← technical-design + ux-designer output
├── scripts/
│   └── framework/
│       ├── install.sh           ← run to install or update
│       ├── config.sh            ← generated; your project-specific values
│       ├── check_agents_static.sh
│       ├── validate_agents.sh
│       ├── run_framework_validation.sh
│       └── run_post_change_sweep.sh
└── audit/
    └── oversight-log.jsonl      ← append-only audit trail
```
