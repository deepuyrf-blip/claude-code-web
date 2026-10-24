#!/usr/bin/env bash
# Change the web UI basic-auth password (regenerates caddy/Caddyfile and reloads Caddy).
# Usage: ./set-password.sh
set -euo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if docker compose version >/dev/null 2>&1; then DC="docker compose"; else DC="docker-compose"; fi

docker info >/dev/null 2>&1 || { echo "Docker daemon access nahi hai. Root se chalao:  sudo ./set-password.sh" >&2; exit 1; }

[ -f caddy/Caddyfile.template ] || { echo "caddy/Caddyfile.template nahi mila - sahi folder me chalao." >&2; exit 1; }
[ -f caddy/Caddyfile ] || { echo "caddy/Caddyfile nahi mila - pehle ./install.sh chalao." >&2; exit 1; }

# Reuse the site address + username already in the generated Caddyfile.
SITE="$(awk 'NF==2 && $2=="{" {print $1; exit}' caddy/Caddyfile)"
SITE="${SITE:-:80}"
AUSER="$(awk '/^[[:space:]]+[A-Za-z0-9._-]+[[:space:]]+\$2/ {print $1; exit}' caddy/Caddyfile)"
AUSER="${AUSER:-admin}"

read -r -p "New username [${AUSER}]: " U; U="${U:-$AUSER}"
read -r -s -p "New password (khali = auto-generate): " P; echo
[ -n "$P" ] || P="$(head -c 32 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 24)"
[ "${#P}" -ge 8 ] || { echo "Password kam se kam 8 characters ka rakho." >&2; exit 1; }

H="$(docker run --rm caddy:2 caddy hash-password --plaintext "$P" | tr -d '\r')"
[ -n "$H" ] || { echo "Hash generate nahi hua." >&2; exit 1; }

sed -e "s|__SITE_ADDRESS__|${SITE}|g" \
    -e "s|__AUTH_USER__|${U}|g" \
    -e "s|__AUTH_HASH__|${H}|g" \
    caddy/Caddyfile.template > caddy/Caddyfile

$DC restart caddy >/dev/null

{
  echo "URL      : ${SITE}"
  echo "Username : ${U}"
  echo "Password : ${P}"
} | tee .credentials.txt
chmod 600 .credentials.txt

echo
echo "Ho gaya. Caddy reload ho gaya - ab isi username/password se login karo."
