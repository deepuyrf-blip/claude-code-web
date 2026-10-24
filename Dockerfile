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

# ---- other coding-agent CLIs CloudCLI can drive (Codex / OpenCode / Cursor) ----
# Each still needs its own sign-in (Settings -> Agents -> Login, or `codex login`).
USER agent
RUN npm install -g @openai/codex opencode-ai \
        --fetch-retries=5 \
        --fetch-retry-mintimeout=20000 \
        --fetch-retry-maxtimeout=120000 \
        --fetch-timeout=600000 \
        --no-audit --no-fund \
    && { command -v codex >/dev/null 2>&1 && echo "codex ok" || echo "codex NOT found"; } \
    && { command -v opencode >/dev/null 2>&1 && echo "opencode ok" || echo "opencode NOT found"; }

# Cursor CLI installs to ~/.local/bin/cursor-agent (non-fatal if the script fails)
RUN curl -fsSL https://cursor.com/install | bash || echo "cursor-agent install skipped"

# ---- Graphify: /graphify skill (codebase -> queryable knowledge graph) ----
# Needs the PyPI package `graphifyy` (double-y); the CLI is `graphify`.
RUN python3 -m pip install --user --break-system-packages graphifyy 2>/dev/null \
    || python3 -m pip install --user graphifyy 2>/dev/null \
    || echo "graphify skipped - install later with: python3 -m pip install --user graphifyy"

# ---- runtime configuration ----
ENV PATH=/home/agent/.local/bin:${PATH}
WORKDIR /home/agent/workspace
ENV HOST=0.0.0.0 \
    PORT=3001 \
    SERVER_PORT=3001 \
    WORKSPACES_ROOT=/home/agent/workspace \
    DATABASE_PATH=/home/agent/.cloudcli/auth.db
# NOTE: deliberately NOT setting NODE_ENV=production - that makes npm skip
# devDependencies, which breaks CloudCLI plugin installs (they need typescript
# and @types/node to build).
EXPOSE 3001

USER root
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh
USER agent

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
