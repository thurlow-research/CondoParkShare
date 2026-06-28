# shellcheck shell=bash
# bin/lib/git-credentials.sh — deterministic git credentials for HOS cron (#738)
#
# Why this exists
# ---------------
# On the first live cron run the worker's `git push` failed because git's
# configured `credential.helper` (Git Credential Manager / osxkeychain) is
# interactive-oriented — it depends on a desktop session / keychain / PATH that
# the cron thin-env does not have. The agent self-healed with a fragile inline
# `git -c credential.helper='!gh auth git-credential' push`, which relies on the
# model rediscovering the workaround every run. The launcher must instead make
# pushes work deterministically, regardless of whatever helper the host has.
#
# Mechanism
# ---------
# `hos_configure_git_credentials` exports GIT_CONFIG_COUNT / GIT_CONFIG_KEY_n /
# GIT_CONFIG_VALUE_n so that git layers two settings on top of the normal config
# hierarchy, at the HIGHEST precedence, for the CURRENT PROCESS AND ITS CHILDREN:
#
#   credential.helper                    = ""                         (reset)
#   credential.https://github.com.helper = !<gh> auth git-credential
#
# An empty `credential.helper` value resets git's accumulated helper list, so
# any host-configured helper (credential-manager-core, osxkeychain, …) is
# discarded; the url-scoped entry then makes the already-authenticated `gh` the
# sole responder for github.com. This is the same empty-then-gh pattern that
# `gh auth setup-git` writes — but via env vars, so:
#   * nothing is written to disk and the developer's global ~/.gitconfig is
#     never mutated (the config dies with the process), and
#   * the headless `claude` subprocess inherits it (env is passed to children),
#     so pushes inside the agent session work with no inline workaround.
#
# Security (#734): the token is NEVER materialized into a URL, command line,
# argv, or file. `gh` supplies it over the credential-helper stdin protocol,
# reading GH_TOKEN from its own environment at fill time.
#
# Usage:
#   source bin/lib/git-credentials.sh
#   hos_configure_git_credentials [gh_binary_path]   # default: $(command -v gh)
#
# Returns non-zero (and configures nothing) if no gh binary is available.

hos_configure_git_credentials() {
  local gh_bin="${1:-$(command -v gh || true)}"
  if [[ -z "$gh_bin" ]]; then
    echo "hos_configure_git_credentials: gh not found on PATH" >&2
    return 1
  fi

  local helper="!${gh_bin} auth git-credential"
  # Append to any GIT_CONFIG_* entries already present rather than clobber them.
  local n="${GIT_CONFIG_COUNT:-0}"

  export "GIT_CONFIG_KEY_${n}=credential.helper"
  export "GIT_CONFIG_VALUE_${n}="
  export "GIT_CONFIG_KEY_$((n + 1))=credential.https://github.com.helper"
  export "GIT_CONFIG_VALUE_$((n + 1))=${helper}"
  export "GIT_CONFIG_KEY_$((n + 2))=credential.https://gist.github.com.helper"
  export "GIT_CONFIG_VALUE_$((n + 2))=${helper}"
  export "GIT_CONFIG_COUNT=$((n + 3))"
}
