# Claude Code Web UI on a VPS (OpenRouter edition)

Run **Claude Code** on your own server and drive it from the browser — a full
chat UI with a file explorer, git panel and a built-in shell — routed through a
**third-party, Anthropic-compatible API (OpenRouter)**.

One command installs Docker, builds the image, and starts everything behind a
Caddy reverse proxy (automatic HTTPS + password protection).

```
┌──────────┐   HTTPS   ┌────────┐   http   ┌──────────────────────────────┐
│ browser  │ ────────► │ Caddy  │ ───────► │ claude-web (CloudCLI UI)     │
└──────────┘  + basic  │ :443   │  :3001   │  └─ Claude Code CLI          │
               auth    └────────┘          │       └─ ANTHROPIC_BASE_URL  │
                                           │          = OpenRouter        │
                                           └──────────────────────────────┘
```

## What's inside

| Piece | What it does |
|---|---|
| `docker/sandbox-templates:claude-code` | Docker's own Claude Code base image (Ubuntu, non-root `agent` user, Node/Python/Go/Java). |
| `@cloudcli-ai/cloudcli` | Open-source web UI (AGPL-3): chat, files, git, shell, mobile friendly. Auto-discovers sessions in `~/.claude`. |
| `caddy` | Reverse proxy: automatic HTTPS when you set a domain, plus HTTP basic auth. |
| OpenRouter | Anthropic-compatible gateway — Claude Code talks to it with no local proxy. |

## Requirements

- A Linux VPS (Ubuntu/Debian recommended) with **root or sudo**.
- **≥ 2 GB RAM.** The web UI's dependency tree is large; on a 1 GB box add swap
  first (see Troubleshooting) or the image build can run out of memory.
- An **OpenRouter API key** — create one at <https://openrouter.ai/keys> (starts with `sk-or-`).
- Optional: a **domain** pointing at the server's IP, for real HTTPS.

## Quick start

```bash
git clone <this-repo> claude-code-web && cd claude-code-web
chmod +x install.sh manage.sh
./install.sh
```

`install.sh` will:

1. Install Docker + Compose if missing.
2. Ask for your OpenRouter key, an optional domain, and a basic-auth password
   (leave blank to auto-generate).
3. Generate `.env`, `caddy/Caddyfile` and `.credentials.txt`.
4. Build the image and start the containers.
5. Print the URL and credentials.

Then open the URL, log in with the basic-auth password, and on the CloudCLI
screen set your in-app password. Start a new session and chat.

### Hinglish quick start

```bash
git clone <this-repo> claude-code-web && cd claude-code-web
./install.sh          # OpenRouter key maangega, domain optional
# install hone ke baad terminal me URL + username + password dikh jayenge
./manage.sh logs      # logs dekhne ke liye
./manage.sh update    # naye version par update karne ke liye
```

## Configuration

Everything lives in `.env` (created by the installer, git-ignored):

| Variable | Meaning |
|---|---|
| `OPENROUTER_API_KEY` | Your OpenRouter key (`sk-or-...`). |
| `ANTHROPIC_BASE_URL` | Anthropic-compatible endpoint. Default `https://openrouter.ai/api` — **no `/v1`**. |
| `ANTHROPIC_DEFAULT_OPUS_MODEL` / `_SONNET_` / `_HAIKU_` | Optional model pins, in OpenRouter `vendor/model` form, e.g. `anthropic/claude-sonnet-4.5`. Blank = Claude Code defaults. |
| `DOMAIN` | Your domain (enables HTTPS). Blank = serve on port 80. |
| `WORKSPACE_DIR` | Host directory mounted as the agent's workspace (default `./workspace`). |
| `HTTP_PORT` / `HTTPS_PORT` | Published ports (default `80` / `443`). |

How the wiring works (inside the container):

```env
ANTHROPIC_BASE_URL=https://openrouter.ai/api
ANTHROPIC_AUTH_TOKEN=$OPENROUTER_API_KEY   # OpenRouter uses a bearer token
ANTHROPIC_API_KEY=""                       # must stay empty
CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY=1
```

After changing `.env`, apply it with `./manage.sh restart`.

## Change the web-UI password

```bash
./set-password.sh
```

Regenerates `caddy/Caddyfile` with a new hash and reloads Caddy. It also updates
`.credentials.txt`.

## Security

- **Basic auth** on every request (Caddy), and **CloudCLI's own password** set on
  first visit — two layers.
- Without a domain, traffic is HTTP (basic-auth credentials travel in the clear).
  Set a domain so Caddy issues a Let's Encrypt certificate automatically.
- The agent has **full read/write access** to `WORKSPACE_DIR`. Point it at a
  dedicated directory, not `/`.
- Anyone who reaches the URL controls a shell on the box. Keep it locked down.

## Troubleshooting

**`E: Unable to locate package docker-model-plugin` while installing Docker** — the
Docker convenience script ships a package that isn't published for older releases
(e.g. Ubuntu 20.04 "focal"). `install.sh` now falls back to a manual install
automatically. To do it by hand:

```bash
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io \
    docker-buildx-plugin docker-compose-plugin
docker --version && docker compose version
```

**Build runs out of memory (`Killed` / `npm error` during build)** — add swap, then rebuild:

```bash
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
./manage.sh update
```

**`model not found` inside the UI** — set the model pins in `.env` (OpenRouter ids)
and restart. Not every model is a good fit for Claude Code; Anthropic models work best.

**Permission denied on `data/` or `workspace/`** — the container runs as uid 1000:

```bash
sudo chown -R 1000:1000 data workspace
```

**Check the connection Claude Code is using** — inside the container:

```bash
docker compose exec claude-web claude --version
docker compose exec claude-web sh -lc 'echo $ANTHROPIC_BASE_URL'
```

## Honest caveats

- Claude Code is built for Anthropic's own models. Anthropic **does not officially
  support** pointing it at third-party gateways, so some features may break and
  routing Claude Code through OpenRouter is billed by OpenRouter, not your
  Anthropic subscription.
- CloudCLI is a third-party (AGPL-3) UI, not an official Anthropic product.
- This repo was assembled from the upstream images/recipes above; test it on your
  own server before relying on it.

## License

MIT (this repo). Bundled components keep their own licenses: CloudCLI UI is
AGPL-3.0-or-later; Docker's base image and Claude Code have their own terms.
