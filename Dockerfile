# Claude Code web UI, self-hosted.
#
# Base image is Docker's own sandbox template for Claude Code: Ubuntu-based,
# runs as the non-root user `agent` (uid 1000), and already ships Claude Code,
# Node.js, Python, Go, Java and a C/C++ toolchain.
#   https://hub.docker.com/r/docker/sandbox-templates
#
# On top of it we add the CloudCLI open-source web UI (chat, file explorer,
# git, built-in shell, mobile friendly) exactly the way CloudCLI's own image
# does it: https://github.com/siteboon/claudecodeui/tree/main/docker
FROM docker/sandbox-templates:claude-code

# ---- extra CLI tools the agent reaches for (build tools are already present) ----
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl git jq ripgrep sqlite3 zip unzip tree less \
    && rm -rf /var/lib/apt/lists/*

# ---- CloudCLI web UI, installed as the non-root `agent` user ----
USER agent
RUN npm install -g @cloudcli-ai/cloudcli typescript \
        --fetch-retries=5 \
        --fetch-retry-mintimeout=20000 \
        --fetch-retry-maxtimeout=120000 \
        --fetch-timeout=600000 \
        --no-audit --no-fund \
    && cloudcli --version && tsc --version

# ---- runtime configuration ----
WORKDIR /home/agent/workspace
ENV HOST=0.0.0.0 \
    PORT=3001 \
    SERVER_PORT=3001 \
    WORKSPACES_ROOT=/home/agent/workspace \
    DATABASE_PATH=/home/agent/.cloudcli/auth.db \
    NODE_ENV=production
EXPOSE 3001

USER root
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh
USER agent

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
