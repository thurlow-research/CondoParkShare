#!/usr/bin/env bash
# install.sh — install or update the agent pipeline framework in a project repo.
#
# Run this in a NEW project to copy all framework files and generate a
# project-specific config. Run it again in an EXISTING project to pick up
# framework updates without overwriting your config values.
#
# What this script does:
#   1. Creates required directories (docs/pm, docs/architecture, docs/design, etc.)
#   2. Copies agent files from the source repo (if --source is given)
#   3. Reads existing config values from scripts/framework/config.sh (if present)
#   4. Asks for any missing project-specific values
#   5. Writes/updates scripts/framework/config.sh with all values
#   6. Makes framework scripts executable
#   7. Runs framework-setup-validator to confirm the installation
#
# Usage:
#   # In the source (framework) repo — copy to a new project:
#   bash scripts/framework/install.sh --source /path/to/source --target /path/to/new-project
#
#   # In an existing project — update config only (no file copying):
#   bash scripts/framework/install.sh
#
#   # Non-interactive (CI/automated) — skip prompts, use env vars instead:
#   PROJECT_NAME="MyApp" PROJECT_STACK="Rails + PostgreSQL" \
#     bash scripts/framework/install.sh --non-interactive

set -euo pipefail

SOURCE_REPO=""
TARGET_REPO="."
NON_INTERACTIVE=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --source)          SOURCE_REPO="$2";       shift 2 ;;
        --target)          TARGET_REPO="$2";       shift 2 ;;
        --non-interactive) NON_INTERACTIVE=true;   shift ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

TARGET_REPO=$(cd "$TARGET_REPO" && pwd)
CONFIG_FILE="$TARGET_REPO/scripts/framework/config.sh"

echo "══════════════════════════════════════════════════"
echo "  Agent Pipeline Framework — Install / Update"
echo "  Target: $TARGET_REPO"
echo "══════════════════════════════════════════════════"
echo ""

# ── Step 1: Create required directories ─────────────────────────────────────
echo "── Step 1: Directories"
for d in \
    .claude/agents \
    docs/pm \
    docs/architecture \
    docs/design \
    scripts/framework \
    .claudetmp/framework \
    audit
do
    full="$TARGET_REPO/$d"
    if [[ -d "$full" ]]; then
        echo "  exists: $d"
    else
        mkdir -p "$full"
        echo "  created: $d"
    fi
done
echo ""

# ── Step 2: Copy framework files (if source provided) ───────────────────────
if [[ -n "$SOURCE_REPO" ]]; then
    echo "── Step 2: Copy framework files from $SOURCE_REPO"
    SOURCE_REPO=$(cd "$SOURCE_REPO" && pwd)

    # Copy agent files — never overwrite existing ones (project may have customised them)
    AGENT_COUNT=0
    for src in "$SOURCE_REPO"/.claude/agents/*.md; do
        [[ -f "$src" ]] || continue
        fname=$(basename "$src")
        dest="$TARGET_REPO/.claude/agents/$fname"
        if [[ -f "$dest" ]]; then
            echo "  skip (exists): .claude/agents/$fname"
        else
            cp "$src" "$dest"
            echo "  copied: .claude/agents/$fname"
            AGENT_COUNT=$(( AGENT_COUNT + 1 ))
        fi
    done
    echo "  $AGENT_COUNT agent file(s) copied"

    # Copy framework scripts — always update (these are framework infrastructure)
    for script in \
        check_agents_static.sh \
        validate_agents.sh \
        install.sh \
        run_framework_validation.sh
    do
        src="$SOURCE_REPO/scripts/framework/$script"
        dest="$TARGET_REPO/scripts/framework/$script"
        if [[ -f "$src" ]]; then
            cp "$src" "$dest"
            chmod +x "$dest"
            echo "  updated: scripts/framework/$script"
        fi
    done
    echo ""
else
    echo "── Step 2: No --source given — skipping file copy (config update only)"
    echo ""
fi

# ── Step 3: Read existing config values ─────────────────────────────────────
echo "── Step 3: Project configuration"

# Load existing values if config exists
EXISTING_PROJECT_NAME=""
EXISTING_PROJECT_STACK=""
EXISTING_PROJECT_NON_AGENT_TOKENS=""
EXISTING_DESIGN_PACK_PATH=""
EXISTING_EXTRA_REVIEW_FILES=""

if [[ -f "$CONFIG_FILE" ]]; then
    # Source safely by extracting values without executing arbitrary code
    EXISTING_PROJECT_NAME=$(grep  '^PROJECT_NAME='               "$CONFIG_FILE" | head -1 | cut -d= -f2- | tr -d '"')
    EXISTING_PROJECT_STACK=$(grep '^PROJECT_STACK='              "$CONFIG_FILE" | head -1 | cut -d= -f2- | tr -d '"')
    EXISTING_PROJECT_NON_AGENT_TOKENS=$(grep '^PROJECT_NON_AGENT_TOKENS=' "$CONFIG_FILE" | head -1 | cut -d= -f2- | tr -d '"')
    EXISTING_DESIGN_PACK_PATH=$(grep '^DESIGN_PACK_PATH='        "$CONFIG_FILE" | head -1 | cut -d= -f2- | tr -d '"')
    EXISTING_EXTRA_REVIEW_FILES=$(grep '^EXTRA_REVIEW_FILES='    "$CONFIG_FILE" | head -1 | cut -d= -f2- | tr -d '"')
    echo "  Existing config found — will update only missing/changed values"
else
    echo "  No existing config — will create fresh config.sh"
fi
echo ""

# ── Step 4: Prompt for missing values ────────────────────────────────────────
prompt_value() {
    local varname="$1"
    local description="$2"
    local existing="$3"
    local default="$4"
    local result

    if [[ -n "$existing" ]]; then
        echo "  $varname: $existing (existing — press Enter to keep)"
        if $NON_INTERACTIVE; then
            result="$existing"
        else
            read -r -p "  New value (or Enter to keep): " input
            result="${input:-$existing}"
        fi
    else
        echo "  $varname: $description"
        if [[ -n "$default" ]]; then
            echo "  Default: $default"
        fi
        if $NON_INTERACTIVE; then
            # Use env var if set, else default
            result="${!varname:-$default}"
        else
            read -r -p "  Value: " input
            result="${input:-$default}"
        fi
    fi
    printf '%s' "$result"
}

echo "── Step 4: Collect project-specific values"
echo ""
echo "These values are saved in scripts/framework/config.sh and reused on future"
echo "installs/updates. They keep the framework scripts generic while providing"
echo "project context to the AI reviewers."
echo ""

NEW_PROJECT_NAME=$(prompt_value \
    "PROJECT_NAME" \
    "Human-readable project name (e.g. 'MyApp')" \
    "$EXISTING_PROJECT_NAME" \
    "")

echo ""
NEW_PROJECT_STACK=$(prompt_value \
    "PROJECT_STACK" \
    "Tech stack description for AI reviewers (e.g. 'Django + HTMX + PostgreSQL')" \
    "$EXISTING_PROJECT_STACK" \
    "")

echo ""
NEW_NON_AGENT_TOKENS=$(prompt_value \
    "PROJECT_NON_AGENT_TOKENS" \
    "Pipe-separated hostnames/services that appear in agent files but aren't agent names (e.g. 'myserver|mydb|myapp'). Leave blank if none." \
    "$EXISTING_PROJECT_NON_AGENT_TOKENS" \
    "")

echo ""
NEW_DESIGN_PACK_PATH=$(prompt_value \
    "DESIGN_PACK_PATH" \
    "Path to your design system doc relative to repo root (e.g. 'Specs/design-pack/DESIGN.md'). Leave blank if no design system." \
    "$EXISTING_DESIGN_PACK_PATH" \
    "")

echo ""
NEW_EXTRA_FILES=$(prompt_value \
    "EXTRA_REVIEW_FILES" \
    "Space-separated extra files to include in AI review. Leave blank if none." \
    "$EXISTING_EXTRA_REVIEW_FILES" \
    "")

echo ""

# ── Step 5: Write config.sh ──────────────────────────────────────────────────
echo "── Step 5: Writing scripts/framework/config.sh"

cat > "$CONFIG_FILE" <<CONFIGEOF
#!/usr/bin/env bash
# config.sh — project-specific overrides for scripts/framework/ tools.
#
# Generated by scripts/framework/install.sh — edit directly or re-run install.sh.
# This file is sourced automatically by the framework scripts.
#
# To update: re-run scripts/framework/install.sh (existing values are preserved
# and you are only prompted for new or changed fields).

# ── Project identity ─────────────────────────────────────────────────────────
PROJECT_NAME="${NEW_PROJECT_NAME}"
PROJECT_STACK="${NEW_PROJECT_STACK}"

# ── Non-agent tokens ─────────────────────────────────────────────────────────
# Pipe-separated hostnames, service names, or domain terms in your agent files
# that are not agent names. The static checker uses this to suppress false positives.
PROJECT_NON_AGENT_TOKENS="${NEW_NON_AGENT_TOKENS}"

# ── Design pack path ─────────────────────────────────────────────────────────
# Path to your project's design system doc, relative to the repo root.
# validate_agents.sh includes this in the AI review package.
DESIGN_PACK_PATH="${NEW_DESIGN_PACK_PATH}"

# ── Extra files for AI review ────────────────────────────────────────────────
# Space-separated additional files to include in validate_agents.sh reviews.
EXTRA_REVIEW_FILES="${NEW_EXTRA_FILES}"
CONFIGEOF

echo "  Written: $CONFIG_FILE"
echo ""

# ── Step 6: Make scripts executable ──────────────────────────────────────────
echo "── Step 6: Ensuring scripts are executable"
for script in \
    "$TARGET_REPO/scripts/framework/check_agents_static.sh" \
    "$TARGET_REPO/scripts/framework/validate_agents.sh" \
    "$TARGET_REPO/scripts/framework/install.sh" \
    "$TARGET_REPO/scripts/framework/run_framework_validation.sh"
do
    [[ -f "$script" ]] && chmod +x "$script" && echo "  chmod +x $(basename "$script")"
done
echo ""

# ── Step 7: Run setup validator ───────────────────────────────────────────────
echo "── Step 7: Running setup validation"
echo ""
cd "$TARGET_REPO"
bash scripts/framework/check_agents_static.sh
echo ""
echo "══════════════════════════════════════════════════"
echo "  Installation complete."
echo "  Next step: invoke framework-setup-validator agent"
echo "  to confirm all agents are present and configured."
echo "══════════════════════════════════════════════════"
