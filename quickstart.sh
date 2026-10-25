#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# 1-click bootstrap for a FRESH VPS.
#
# Run this (root) on a new server:
#
#   sudo bash -c "$(curl -fsSL https://raw.githubusercontent.com/deepuyrf-blip/claude-code-web/main/quickstart.sh)"
#
# It installs Docker + the whole stack and prints your Cloudflare tunnel URL.
#
# Optional env: CCW_REPO (git url), CCW_DIR (install dir, default /opt/claude-code-web),
#               OPENROUTER_API_KEY (skip the prompt)
# ---------------------------------------------------------------------------
set -euo pipefail

REPO="${CCW_REPO:-https://github.com/deepuyrf-blip/claude-code-web}"
DIR="${CCW_DIR:-/opt/claude-code-web}"
say(){ printf '\n\033[1m==> %s\033[0m\n' "$*"; }

if [ "$(id -u)" -ne 0 ]; then
  echo "Root chahiye. Aise chalao:" >&2
  echo '  sudo bash -c "$(curl -fsSL https://raw.githubusercontent.com/deepuyrf-blip/claude-code-web/main/quickstart.sh)"' >&2
  exit 1
fi

if ! command -v git >/dev/null 2>&1 || ! command -v curl >/dev/null 2>&1; then
  say "git + curl install kar raha hoon..."
  apt-get update -qq && apt-get install -y git curl ca-certificates
fi

say "Code clone/update: $DIR"
mkdir -p "$(dirname "$DIR")"
if [ -d "$DIR/.git" ]; then
  git -C "$DIR" fetch --depth 1 origin main >/dev/null 2>&1 || true
  git -C "$DIR" reset --hard origin/main >/dev/null 2>&1 || true
else
  rm -rf "$DIR"
  git clone --depth 1 "$REPO" "$DIR"
fi
cd "$DIR"
chmod +x install.sh manage.sh set-password.sh 2>/dev/null || true

KEY="${OPENROUTER_API_KEY:-}"
if [ -z "$KEY" ]; then
  printf '\nOpenRouter API key (sk-or-...): '
  read -r KEY
fi
[ -n "$KEY" ] || { echo "API key zaroori hai." >&2; exit 1; }

say "Install + Cloudflare tunnel start kar raha hoon (5-10 min lag sakte hain)..."
TUNNEL=1 OPENROUTER_API_KEY="$KEY" ./install.sh --non-interactive --tunnel

say "Ho gaya! Upar wala URL browser me kholo."
echo "Install dir: $DIR"
echo "Baad me:  cd $DIR && bash manage.sh [logs|restart|stop|update|upgrade]"
