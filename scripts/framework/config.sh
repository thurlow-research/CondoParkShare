# HOS project configuration — placeholder substitution values.
#
# These values are substituted into .claude/agents/*.md on install/upgrade.
# DO NOT DELETE. The installer reads these; missing values leave raw {TOKEN}s
# in the agent files (the #87/#99 destructive-upgrade class of bug).
#
# Each path is verified to point at a file/dir that actually exists in this repo.

PROJECT_NAME="CondoParkShare"
SPEC_FILE="Specs/SPEC-1-pilot.md"
DESIGN_PACK_DIR="docs/design"
ADR_FILE="docs/architecture/ADR-001-pilot.md"
PACK="django"
