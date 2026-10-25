#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# One-command installer: Claude Code web UI on a VPS.
#
#   ./install.sh                      interactive
#   OPENROUTER_API_KEY=sk-or-... ./install.sh --non-interactive --tunnel
#
# Flags:  --non-interactive  (read values from env, no prompts)
#         --tunnel           (start a Cloudflare quick tunnel and print its URL)
#         --reset            (regenerate .env / caddy/Caddyfile)
#
# Env (non-interactive): OPENROUTER_API_KEY, DOMAIN, WORKSPACE_DIR,
#                        AUTH_USER, AUTH_PASS, HTTP_PORT, HTTPS_PORT, TUNNEL=1
# ---------------------------------------------------------------------------
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_DIR"

if [ -t 1 ]; then
  RED=$'\033[31m'; GRN=$'\033[32m'; YLW=$'\033[33m'; BLD=$'\033[1m'; RST=$'\033[0m'
else
  RED=''; GRN=''; YLW=''; BLD=''; RST=''
fi
info(){ printf '%s\n' "${BLD}[*]${RST} $*"; }
ok(){   printf '%s\n' "${GRN}[+]${RST} $*"; }
warn(){ printf '%s\n' "${YLW}[!]${RST} $*"; }
die(){  printf '%s\n' "${RED}[x]${RST} $*" >&2; exit 1; }

# ---- 0. sanity ------------------------------------------------------------
[ "$(uname -s)" = "Linux" ] || die "Ye installer Linux VPS ke liye hai."
command -v curl >/dev/null 2>&1 || die "curl chahiye: apt-get update && apt-get install -y curl"

# ---- 1. docker ------------------------------------------------------------
install_docker() {
  info "Docker install kar raha hoon (get.docker.com)..."
  if curl -fsSL https://get.docker.com -o /tmp/get-docker.sh && sh /tmp/get-docker.sh; then
    command -v docker >/dev/null 2>&1 && return 0
  fi
  warn "Convenience script fail hua - manually install kar raha hoon (bina docker-model-plugin)..."
  # shellcheck disable=SC1091
  . /etc/os-release
  local DISTRO="${ID:-ubuntu}"
  local CODENAME="${VERSION_CODENAME:-$(lsb_release -cs 2>/dev/null || echo stable)}"
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL "https://download.docker.com/linux/${DISTRO}/gpg" -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/${DISTRO} ${CODENAME} stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -qq
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin \
    || apt-get install -y docker-ce docker-ce-cli containerd.io
  systemctl enable --now docker 2>/dev/null || service docker start 2>/dev/null || true
}

if ! command -v docker >/dev/null 2>&1; then
  install_docker
  command -v docker >/dev/null 2>&1 || die "Docker install nahi ho paya. Manually: https://docs.docker.com/engine/install/"
  ok "Docker install ho gaya: $(docker --version)"
else
  ok "Docker pehle se installed: $(docker --version)"
fi

if docker compose version >/dev/null 2>&1; then
  DC="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  DC="docker-compose"
else
  info "Compose plugin missing - install kar raha hoon..."
  apt-get update -qq && apt-get install -y docker-compose-plugin || true
  DC="docker compose"
fi
ok "Compose command: $DC"

docker info >/dev/null 2>&1 || die "Docker daemon access nahi hai. Root se chalao:  sudo ./install.sh"

# ---- 2. configuration -----------------------------------------------------
RESET=0; NONINT=0; TUNNEL="${TUNNEL:-0}"
for a in "$@"; do
  case "$a" in
    --reset) RESET=1 ;;
    --non-interactive|-y) NONINT=1 ;;
    --tunnel) TUNNEL=1 ;;
  esac
done
[ -n "${OPENROUTER_API_KEY:-}" ] && NONINT=1

# Read a value from .env WITHOUT shell-expanding it.
get_env(){ [ -f .env ] && grep -E "^$1=" .env | tail -n1 | cut -d= -f2- || true; }

if [ -f .env ] && [ "$RESET" -eq 0 ]; then
  ok ".env already exists - usi ko use kar raha hoon (regenerate: ./install.sh --reset)"
  OR_KEY="${OPENROUTER_API_KEY:-$(get_env OPENROUTER_API_KEY)}"
  DOMAIN="$(get_env DOMAIN)"
  WORKSPACE_DIR="$(get_env WORKSPACE_DIR)"; WORKSPACE_DIR="${WORKSPACE_DIR:-./workspace}"
  HTTP_PORT="$(get_env HTTP_PORT)"; HTTP_PORT="${HTTP_PORT:-80}"
  HTTPS_PORT="$(get_env HTTPS_PORT)"; HTTPS_PORT="${HTTPS_PORT:-443}"
  AUSER="$(get_env AUTH_USER)"; AUSER="${AUSER:-admin}"
  APASS="$(get_env AUTH_PASS)"
elif [ "$NONINT" -eq 1 ]; then
  OR_KEY="${OPENROUTER_API_KEY:-}"
  DOMAIN="${DOMAIN:-}"
  WORKSPACE_DIR="${WORKSPACE_DIR:-./workspace}"
  AUSER="${AUTH_USER:-admin}"
  APASS="${AUTH_PASS:-}"
  HTTP_PORT="${HTTP_PORT:-80}"
  HTTPS_PORT="${HTTPS_PORT:-443}"
else
  info "Setup ke kuch sawaal:"
  read -r -p "  OpenRouter API key (sk-or-...): " OR_KEY
  read -r -p "  Domain (khali chhodo agar nahi hai - iske bina HTTPS nahi milega): " DOMAIN
  read -r -p "  Workspace dir (projects yahan rahenge) [./workspace]: " WORKSPACE_DIR
  WORKSPACE_DIR="${WORKSPACE_DIR:-./workspace}"
  read -r -p "  Web UI basic-auth username [admin]: " AUSER
  AUSER="${AUSER:-admin}"
  read -r -s -p "  Web UI basic-auth password (khali = auto-generate): " APASS; echo
  read -r -p "  HTTP port [80]: " HTTP_PORT; HTTP_PORT="${HTTP_PORT:-80}"
  read -r -p "  HTTPS port [443]: " HTTPS_PORT; HTTPS_PORT="${HTTPS_PORT:-443}"
fi

[ -n "${OR_KEY:-}" ] || die "OpenRouter API key zaroori hai (OPENROUTER_API_KEY set karo)."
[ -n "${APASS:-}" ] || APASS="$(head -c 32 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 24)"
[ "${#APASS}" -ge 8 ] || APASS="ChangeMe-$(date +%s)"
[ -n "${AUSER:-}" ] || AUSER="admin"

if [ -n "$DOMAIN" ]; then SITE="$DOMAIN"; else SITE=":80"; fi
PUBIP="$(curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null || hostname -I 2>/dev/null | awk '{print $1}')"

info "Basic-auth hash bana raha hoon (caddy image se)..."
AHASH="$(docker run --rm caddy:2 caddy hash-password --plaintext "$APASS" 2>/dev/null | tr -d '\r')"
[ -n "$AHASH" ] || die "Password hash generate nahi hua."

umask 077
cat > .env <<EOF
# Generated by install.sh on $(date -u +%Y-%m-%dT%H:%M:%SZ)
OPENROUTER_API_KEY=${OR_KEY}
ANTHROPIC_BASE_URL=https://openrouter.ai/api
ANTHROPIC_DEFAULT_OPUS_MODEL=
ANTHROPIC_DEFAULT_SONNET_MODEL=
ANTHROPIC_DEFAULT_HAIKU_MODEL=
DOMAIN=${DOMAIN}
WORKSPACE_DIR=${WORKSPACE_DIR}
HTTP_PORT=${HTTP_PORT}
HTTPS_PORT=${HTTPS_PORT}
AUTH_USER=${AUSER}
AUTH_PASS=${APASS}
EOF

# Caddyfile with the hash embedded (kept out of .env to avoid "$" issues).
sed -e "s|__SITE_ADDRESS__|${SITE}|g" \
    -e "s|__AUTH_USER__|${AUSER}|g" \
    -e "s|__AUTH_HASH__|${AHASH}|g" \
    caddy/Caddyfile.template > caddy/Caddyfile

cat > .credentials.txt <<EOF
URL      : $([ -n "$DOMAIN" ] && echo "https://${DOMAIN}" || echo "http://${PUBIP:-<server-ip>}:${HTTP_PORT}")
Username : ${AUSER}
Password : ${APASS}
EOF
chmod 600 .env .credentials.txt
ok ".env + caddy/Caddyfile + .credentials.txt likh diye."

# ---- 3. directories (container runs as uid 1000 "agent") ------------------
mkdir -p data/claude data/cloudcli data/cloudcli-ui "$WORKSPACE_DIR"
if [ "$(id -u)" -eq 0 ]; then
  chown -R 1000:1000 data "$WORKSPACE_DIR" 2>/dev/null || true
else
  warn "Root nahi ho - permission error aaye to: sudo chown -R 1000:1000 data ${WORKSPACE_DIR}"
fi

# ---- 4. build + start -----------------------------------------------------
info "Image build + containers start ho rahe hain (pehli baar 5-10 min)..."
$DC up -d --build

# ---- 5. optional Cloudflare quick tunnel ----------------------------------
TUNNEL_URL=""
if [ "$TUNNEL" = "1" ]; then
  info "Cloudflare quick tunnel start kar raha hoon..."
  docker rm -f cf-tunnel >/dev/null 2>&1 || true
  docker run -d --name cf-tunnel --restart unless-stopped --network host \
    cloudflare/cloudflared:latest tunnel --url "http://localhost:${HTTP_PORT}" >/dev/null 2>&1 || true
  for _ in 1 2 3 4 5 6 7 8; do
    sleep 2
    TUNNEL_URL="$(docker logs cf-tunnel 2>&1 | grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' | tail -1 || true)"
    [ -n "$TUNNEL_URL" ] && break
  done
fi

# ---- 6. summary -----------------------------------------------------------
echo
ok "Ho gaya!"
echo "------------------------------------------------------------"
if [ -n "$TUNNEL_URL" ]; then
  echo "  URL      : ${TUNNEL_URL}"
  echo "             (cloudflared tunnel - VPS reboot par badal jayega)"
else
  if [ -f .credentials.txt ]; then head -n1 .credentials.txt; fi
  [ -n "${DOMAIN:-}" ] || warn "Domain ke bina HTTPS nahi milega - public use ke liye domain set karo."
  echo "  (URL host ${PUBIP:-<server-ip>} ho sakta hai agar domain nahi hai)"
fi
echo "  Username : ${AUSER}"
echo "  Password : ${APASS}"
echo
echo "  Provider/key page: ${TUNNEL_URL:-$([ -n "${DOMAIN:-}" ] && echo "https://${DOMAIN}" || echo "http://${PUBIP:-<server-ip>}:${HTTP_PORT}")}/admin"
echo "  CloudCLI pehli visit par apna password bhi set karne ko kahega."
echo "  Logs    : $DC logs -f claude-web"
echo "  Manage  : bash manage.sh [logs|restart|stop|update|upgrade|status]"
echo "------------------------------------------------------------"
