#!/usr/bin/env python3
"""
Provider / API-key switcher for the Claude Code web UI.

Serves a small web page (default at /admin) where you pick a provider and paste
an API key. It writes Claude Code's global settings file
(~/.claude/settings.json -> mounted here as $KEYMGR_DATA_DIR/settings.json)
with the matching `env` block, so *new* Claude Code sessions pick the new
provider up automatically - no container restart needed.

Also has a "Load models" button (queries the provider's /models endpoint and
fills the tier dropdowns) and a green/red connection indicator.

Outbound calls use `curl` when available (some providers sit behind a WAF that
rejects library user-agents), falling back to urllib. Stdlib only otherwise.
"""
import base64
import html
import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

DATA_DIR = os.environ.get("KEYMGR_DATA_DIR", "/data")
PORT = int(os.environ.get("KEYMGR_PORT", "8000"))
PASSWORD = os.environ.get("KEYMGR_PASSWORD", "")  # optional HTTP basic auth
BASE = "/admin"
USER_AGENT = os.environ.get("KEYMGR_USER_AGENT", "curl/8.5.0")

SETTINGS_PATH = os.path.join(DATA_DIR, "settings.json")
STATE_PATH = os.path.join(DATA_DIR, "keymanager.json")

# auth: "token" -> ANTHROPIC_AUTH_TOKEN (bearer), "apikey" -> ANTHROPIC_API_KEY
PROVIDERS = {
    "radoxai": {"label": "Radox AI (radoxai.com)", "base_url": "https://radoxai.com", "auth": "token",
                "fable": "claude-fable-5.1", "opus": "claude-opus-4.8", "sonnet": "claude-sonnet-5",
                "haiku": "deepseek-v4-flash-0731"},
    "openrouter_free": {"label": "OpenRouter - FREE models (no credits needed)",
                        "base_url": "https://openrouter.ai/api", "auth": "token",
                        "fable": "openrouter/free", "opus": "openrouter/free",
                        "sonnet": "openrouter/free", "haiku": "openrouter/free"},
    "openrouter": {"label": "OpenRouter (paid / your credits)",
                   "base_url": "https://openrouter.ai/api", "auth": "token",
                   "fable": "", "opus": "", "sonnet": "", "haiku": ""},
    "deepseek": {"label": "DeepSeek", "base_url": "https://api.deepseek.com/anthropic", "auth": "token",
                 "fable": "deepseek-v4-pro", "opus": "deepseek-v4-pro", "sonnet": "deepseek-v4-pro",
                 "haiku": "deepseek-v4-flash"},
    "moonshot": {"label": "Kimi / Moonshot", "base_url": "https://api.moonshot.ai/anthropic", "auth": "token",
                 "fable": "kimi-k2.7-code", "opus": "kimi-k2.7-code", "sonnet": "kimi-k2.7-code",
                 "haiku": "kimi-k2.5"},
    "zai": {"label": "Z.ai (GLM)", "base_url": "https://api.z.ai/api/anthropic", "auth": "token",
            "fable": "glm-5.2", "opus": "glm-5.2", "sonnet": "glm-5.2", "haiku": "glm-4.7"},
    "minimax": {"label": "MiniMax", "base_url": "https://api.minimax.io/anthropic", "auth": "token",
                "fable": "", "opus": "", "sonnet": "", "haiku": ""},
    "anthropic": {"label": "Anthropic (official)", "base_url": "https://api.anthropic.com", "auth": "apikey",
                  "fable": "", "opus": "", "sonnet": "", "haiku": ""},
    "custom": {"label": "Custom (Anthropic-compatible)", "base_url": "", "auth": "token",
               "fable": "", "opus": "", "sonnet": "", "haiku": ""},
}

TIERS = ("fable", "opus", "sonnet", "haiku")

MANAGED_KEYS = [
    "ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY",
    "ANTHROPIC_DEFAULT_FABLE_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY",
]


# --------------------------------------------------------------------------- #
# settings / state
# --------------------------------------------------------------------------- #
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
    st.setdefault("provider", "radoxai")
    st.setdefault("key", "")
    st.setdefault("base_url", PROVIDERS.get(st["provider"], PROVIDERS["radoxai"])["base_url"])
    for k in TIERS:
        st.setdefault(k, "")
    st.setdefault("loaded_models", [])
    st.setdefault("loaded_provider", "")
    return st


def apply_config(provider_id, key, base_url, models, loaded_models=None, loaded_provider=""):
    prov = PROVIDERS.get(provider_id, PROVIDERS["custom"])
    state = {"provider": provider_id, "key": key, "base_url": base_url}
    state.update({t: models.get(t, "") for t in TIERS})
    state["loaded_models"] = list(loaded_models or [])
    state["loaded_provider"] = loaded_provider
    _write_json(STATE_PATH, state)

    env = {"ANTHROPIC_BASE_URL": base_url,
           "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY": "1"}
    if prov["auth"] == "apikey":
        env["ANTHROPIC_API_KEY"] = key
        env["ANTHROPIC_AUTH_TOKEN"] = ""
    else:
        env["ANTHROPIC_AUTH_TOKEN"] = key
        env["ANTHROPIC_API_KEY"] = ""
    for tier in TIERS:
        val = (models.get(tier) or "").strip()
        if val:
            env[f"ANTHROPIC_DEFAULT_{tier.upper()}_MODEL"] = val

    settings = _read_json(SETTINGS_PATH, {})
    block = settings.setdefault("env", {})
    for k in MANAGED_KEYS:
        block.pop(k, None)
    block.update(env)
    # Expand the /model picker with every model we loaded, so the chat's model
    # dropdown lists all of them (Claude Code's `modelPicker` setting).
    if loaded_models and loaded_provider == provider_id:
        settings["modelPicker"] = {"options": [{"model": m} for m in loaded_models]}
    else:
        settings.pop("modelPicker", None)
    _write_json(SETTINGS_PATH, settings)
    return state


def mask(key):
    if not key:
        return ""
    if len(key) <= 8:
        return "*" * len(key)
    return key[:4] + "*" * (len(key) - 8) + key[-4:]


# --------------------------------------------------------------------------- #
# HTTP (curl preferred, urllib fallback)
# --------------------------------------------------------------------------- #
def _http(url, headers, method="GET", body=None, timeout=20):
    """Return (status_code, text). status 0 = transport error."""
    if shutil.which("curl"):
        args = ["curl", "-sS", "--max-time", str(timeout), "-X", method,
                "-A", USER_AGENT, "-w", "\n__HTTP__%{http_code}", url]
        for k, v in headers.items():
            args += ["-H", f"{k}: {v}"]
        if body is not None:
            args += ["-H", "content-type: application/json", "--data-binary", json.dumps(body)]
        try:
            p = subprocess.run(args, capture_output=True, text=True, timeout=timeout + 5)
        except Exception as e:
            return 0, f"curl error: {e}"
        out, code = p.stdout, 0
        if "__HTTP__" in out:
            out, _, c = out.rpartition("__HTTP__")
            c = c.strip()
            if c.isdigit():
                code = int(c)
        return code, out

    data = json.dumps(body).encode() if body is not None else None
    h = dict(headers)
    h.setdefault("User-Agent", USER_AGENT)
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return 0, type(e).__name__


def _safe_json(text):
    try:
        return json.loads(text)
    except Exception:
        return {}


def _strip_suffixes(b):
    for suf in ("/v1", "/anthropic", "/api/anthropic", "/api/coding", "/api"):
        if b.endswith(suf):
            return b[: -len(suf)]
    return b


def candidate_model_urls(base_url):
    b = base_url.rstrip("/")
    stripped = _strip_suffixes(b)
    urls = []
    for root in (b, stripped):
        for suffix in ("/models", "/v1/models"):
            u = root + suffix
            if u not in urls:
                urls.append(u)
    return urls


def _auth_headers(key):
    return [
        {"Authorization": "Bearer " + key, "anthropic-version": "2023-06-01", "accept": "application/json"},
        {"x-api-key": key, "anthropic-version": "2023-06-01", "accept": "application/json"},
    ]


def _extract_ids(data):
    items = None
    if isinstance(data, dict):
        for k in ("data", "models", "result", "model"):
            if isinstance(data.get(k), list):
                items = data[k]
                break
    elif isinstance(data, list):
        items = data
    if not items:
        return []
    out = []
    for it in items:
        if isinstance(it, dict):
            mid = it.get("id") or it.get("name") or it.get("model")
            if mid:
                out.append(str(mid))
        elif isinstance(it, str):
            out.append(it)
    return out


def fetch_models(base_url, key, timeout=20):
    tried = []
    for url in candidate_model_urls(base_url):
        for headers in _auth_headers(key):
            code, text = _http(url, headers, "GET", timeout=timeout)
            if 200 <= code < 300:
                ids = _extract_ids(_safe_json(text))
                if ids:
                    return {"ok": True, "url": url, "models": ids, "tried": tried}
                tried.append(f"{url} -> HTTP {code} (no models in response)")
            else:
                tried.append(f"{url} -> HTTP {code}")
    return {"ok": False, "models": [], "tried": tried}


def test_connection(base_url, key, model=""):
    m = fetch_models(base_url, key)
    if m["ok"]:
        return {"ok": True, "how": "models endpoint", "detail": f"{len(m['models'])} models found",
                "tried": m["tried"]}

    b = base_url.rstrip("/")
    payload = {"model": (model or "claude-sonnet-5"), "max_tokens": 1,
               "messages": [{"role": "user", "content": "hi"}]}
    for url in (b + "/v1/messages", b + "/messages"):
        for headers in _auth_headers(key):
            code, _ = _http(url, headers, "POST", payload)
            if 200 <= code < 300:
                return {"ok": True, "how": "inference", "detail": url, "tried": m["tried"]}
            m["tried"].append(f"POST {url} -> HTTP {code}")
    return {"ok": False, "tried": m["tried"]}


# --------------------------------------------------------------------------- #
# page
# --------------------------------------------------------------------------- #
HTML = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Claude Code - Provider &amp; Key</title>
<style>
 body{font-family:system-ui,Segoe UI,Roboto,sans-serif;margin:0;background:#0f1117;color:#e6e8ee}
 .wrap{max-width:620px;margin:0 auto;padding:24px 16px 48px}
 h1{font-size:20px;margin:0 0 4px}
 p.sub{color:#9aa3b2;font-size:13px;margin:0 0 18px}
 label{display:block;font-size:13px;color:#c3cad6;margin:14px 0 6px}
 select,input{width:100%;box-sizing:border-box;padding:11px 12px;border-radius:10px;
   border:1px solid #2a3040;background:#161a24;color:#e6e8ee;font-size:15px}
 .btn{margin-top:20px;width:100%;padding:13px;border:0;border-radius:10px;
   background:#3b6ef6;color:#fff;font-size:16px;font-weight:600}
 .row2{display:flex;gap:10px;margin-top:14px}
 .row2 button{flex:1;padding:11px;border-radius:10px;border:1px solid #2a3040;
   background:#161a24;color:#e6e8ee;font-size:14px;font-weight:600}
 .ok{background:#123524;border:1px solid #1f6f45;color:#8ff0bd;padding:10px 12px;border-radius:10px;font-size:13px;margin-bottom:14px}
 .err{background:#3a1620;border:1px solid #8a2b3f;color:#ffb3c1;padding:10px 12px;border-radius:10px;font-size:13px;margin-bottom:14px}
 .cur{color:#9aa3b2;font-size:12px;margin-top:4px}
 .tiers{display:grid;grid-template-columns:1fr 1fr;gap:10px}
 .status{display:flex;align-items:center;gap:8px;margin-top:16px;font-size:14px}
 .dot{width:12px;height:12px;border-radius:50%;background:#7a8496;flex:0 0 auto}
 .dot.green{background:#2ecc71;box-shadow:0 0 8px #2ecc71}
 .dot.red{background:#e74c3c;box-shadow:0 0 8px #e74c3c}
 code{background:#161a24;padding:2px 6px;border-radius:6px;font-size:12px}
 .tried{color:#9aa3b2;font-size:11px;margin-top:6px;white-space:pre-wrap;word-break:break-all}
</style></head><body><div class="wrap">
<h1>Provider &amp; API Key</h1>
<p class="sub">Pick a provider, paste the key, test the connection, load its models, then Save.</p>
__BANNER__
<form method="post" action="__BASE__/save">
  <label>Provider</label>
  <select name="provider" id="provider" onchange="fill()">__PROVIDER_OPTS__</select>

  <label>API Key</label>
  <input name="key" id="key" placeholder="paste the key here" value="__KEY__" autocomplete="off">
  <div class="cur">current: __KEY_CUR__</div>

  <label>Base URL</label>
  <input name="base_url" id="base_url" value="__BASE_URL__">

  <div class="row2">
    <button type="button" onclick="testConn()">Test connection</button>
    <button type="button" onclick="loadModels()">Load models</button>
  </div>
  <div class="status"><span class="dot" id="dot"></span><span id="statusText">Not tested</span></div>
  <div class="tried" id="tried"></div>

  <label>Models per tier (Fable / Opus / Sonnet / Haiku) - "Load models" fills these dropdowns</label>
  <p class="sub" style="margin:2px 0 8px">Tip: "Load models" + Save karne se saare models chat ke model picker me bhi aa jayenge (koi bhi model choose kar sakoge).</p>
  <div class="tiers">__TIER_INPUTS__</div>
  <datalist id="modelList"></datalist>

  <button class="btn" type="submit">Save</button>
</form>
<p class="sub" style="margin-top:22px">Writes to <code>__SETTINGS_PATH__</code>.
Bearer providers go to <code>ANTHROPIC_AUTH_TOKEN</code>; official Anthropic uses <code>ANTHROPIC_API_KEY</code> - handled automatically.</p>
</div>
<script>
const BASE = "__BASE__";
const PROVIDERS = __PROVIDERS_JSON__;
const TIERS = ["fable","opus","sonnet","haiku"];

function fill(){
  const p = PROVIDERS[document.getElementById('provider').value] || {};
  document.getElementById('base_url').value = p.base_url || '';
  TIERS.forEach(function(t){ document.getElementById(t).value = p[t] || ''; });
  setStatus(null, 'Not tested'); document.getElementById('tried').textContent = '';
}
function setStatus(ok, msg){
  const dot = document.getElementById('dot');
  dot.className = 'dot' + (ok === true ? ' green' : ok === false ? ' red' : '');
  document.getElementById('statusText').textContent = msg;
}
async function callApi(path){
  const base_url = document.getElementById('base_url').value.trim();
  const key = document.getElementById('key').value.trim();
  const model = document.getElementById('opus').value.trim();
  const provider = document.getElementById('provider').value;
  const url = BASE + path + '?base_url=' + encodeURIComponent(base_url)
            + '&key=' + encodeURIComponent(key) + '&model=' + encodeURIComponent(model)
            + '&provider=' + encodeURIComponent(provider);
  const r = await fetch(url);
  return await r.json();
}
async function testConn(){
  setStatus(null, 'Testing...'); document.getElementById('tried').textContent = '';
  try{
    const d = await callApi('/api/test');
    if(d.ok){ setStatus(true, 'Connected \\u2713 (' + (d.how || '') + ')'); }
    else { setStatus(false, 'Connection failed'); document.getElementById('tried').textContent = (d.tried||[]).join('\\n'); }
  }catch(e){ setStatus(false, 'Error: ' + e); }
}
async function loadModels(){
  setStatus(null, 'Loading models...'); document.getElementById('tried').textContent = '';
  try{
    const d = await callApi('/api/models');
    if(d.ok){
      const dl = document.getElementById('modelList');
      dl.innerHTML = '';
      d.models.forEach(function(m){ const o=document.createElement('option'); o.value=m; dl.appendChild(o); });
      setStatus(true, 'Connected \\u2713 - ' + d.models.length + ' models loaded');
      document.getElementById('tried').textContent = 'from ' + d.url + ' - ab Save karo, saare models chat ke model picker me aa jayenge.';
    } else {
      setStatus(false, 'Could not load models');
      document.getElementById('tried').textContent = (d.tried||[]).join('\\n');
    }
  }catch(e){ setStatus(false, 'Error: ' + e); }
}
</script></body></html>"""


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
    tier_inputs = "".join(
        f'<div><input name="{t}" id="{t}" list="modelList" placeholder="{t}" '
        f'value="{html.escape(state.get(t, ""))}"></div>'
        for t in TIERS
    )
    return (HTML
            .replace("__BANNER__", banner)
            .replace("__PROVIDER_OPTS__", "".join(opts))
            .replace("__KEY__", html.escape(state.get("key", "")))
            .replace("__KEY_CUR__", html.escape(mask(state.get("key", ""))) or "(none saved yet)")
            .replace("__BASE_URL__", html.escape(state.get("base_url", "")))
            .replace("__TIER_INPUTS__", tier_inputs)
            .replace("__SETTINGS_PATH__", html.escape(SETTINGS_PATH))
            .replace("__PROVIDERS_JSON__", json.dumps(PROVIDERS))
            .replace("__BASE__", BASE))


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

    def _json(self, obj):
        self._send(200, json.dumps(obj), "application/json")

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        if path == "/":
            self.send_response(302)
            self.send_header("Location", BASE)
            self.end_headers()
            return

        if path in (BASE + "/api/models", BASE + "/api/test"):
            if not self._authorized():
                return self._deny()
            q = parse_qs(parsed.query)
            base_url = (q.get("base_url", [""])[0] or "").strip()
            key = (q.get("key", [""])[0] or "").strip()
            model = (q.get("model", [""])[0] or "").strip()
            if not base_url:
                return self._json({"ok": False, "models": [], "tried": ["base_url missing"]})
            if path.endswith("/test"):
                return self._json(test_connection(base_url, key, model))
            result = fetch_models(base_url, key)
            if result["ok"]:
                st = load_state()
                st["loaded_models"] = result["models"]
                st["loaded_provider"] = (q.get("provider", [""])[0] or "").strip()
                _write_json(STATE_PATH, st)
            return self._json(result)

        if path != BASE:
            self._send(404, "not found")
            return
        if not self._authorized():
            return self._deny()
        q = parse_qs(parsed.query)
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
        st = load_state()
        apply_config(provider, key, base_url, {t: get(t) for t in TIERS},
                     loaded_models=st.get("loaded_models", []),
                     loaded_provider=st.get("loaded_provider", ""))
        self.send_response(302)
        self.send_header("Location", BASE + "?saved=1")
        self.end_headers()

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    os.makedirs(DATA_DIR, exist_ok=True)
    print(f"[keymgr] serving on :{PORT}{BASE} (data dir {DATA_DIR})", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
