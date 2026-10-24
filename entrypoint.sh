#!/usr/bin/env bash
# Container entrypoint: a couple of sanity checks, then start the CloudCLI web UI.
set -euo pipefail

if [ -z "${ANTHROPIC_AUTH_TOKEN:-}" ]; then
  echo "[warn] ANTHROPIC_AUTH_TOKEN is empty - set OPENROUTER_API_KEY in your .env" >&2
fi

echo "[entrypoint] ANTHROPIC_BASE_URL = ${ANTHROPIC_BASE_URL:-<unset>}"
echo "[entrypoint] claude binary      = $(command -v claude || echo 'NOT FOUND')"
echo "[entrypoint] cloudcli version   = $(cloudcli --version 2>/dev/null || echo 'unknown')"

# Skip Claude Code's interactive first-run onboarding (theme + trust prompt).
# Only written when there is no existing config, so real user settings are kept.
CFG="${HOME}/.claude.json"
if [ ! -f "$CFG" ]; then
  printf '{"hasCompletedOnboarding":true,"bypassPermissionsModeAccepted":true}\n' > "$CFG" 2>/dev/null || true
fi

mkdir -p "${HOME}/.cloudcli" "${WORKSPACES_ROOT:-$HOME/workspace}"

# CloudCLI reads HOST / PORT / SERVER_PORT / DATABASE_PATH / WORKSPACES_ROOT from the env.
exec cloudcli start --port "${PORT:-3001}"
