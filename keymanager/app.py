#!/usr/bin/env python3
"""
Provider / API-key switcher for the Claude Code web UI.

Serves a small web page (default at /admin) where you pick a provider and paste
an API key. It writes Claude Code's global settings file
(~/.claude/settings.json -> mounted here as $KEYMGR_DATA_DIR/settings.json)
with the matching `env` block, so *new* Claude Code sessions pick the new
provider up automatically - no container restart needed.

Stdlib only, so it runs on a plain python:3-slim image.
"""
import base64
import html
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

DATA_DIR = os.environ.get("KEYMGR_DATA_DIR", "/data")
PORT = int(os.environ.get("KEYMGR_PORT", "8000"))
PASSWORD = os.environ.get("KEYMGR_PASSWORD", "")  # optional HTTP basic auth
BASE = "/admin"

SETTINGS_PATH = os.path.join(DATA_DIR, "settings.json")
STATE_PATH = os.path.join(DATA_DIR, "keymanager.json")

# auth: "token" -> ANTHROPIC_AUTH_TOKEN (bearer), "apikey" -> ANTHROPIC_API_KEY
PROVIDERS = {
    "openrouter": {"label": "OpenRouter", "base_url": "https://openrouter.ai/api", "auth": "token",
                   "opus": "", "sonnet": "", "haiku": ""},
    "deepseek": {"label": "DeepSeek", "base_url": "https://api.deepseek.com/anthropic", "auth": "token",
                 "opus": "deepseek-v4-pro", "sonnet": "deepseek-v4-pro", "haiku": "deepseek-v4-flash"},
    "moonshot": {"label": "Kimi / Moonshot", "base_url": "https://api.moonshot.ai/anthropic", "auth": "token",
                 "opus": "kimi-k2.7-code", "sonnet": "kimi-k2.7-code", "haiku": "kimi-k2.5"},
    "zai": {"label": "Z.ai (GLM)", "base_url": "https://api.z.ai/api/anthropic", "auth": "token",
            "opus": "glm-5.2", "sonnet": "glm-5.2", "haiku": "glm-4.7"},
    "minimax": {"label": "MiniMax", "base_url": "https://api.minimax.io/anthropic", "auth": "token",
                "opus": "", "sonnet": "", "haiku": ""},
    "anthropic": {"label": "Anthropic (official)", "base_url": "https://api.anthropic.com", "auth": "apikey",
                  "opus": "", "sonnet": "", "haiku": ""},
    "custom": {"label": "Custom (Anthropic-compatible)", "base_url": "", "auth": "token",
               "opus": "", "sonnet": "", "haiku": ""},
}

MANAGED_KEYS = [
    "ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY",
    "ANTHROPIC_DEFAULT_OPUS_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL", "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY",
]


def _read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _write_json(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def load_state():
    st = _read_json(STATE_PATH, {})
    st.setdefault("provider", "openrouter")
    st.setdefault("key", "")
    st.setdefault("base_url", PROVIDERS.get(st["provider"], PROVIDERS["openrouter"])["base_url"])
    for k in ("opus", "sonnet", "haiku"):
        st.setdefault(k, "")
    return st


def apply_config(provider_id, key, base_url, opus, sonnet, haiku):
    """Write both the display state and Claude Code's settings.json."""
    prov = PROVIDERS.get(provider_id, PROVIDERS["custom"])
    state = {"provider": provider_id, "key": key, "base_url": base_url,
             "opus": opus, "sonnet": sonnet, "haiku": haiku}
    _write_json(STATE_PATH, state)

    env = {"ANTHROPIC_BASE_URL": base_url,
           "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY": "1"}
    if prov["auth"] == "apikey":
        env["ANTHROPIC_API_KEY"] = key
        env["ANTHROPIC_AUTH_TOKEN"] = ""
    else:
        env["ANTHROPIC_AUTH_TOKEN"] = key
        env["ANTHROPIC_API_KEY"] = ""
    for tier, val in (("OPUS", opus), ("SONNET", sonnet), ("HAIKU", haiku)):
        if val:
            env[f"ANTHROPIC_DEFAULT_{tier}_MODEL"] = val

    settings = _read_json(SETTINGS_PATH, {})
    block = settings.setdefault("env", {})
    for k in MANAGED_KEYS:
        block.pop(k, None)
    block.update(env)
    _write_json(SETTINGS_PATH, settings)
    return state


def mask(key):
    if not key:
        return ""
    if len(key) <= 8:
        return "*" * len(key)
    return key[:4] + "*" * (len(key) - 8) + key[-4:]


def page(state, saved=False, error=""):
    opts = []
    for pid, p in PROVIDERS.items():
        sel = " selected" if pid == state["provider"] else ""
        opts.append(f'<option value="{pid}"{sel}>{html.escape(p["label"])}</option>')
    banner = ""
    if saved:
        banner = '<div class="ok">Saved. New Claude Code sessions will use this provider.</div>'
    if error:
        banner = f'<div class="err">{html.escape(error)}</div>'
    cur = html.escape(mask(state.get("key", "")))
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Claude Code - Provider &amp; Key</title>
<style>
 body{{font-family:system-ui,Segoe UI,Roboto,sans-serif;margin:0;background:#0f1117;color:#e6e8ee}}
 .wrap{{max-width:560px;margin:0 auto;padding:24px 16px 48px}}
 h1{{font-size:20px;margin:0 0 4px}}
 p.sub{{color:#9aa3b2;font-size:13px;margin:0 0 20px}}
 label{{display:block;font-size:13px;color:#c3cad6;margin:14px 0 6px}}
 select,input{{width:100%;box-sizing:border-box;padding:11px 12px;border-radius:10px;
   border:1px solid #2a3040;background:#161a24;color:#e6e8ee;font-size:15px}}
 button{{margin-top:22px;width:100%;padding:13px;border:0;border-radius:10px;
   background:#3b6ef6;color:#fff;font-size:16px;font-weight:600}}
 .ok{{background:#123524;border:1px solid #1f6f45;color:#8ff0bd;padding:10px 12px;border-radius:10px;font-size:13px;margin-bottom:14px}}
 .err{{background:#3a1620;border:1px solid #8a2b3f;color:#ffb3c1;padding:10px 12px;border-radius:10px;font-size:13px;margin-bottom:14px}}
 .cur{{color:#9aa3b2;font-size:12px;margin-top:4px}}
 .row{{display:flex;gap:10px}}
 .row>div{{flex:1}}
 code{{background:#161a24;padding:2px 6px;border-radius:6px;font-size:12px}}
</style></head><body><div class="wrap">
<h1>Provider &amp; API Key</h1>
<p class="sub">Pick a provider, paste the key, Save. New Claude Code sessions use it immediately.</p>
{banner}
<form method="post" action="{BASE}/save">
  <label>Provider</label>
  <select name="provider" id="provider" onchange="fill()">{''.join(opts)}</select>
  <label>API Key</label>
  <input name="key" id="key" placeholder="paste the key here" value="{cur}">
  <div class="cur">current: {cur or '(none saved yet)'}</div>
  <label>Base URL</label>
  <input name="base_url" id="base_url" value="{html.escape(state['base_url'])}">
  <label>Models (optional - leave blank to use defaults)</label>
  <div class="row">
    <div><input name="opus" id="opus" placeholder="opus tier" value="{html.escape(state['opus'])}"></div>
    <div><input name="sonnet" id="sonnet" placeholder="sonnet tier" value="{html.escape(state['sonnet'])}"></div>
    <div><input name="haiku" id="haiku" placeholder="haiku tier" value="{html.escape(state['haiku'])}"></div>
  </div>
  <button type="submit">Save</button>
</form>
<p class="sub" style="margin-top:22px">Writes to <code>{html.escape(SETTINGS_PATH)}</code>.
If a provider needs the key in a different field (official Anthropic uses x-api-key), this page handles it automatically.</p>
</div>
<script>
const P = {json.dumps({k: v for k, v in PROVIDERS.items()})};
function fill(){{
  const p = P[document.getElementById('provider').value] || {{}};
  document.getElementById('base_url').value = p.base_url || '';
  document.getElementById('opus').value = p.opus || '';
  document.getElementById('sonnet').value = p.sonnet || '';
  document.getElementById('haiku').value = p.haiku || '';
}}
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    server_version = "keymgr"

    def _authorized(self):
        if not PASSWORD:
            return True
        hdr = self.headers.get("Authorization", "")
        if not hdr.startswith("Basic "):
            return False
        try:
            user, _, pw = base64.b64decode(hdr[6:]).decode().partition(":")
        except Exception:
            return False
        return pw == PASSWORD or user == PASSWORD

    def _deny(self):
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="key manager"')
        self.end_headers()

    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path == "/":
            self.send_response(302)
            self.send_header("Location", BASE)
            self.end_headers()
            return
        if path != BASE:
            self._send(404, "not found")
            return
        if not self._authorized():
            return self._deny()
        q = parse_qs(urlparse(self.path).query)
        self._send(200, page(load_state(), saved="saved" in q))

    def do_POST(self):
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path != BASE + "/save":
            self._send(404, "not found")
            return
        if not self._authorized():
            return self._deny()
        length = int(self.headers.get("Content-Length", "0"))
        form = parse_qs(self.rfile.read(length).decode("utf-8"))
        get = lambda k: (form.get(k, [""])[0] or "").strip()
        provider = get("provider") or "custom"
        key = get("key")
        base_url = get("base_url") or PROVIDERS.get(provider, {}).get("base_url", "")
        if not base_url:
            self._send(200, page(load_state(), error="Base URL khaali hai - provider choose karo ya URL daalo."))
            return
        apply_config(provider, key, base_url, get("opus"), get("sonnet"), get("haiku"))
        self.send_response(302)
        self.send_header("Location", BASE + "?saved=1")
        self.end_headers()

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    os.makedirs(DATA_DIR, exist_ok=True)
    print(f"[keymgr] serving on :{PORT}{BASE} (data dir {DATA_DIR})", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
