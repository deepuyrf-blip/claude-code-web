#!/usr/bin/env bash
# Small helper around docker compose for day-to-day use.
set -euo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if docker compose version >/dev/null 2>&1; then DC="docker compose"; else DC="docker-compose"; fi

case "${1:-help}" in
  logs)    $DC logs -f --tail=200 claude-web ;;
  restart) $DC restart ;;
  stop)    $DC down ;;
  start)   $DC up -d ;;
  status)  $DC ps ;;
  update)  git pull --ff-only && $DC up -d --build ;;
  upgrade) $DC build --no-cache claude-web && $DC up -d ;;   # latest Claude Code + all CLIs
  url)     [ -f .credentials.txt ] && head -n1 .credentials.txt || echo "no .credentials.txt (run ./install.sh)";;
  *)
    cat <<'EOF'
Usage: ./manage.sh <command>
  logs      Follow the web UI container logs
  status    Show container status
  start     Start containers
  restart   Restart containers
  stop      Stop and remove containers
  update    git pull + rebuild
  upgrade   Rebuild claude-web with --no-cache (latest Claude Code + CLIs)
  url       Print the configured URL
EOF
    ;;
esac
