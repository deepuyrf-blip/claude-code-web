# Installation & Usage Guide

Everything you need to run this on a VPS and use it — install, access, provider
setup, models, plugins, troubleshooting.

---

## TL;DR — one command (fresh VPS)

On a brand-new server, as root:

```bash
sudo bash -c "$(curl -fsSL https://raw.githubusercontent.com/deepuyrf-blip/claude-code-web/main/quickstart.sh)"
```

It installs Docker + the whole stack, starts a Cloudflare tunnel, and prints your
URL + login. Open the URL and you're in.

---

## What you get

- **Claude Code** running on your server (installed in a container).
- **CloudCLI** web UI — chat, file explorer, git, built-in shell (mobile friendly).
- **Caddy** reverse proxy (automatic HTTPS if you set a domain, basic auth).
- **`/admin`** page — provider + API-key switcher, multi-key rotation, saved
  configs with 1-click switch.
- Extra agent CLIs: **Codex**, **OpenCode**, **Cursor** (each needs its own login).

---

## 1. One-click install (fresh VPS)

```bash
sudo bash -c "$(curl -fsSL https://raw.githubusercontent.com/deepuyrf-blip/claude-code-web/main/quickstart.sh)"
```

What it does: installs `git`/`curl` → clones the repo to `/opt/claude-code-web`
→ asks for your OpenRouter key → installs Docker → builds and starts everything
→ starts a Cloudflare quick tunnel → prints the URL.

Custom repo / dir / key:

```bash
CCW_REPO=https://github.com/you/fork  CCW_DIR=/opt/ccw  OPENROUTER_API_KEY=sk-or-... \
  sudo -E bash -c "$(curl -fsSL https://raw.githubusercontent.com/deepuyrf-blip/claude-code-web/main/quickstart.sh)"
```

## 2. Manual install

```bash
git clone https://github.com/deepuyrf-blip/claude-code-web
cd claude-code-web
chmod +x install.sh manage.sh set-password.sh quickstart.sh
./install.sh
```

Or non-interactive + tunnel:

```bash
OPENROUTER_API_KEY=sk-or-... ./install.sh --non-interactive --tunnel
```

Flags: `--non-interactive` (read from env, no prompts), `--tunnel` (start a
Cloudflare quick tunnel + print URL), `--reset` (regenerate `.env` / Caddyfile).

---

## 3. Open the app (URL)

You need to reach the server on port 80 (or 443 with a domain).

### a) Cloudflare tunnel (no firewall changes)

```bash
docker rm -f cf-tunnel 2>/dev/null
docker run -d --name cf-tunnel --restart unless-stopped --network host \
  cloudflare/cloudflared:latest tunnel --url http://localhost:80
sleep 6
docker logs cf-tunnel 2>&1 | grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' | tail -1
```

Open that `https://….trycloudflare.com` URL. Note: the quick-tunnel URL changes
if the container restarts (e.g. VPS reboot) — re-run the last command for a new one.

### b) Your own domain (permanent HTTPS)

Point a domain's A record at the server's public IP, then install with it:

```bash
DOMAIN=code.example.com ./install.sh --reset
```

Caddy fetches a Let's Encrypt cert automatically. URL: `https://code.example.com`.

### c) Direct IP + firewall

If you have no domain, the app is served on `http://<server-ip>`. You must open
port 80 in your cloud provider's firewall.

**Oracle Cloud (OCI):** Networking → Virtual Cloud Networks → your VCN → public
subnet → **Default Security List** → Add Ingress Rules:

- Source CIDR `0.0.0.0/0`, IP Protocol **TCP**, Destination Port **80**
- Source CIDR `0.0.0.0/0`, IP Protocol **TCP**, Destination Port **443**

Also check host iptables (Oracle images sometimes ship REJECT rules):

```bash
sudo iptables -L INPUT -n --line-numbers
# if 80/443 are rejected:
sudo iptables -I INPUT 6 -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT 6 -p tcp --dport 443 -j ACCEPT
sudo netfilter-persistent save
```

**Other providers (AWS/GCP/Azure/Hetzner…):** open inbound TCP 80/443 in the
security group / firewall panel. Check `sudo ufw status` too (`ufw allow 80`).

Quick test from the server:

```bash
curl -I http://localhost     # expect 200/302 (or 401 if basic auth is on)
```

---

## 4. Set your provider + API key (`/admin`)

Open **`<your-url>/admin`** (separate from CloudCLI — it bypasses CloudCLI's login).

1. **Provider** — pick one (or Custom).
2. **API Key (primary)** — paste the key.
3. **Extra API keys** — optional, one per line (see multi-key below).
4. **Base URL** — auto-filled by the preset; adjust for Custom.
5. **Test connection** → green dot = working.
6. **Load models** → pulls every model the provider offers.
7. **Save**.

### Provider base URLs

| Provider | Base URL | Key |
|---|---|---|
| OpenRouter | `https://openrouter.ai/api` | `sk-or-…` |
| OpenRouter (free) | `https://openrouter.ai/api` | free key |
| DeepSeek | `https://api.deepseek.com/anthropic` | DeepSeek key |
| Kimi / Moonshot | `https://api.moonshot.ai/anthropic` | Moonshot key |
| Z.ai (GLM) | `https://api.z.ai/api/anthropic` | Z.ai key |
| MiniMax | `https://api.minimax.io/anthropic` | MiniMax key |
| Radox AI | `https://radoxai.com` | `cc_…` |
| Anthropic (official) | `https://api.anthropic.com` | `sk-ant-…` |

> **Base URL gotcha:** for Anthropic-compatible endpoints Claude Code appends
> `/v1/messages` itself — so **don't** put `/v1` in the base URL. Radox, for
> example, is `https://radoxai.com` (not `…/v1`).

### Multiple API keys (share quota)

Put extra keys in the **Extra API keys** box (one per line). With 2+ keys,
Claude Code is routed through this service's built-in **rotating gateway**:

```
Claude Code  →  gateway (key 1, key 2, key 3 … round-robin)  →  provider
```

- Each request uses the next key.
- On `401/402/403/429` the gateway retries the next key automatically.
- One key = direct mode (fastest); 2+ keys = via gateway.

### Saved configs — 1-click switch

Every **Save** is stored in the page's **history**. The bottom of `/admin` shows
a **Saved configs** list, each with a green **Use** button. When one key's limit
runs out, just click **Use** on another — provider + base URL + keys + models are
restored instantly, no typing.

---

## 5. Models

- **Load models** fills the tier fields (Fable / Opus / Sonnet / Haiku) with the
  provider's models, and on **Save** writes them into Claude Code's `modelPicker`
  — so Claude Code's `/model` picker lists every model.
- In the **CloudCLI chat** dropdown you may not see all of them; type
  **`/model <model-id>`** in the chat, or pick a model in the **Shell** tab
  (`claude` → `/model` → Enter sets it as the default for new sessions).
- Pin all four tiers (Fable/Opus/Sonnet/Haiku) — an unpinned tier shows a
  "model may not exist" error.

---

## 6. Other agents (Codex / OpenCode / Cursor)

The container also has these CLIs installed. CloudCLI → **Settings → Agents** →
select → **Login**.

- **OpenCode** — provider-agnostic; can point at any OpenAI-compatible endpoint.
- **Codex** — needs the **Responses API**; some providers (chat-completions only)
  won't work.
- **Cursor** — uses Cursor's own account/backend; **cannot** use your custom key.

> Note: the `/admin` provider/key setup applies to **Claude**. Codex/OpenCode use
> their own configs.

---

## 7. Plugins

CloudCLI → **Settings → Plugins** → Install. (The image ships `typescript` so
plugin builds work, and `~/.claude-code-ui` is persisted.)

---

## 8. Graphify (codebase → knowledge graph)

Maps your project into a queryable graph so the agent queries it instead of
grepping files (saves tokens).

```bash
docker exec -it claude-code-web graphify install   # one time, registers the skill
```

Then in the chat: **`/graphify .`** → produces `graphify-out/` (graph.html,
GRAPH_REPORT.md, graph.json). Query with `graphify explain "X"`,
`graphify path A B`, `graphify query "…"`.

> Run it on a folder that has real content — an empty folder gives an empty graph.

---

## 9. Upgrade

```bash
cd /opt/claude-code-web
bash manage.sh upgrade     # rebuild claude-web --no-cache -> latest Claude Code + all CLIs
```

---

## 10. Troubleshooting

| Symptom | Fix |
|---|---|
| `E: Unable to locate package docker-model-plugin` | Old Ubuntu — `install.sh` falls back automatically; or `apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin` |
| Script dies silently / "docker daemon access" | Run as **root** (`sudo`). |
| `spawn cursor-agent ENOENT` in chat | Agent set to Cursor — switch back to **Claude** (Settings → Agents). |
| `unable to open database file` / 502 | After deleting `data/*`, re-create + chown: `mkdir -p data/cloudcli data/claude && chown -R 1000:1000 data` (then restart). |
| `402 This request requires more credits` | Provider credits/quota — add credits or use a free model. |
| `model may not exist` | Wrong model id, or base URL has an extra `/v1`. Check `/admin` + "Load models". |
| Plugin install "Internal server error" (`tsc: not found`) | Fixed in the image (typescript). Rebuild: `bash manage.sh update`. |
| Browser keeps asking basic-auth | Wrong creds or cached — use a private/incognito window; change the password with `bash set-password.sh`. |
| `ERR_TIMED_OUT` reaching the IP | Cloud firewall — open port 80 (see §3c). |
| 403 from a provider in `/admin` | Provider blocks library user-agents — the gateway already uses `curl`; ensure you rebuilt (`bash manage.sh update`). |

---

## 11. Command cheat sheet

```bash
# install (fresh VPS)
sudo bash -c "$(curl -fsSL https://raw.githubusercontent.com/deepuyrf-blip/claude-code-web/main/quickstart.sh)"

# day-to-day (in the repo dir)
bash manage.sh logs        # follow logs
bash manage.sh status      # container status
bash manage.sh restart     # restart containers
bash manage.sh stop        # stop
bash manage.sh update      # git pull + rebuild
bash manage.sh upgrade     # rebuild --no-cache (latest CLIs)

# change the web-UI basic-auth password
bash set-password.sh

# cloudflare tunnel
docker rm -f cf-tunnel 2>/dev/null
docker run -d --name cf-tunnel --restart unless-stopped --network host \
  cloudflare/cloudflared:latest tunnel --url http://localhost:80
docker logs cf-tunnel 2>&1 | grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' | tail -1

# health
docker compose ps
curl -I http://localhost
docker exec claude-code-web sh -c 'command -v claude codex opencode cursor-agent graphify'
```

---

## License

MIT (this repo). Bundled components keep their own licenses (CloudCLI UI is
AGPL-3.0-or-later; Docker's base image and Claude Code have their own terms).
