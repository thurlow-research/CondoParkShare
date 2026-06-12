#!/usr/bin/env bash
# config.sh — project-specific overrides for scripts/framework/ tools.
#
# The framework scripts (check_agents_static.sh, validate_agents.sh) are
# project-agnostic by design. Put any project-specific configuration here
# rather than editing the scripts themselves.
#
# This file is sourced automatically by the framework scripts if it exists.

# ── Project identity ─────────────────────────────────────────────────────────
PROJECT_NAME="CondoParkShare"
PROJECT_STACK="Django + HTMX + PostgreSQL"

# ── Additional non-agent tokens ──────────────────────────────────────────────
# Hostnames, service names, or domain terms that appear in escalation-like
# phrases in agent files but are not agent names.
# Pipe-separated, no spaces. These extend the generic list in check_agents_static.sh.
PROJECT_NON_AGENT_TOKENS="opus|nexus|parkshare|kumajyo|bellevue|columbia"

# ── Design pack path ─────────────────────────────────────────────────────────
# Path to the project's design pack directory, relative to the repo root.
# validate_agents.sh includes this file in the review package.
DESIGN_PACK_PATH="Specs/condoparkshare-design-pack/DESIGN.md"

# ── Extra files to include in AI review ─────────────────────────────────────
# Space-separated list of additional files to include in validate_agents.sh reviews.
# DESIGN_PACK_PATH is always included; add anything else here.
EXTRA_REVIEW_FILES=""
