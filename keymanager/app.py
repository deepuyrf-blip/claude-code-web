#!/usr/bin/env python3
"""
Provider / API-key switcher + multi-key rotating gateway for the Claude Code web UI.

Page (at /admin): pick a provider, paste one or more API keys, test the
connection, load the provider's models, Save. Writes Claude Code's global
settings (~/.claude/settings.json -> $KEYMGR_DATA_DIR/settings.json).

Multi-key: if you give more than one key, Claude Code is pointed at this
service's built-in gateway (/gw) which round-robins the keys across requests and
retries the next key on 401/402/403/429 - so several keys share the load.

Outbound calls use `curl` when available (some providers sit behind a WAF that
rejects library user-agents), falling back to urllib.
"""
import base64
import html
import json
import os
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

DATA_DIR = os.environ.get("KEYMGR_DATA_DIR", "/data")
PORT = int(os.environ.get("KEYMGR_PORT", "8000"))
PASSWORD = os.environ.get("KEYMGR_PASSWORD", "")  # optional HTTP basic auth
BASE = "/admin"
PROXY_PREFIX = "/gw"
PROXY_URL = os.environ.get("KEYMGR_PROXY_URL", "http://key-manager:8000/gw")
USER_AGENT = os.environ.get("KEYMGR_USER_AGENT", "curl/8.5.0")
CODEX_DIR = os.environ.get("KEYMGR_CODEX_DIR", "")        # claude-web's ~/.codex
OPENCODE_DIR = os.environ.get("KEYMGR_OPENCODE_DIR", "")  # claude-web's ~/.config/opencode

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
    st.setdefault("keys", [])
    for k in TIERS:
        st.setdefault(k, "")
    st.setdefault("loaded_models", [])
    st.setdefault("loaded_provider", "")
    return st


# ---- saved-config history (for 1-click switching when a key runs out) ----
HISTORY_PATH = os.path.join(DATA_DIR, "history.json")


def load_history():
    h = _read_json(HISTORY_PATH, [])
    return h if isinstance(h, list) else []


def history_add(entry):
    h = load_history()
    sig = (entry.get("provider"), entry.get("base_url"), tuple(entry.get("keys", [])))
    h = [e for e in h if (e.get("provider"), e.get("base_url"), tuple(e.get("keys", []))) != sig]
    h.insert(0, entry)
    _write_json(HISTORY_PATH, h[:50])


def history_entry(state, provider_id):
    keys = [k for k in (state.get("keys") or []) if k]
    first = keys[0] if keys else ""
    label = f"{PROVIDERS.get(provider_id, {}).get('label', provider_id)} \u00b7 {mask(first) or '(no key)'}"
    return {
        "provider": provider_id,
        "base_url": state.get("base_url", ""),
        "keys": keys,
        "models": {t: state.get(t, "") for t in TIERS},
        "loaded_models": state.get("loaded_models", []),
        "label": label,
        "ts": int(time.time()),
    }


def apply_config(provider_id, key, base_url, models, extra_keys=None,
                 loaded_models=None, loaded_provider="", add_history=True):
    prov = PROVIDERS.get(provider_id, PROVIDERS["custom"])
    pool = [k.strip() for k in ([key] + list(extra_keys or [])) if k and k.strip()]
    use_proxy = len(pool) > 1

    state = {"provider": provider_id, "key": key, "base_url": base_url, "keys": pool,
             "use_proxy": use_proxy}
    state.update({t: models.get(t, "") for t in TIERS})
    state["loaded_models"] = list(loaded_models or [])
    state["loaded_provider"] = loaded_provider
    _write_json(STATE_PATH, state)
    if add_history:
        history_add(history_entry(state, provider_id))

    env = {"CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY": "1"}
    if use_proxy:
        # point Claude Code at our rotating gateway; the gateway injects real keys
        env["ANTHROPIC_BASE_URL"] = PROXY_URL
        env["ANTHROPIC_AUTH_TOKEN"] = "multi-key"
        env["ANTHROPIC_API_KEY"] = ""
    else:
        env["ANTHROPIC_BASE_URL"] = base_url
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
    if loaded_models and loaded_provider == provider_id:
        settings["modelPicker"] = {"options": [{"model": m} for m in loaded_models]}
    else:
        settings.pop("modelPicker", None)
    _write_json(SETTINGS_PATH, settings)
    return state


def _openai_base(base_url, use_proxy):
    if use_proxy:
        return PROXY_URL.rstrip("/") + "/v1"
    return base_url.rstrip("/") + "/v1"


def write_agent_configs(state):
    """Best-effort: point Codex and OpenCode at the same provider + key(s).
    Codex needs the Responses API (may not suit every provider); OpenCode uses
    the OpenAI chat-completions surface, which radox/freemodel speak."""
    base = state.get("base_url") or ""
    if not base:
        return
    keys = [k for k in (state.get("keys") or []) if k]
    token = "multi-key" if state.get("use_proxy") else (keys[0] if keys else "")
    obase = _openai_base(base, state.get("use_proxy"))
    models = state.get("loaded_models") or []
    model = state.get("opus") or state.get("sonnet") or (models[0] if models else "gpt-4o")

    if CODEX_DIR:
        try:
            os.makedirs(CODEX_DIR, exist_ok=True)
            toml = (
                f'model = "{model}"\n'
                f'model_provider = "ccw"\n\n'
                f'[model_providers.ccw]\n'
                f'name = "claude-code-web"\n'
                f'base_url = "{obase}"\n'
                f'wire_api = "chat"\n'
                f'experimental_bearer_token = "{token}"\n'
            )
            with open(os.path.join(CODEX_DIR, "config.toml"), "w", encoding="utf-8") as f:
                f.write(toml)
        except Exception:
            pass

    if OPENCODE_DIR:
        try:
            os.makedirs(OPENCODE_DIR, exist_ok=True)
            cfg = {
                "$schema": "https://opencode.ai/config.json",
                "provider": {
                    "ccw": {
                        "npm": "@ai-sdk/openai-compatible",
                        "name": "claude-code-web",
                        "options": {"baseURL": obase, "apiKey": token},
                        "models": {m: {"name": m} for m in (models or [model])},
                    }
                },
            }
            with open(os.path.join(OPENCODE_DIR, "opencode.json"), "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2)
        except Exception:
            pass


def mask(key):
    if not key:
        return ""
    if len(key) <= 8:
        return "*" * len(key)
    return key[:4] + "*" * (len(key) - 8) + key[-4:]


# --------------------------------------------------------------------------- #
# HTTP helpers (curl preferred, urllib fallback)
# --------------------------------------------------------------------------- #
def _http(url, headers, method="GET", body=None, timeout=20):
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
# rotating gateway (multi-key)
# --------------------------------------------------------------------------- #
_rot_lock = threading.Lock()
_rot_idx = 0


def _next_key(keys):
    global _rot_idx
    with _rot_lock:
        k = keys[_rot_idx % len(keys)]
        _rot_idx += 1
    return k


class Handler(BaseHTTPRequestHandler):
    server_version = "keymgr"
    protocol_version = "HTTP/1.1"

    # ---- auth ----
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
        self.send_header("Content-Length", "0")
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

    # ---- gateway ----
    def _proxy(self, method, parsed):
        sub = parsed.path[len(PROXY_PREFIX):] or "/"
        if parsed.query:
            sub += "?" + parsed.query
        length = int(self.headers.get("Content-Length", "0") or 0)
        body = self.rfile.read(length) if length else None

        st = load_state()
        base = (st.get("base_url") or "").rstrip("/")
        keys = [k for k in (st.get("keys") or []) if k]
        if not base or not keys:
            return self._send(502, "gateway: base_url / keys not configured")
        auth = PROVIDERS.get(st.get("provider", ""), {}).get("auth", "token")
        url = base + sub

        last = None
        for i in range(len(keys)):
            key = _next_key(keys)
            status, proc, hdrs = self._curl_headers(url, method, key, body, auth)
            if status is None:
                self._kill(proc)
                last = "transport"
                continue
            if status in (401, 402, 403, 429) and i < len(keys) - 1:
                self._kill(proc)
                last = status
                continue
            return self._relay(status, proc, hdrs)
        return self._send(502, f"gateway: all keys failed (last {last})")

    @staticmethod
    def _kill(proc):
        try:
            proc.kill()
        except Exception:
            pass

    def _curl_headers(self, url, method, key, body, auth, timeout=600):
        args = ["curl", "-sS", "--no-buffer", "--max-time", str(timeout), "-D", "-",
                "-X", method, url,
                "-H", ("x-api-key: " + key) if auth == "apikey" else ("Authorization: Bearer " + key),
                "-H", "anthropic-version: " + (self.headers.get("anthropic-version") or "2023-06-01"),
                "-H", "content-type: application/json"]
        acc = self.headers.get("accept")
        if acc:
            args += ["-H", "accept: " + acc]
        stdin = None
        if body is not None:
            args += ["--data-binary", "@-"]
            stdin = subprocess.PIPE
        proc = subprocess.Popen(args, stdin=stdin, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        if body is not None:
            try:
                proc.stdin.write(body)
                proc.stdin.close()
            except Exception:
                pass
        status, hdrs = None, []
        try:
            while True:
                line = proc.stdout.readline()
                if not line:
                    break
                s = line.decode("latin-1")
                if s in ("\r\n", "\n"):
                    break
                if s.upper().startswith("HTTP/"):
                    parts = s.split()
                    if len(parts) >= 2 and parts[1].isdigit():
                        status = int(parts[1])
                elif ":" in s:
                    hdrs.append(s.strip())
        except Exception:
            pass
        return status, proc, hdrs

    def _relay(self, status, proc, hdrs):
        self.send_response(status)
        for h in hdrs:
            name, _, val = h.partition(":")
            if name.strip().lower() in ("transfer-encoding", "content-length", "connection"):
                continue
            self.send_header(name.strip(), val.strip())
        self.send_header("Connection", "close")
        self.close_connection = True
        self.end_headers()
        try:
            while True:
                chunk = proc.stdout.read(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except Exception:
            pass
        finally:
            self._kill(proc)

    # ---- routes ----
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        if path == PROXY_PREFIX or path.startswith(PROXY_PREFIX + "/"):
            return self._proxy("GET", parsed)

        if path == "/":
            self.send_response(302)
            self.send_header("Location", BASE)
            self.send_header("Content-Length", "0")
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
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        if path == PROXY_PREFIX or path.startswith(PROXY_PREFIX + "/"):
            return self._proxy("POST", parsed)

        if path == BASE + "/use":
            if not self._authorized():
                return self._deny()
            length = int(self.headers.get("Content-Length", "0"))
            form = parse_qs(self.rfile.read(length).decode("utf-8"))
            try:
                idx = int((form.get("idx", [""])[0] or "").strip())
            except Exception:
                idx = -1
            h = load_history()
            if 0 <= idx < len(h):
                e = h[idx]
                ks = [k for k in (e.get("keys") or []) if k]
                apply_config(e.get("provider", "custom"), ks[0] if ks else "",
                             e.get("base_url", ""), e.get("models", {}),
                             extra_keys=ks[1:], loaded_models=e.get("loaded_models", []),
                             loaded_provider=e.get("provider", ""), add_history=False)
            self.send_response(302)
            self.send_header("Location", BASE + "?saved=1")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

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
        extra = [ln.strip() for ln in get("extra_keys").splitlines() if ln.strip()]
        base_url = get("base_url") or PROVIDERS.get(provider, {}).get("base_url", "")
        if not base_url:
            self._send(200, page(load_state(), error="Base URL khaali hai - provider choose karo ya URL daalo."))
            return
        st = load_state()
        apply_config(provider, key, base_url, {t: get(t) for t in TIERS},
                     extra_keys=extra,
                     loaded_models=st.get("loaded_models", []),
                     loaded_provider=st.get("loaded_provider", ""))
        self.send_response(302)
        self.send_header("Location", BASE + "?saved=1")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


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
 select,input,textarea{width:100%;box-sizing:border-box;padding:11px 12px;border-radius:10px;
   border:1px solid #2a3040;background:#161a24;color:#e6e8ee;font-size:15px;font-family:inherit}
 textarea{min-height:84px;resize:vertical}
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
 .hrow{display:flex;align-items:center;gap:8px;margin:6px 0}
 .hlab{flex:1;font-size:13px;color:#c3cad6;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
 .hbtn{padding:8px 16px;border-radius:8px;border:0;background:#2ecc71;color:#04301c;font-weight:700}
</style></head><body><div class="wrap">
<h1>Provider &amp; API Key</h1>
<p class="sub">Pick a provider, paste key(s), test, load models, Save.</p>
__BANNER__
<form method="post" action="__BASE__/save">
  <label>Provider</label>
  <select name="provider" id="provider" onchange="fill()">__PROVIDER_OPTS__</select>

  <label>API Key (primary)</label>
  <input name="key" id="key" placeholder="paste the key here" value="__KEY__" autocomplete="off">
  <div class="cur">current: __KEY_CUR__</div>

  <label>Extra API keys (optional - one per line, for rotation)</label>
  <textarea name="extra_keys" id="extra_keys" placeholder="key 2&#10;key 3">__EXTRA_KEYS__</textarea>
  <div class="cur">__KEYS_NOTE__</div>

  <label>Base URL</label>
  <input name="base_url" id="base_url" value="__BASE_URL__">

  <div class="row2">
    <button type="button" onclick="testConn()">Test connection</button>
    <button type="button" onclick="loadModels()">Load models</button>
  </div>
  <div class="status"><span class="dot" id="dot"></span><span id="statusText">Not tested</span></div>
  <div class="tried" id="tried"></div>

  <label>Models per tier (Fable / Opus / Sonnet / Haiku) - "Load models" fills these dropdowns</label>
  <p class="sub" style="margin:2px 0 8px">Tip: "Load models" + Save se saare models chat ke model picker me bhi aa jayenge.</p>
  <div class="tiers">__TIER_INPUTS__</div>
  <datalist id="modelList"></datalist>

  <button class="btn" type="submit">Save</button>
</form>
__HISTORY__
<p class="sub" style="margin-top:22px">Writes to <code>__SETTINGS_PATH__</code>.
2+ keys do to Claude Code apne rotating gateway se chalega (keys baari-baari, quota share).</p>
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
    keys = state.get("keys") or []
    extra = "\n".join(keys[1:]) if len(keys) > 1 else ""
    if len(keys) > 1:
        note = f"{len(keys)} keys - rotating gateway ON (Claude Code -> this service -> provider)"
    else:
        note = "1 key - direct mode (add more lines above to rotate)"
    hist = load_history()
    if hist:
        rows = []
        for i, e in enumerate(hist):
            rows.append(
                f'<form method="post" action="{BASE}/use" class="hrow">'
                f'<input type="hidden" name="idx" value="{i}">'
                f'<span class="hlab">{html.escape(e.get("label", ""))}</span>'
                f'<button type="submit" class="hbtn">Use</button></form>'
            )
        history_html = ('<label>Saved configs - 1-click switch (jab ek key ka limit khatam ho)</label>'
                        + "".join(rows))
    else:
        history_html = ""
    return (HTML
            .replace("__HISTORY__", history_html)
            .replace("__BANNER__", banner)
            .replace("__PROVIDER_OPTS__", "".join(opts))
            .replace("__KEY__", html.escape(state.get("key", "")))
            .replace("__KEY_CUR__", html.escape(mask(state.get("key", ""))) or "(none saved yet)")
            .replace("__EXTRA_KEYS__", html.escape(extra))
            .replace("__KEYS_NOTE__", html.escape(note))
            .replace("__BASE_URL__", html.escape(state.get("base_url", "")))
            .replace("__TIER_INPUTS__", tier_inputs)
            .replace("__SETTINGS_PATH__", html.escape(SETTINGS_PATH))
            .replace("__PROVIDERS_JSON__", json.dumps(PROVIDERS))
            .replace("__BASE__", BASE))


if __name__ == "__main__":
    os.makedirs(DATA_DIR, exist_ok=True)
    print(f"[keymgr] serving on :{PORT}{BASE} (data dir {DATA_DIR})", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
