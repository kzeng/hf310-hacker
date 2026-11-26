"""Stdlib HTTP server for the HF310 status dashboard.

Refresh-driven: GET / never collects. POST /api/refresh collects (throttled)
and returns the snapshot. First paint embeds the last cached snapshot.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

BASE = Path(__file__).resolve().parent
DATA = Path(os.getenv("DASHBOARD_DATA_DIR", str(BASE / "data")))
CACHE = DATA / "snapshot.json"
TEMPLATES = BASE / "templates"
STATIC = BASE / "static"

MIN_REFRESH = int(os.getenv("DASHBOARD_MIN_REFRESH_SECONDS", "10"))
TTL = int(os.getenv("DASHBOARD_SESSION_TTL_SECONDS", str(7 * 86400)))
COOKIE_SECURE = os.getenv("DASHBOARD_COOKIE_SECURE", "1") != "0"
SESSION_COOKIE = "hf310_status_session"
PASSWORD_HASH = os.getenv("DASHBOARD_PASSWORD_HASH", "")
SESSION_SECRET = os.getenv("DASHBOARD_SESSION_SECRET", "")
PORT = int(os.getenv("DASHBOARD_PORT", "8082"))
HOST = os.getenv("DASHBOARD_HOST", "127.0.0.1")

_lock = threading.Lock()
_last_collect = 0.0

CONTENT_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".json": "application/json; charset=utf-8",
}

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    ),
}


def _decode_b64(value):
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def make_password_hash(password, iterations=200000):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    salt_b64 = base64.urlsafe_b64encode(salt).decode().rstrip("=")
    digest_b64 = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return "pbkdf2_sha256" + "$" + str(iterations) + "$" + salt_b64 + "$" + digest_b64


def verify_password(password, stored):
    try:
        scheme, iterations_raw, salt_raw, digest_raw = stored.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        expected = _decode_b64(digest_raw)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), _decode_b64(salt_raw), int(iterations_raw))
        return hmac.compare_digest(expected, actual)
    except Exception:
        return False


def _sign(body):
    mac = hmac.new(SESSION_SECRET.encode(), body, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).decode().rstrip("=")


def make_session():
    exp = int(time.time()) + TTL
    stamp = str(exp)
    return stamp + "." + _sign(stamp.encode())


def valid_session(token):
    if not token or not SESSION_SECRET:
        return False
    try:
        stamp, sig = token.split(".", 1)
        if int(stamp) < time.time():
            return False
        return hmac.compare_digest(sig, _sign(stamp.encode()))
    except Exception:
        return False


def load_cache():
    try:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_cache(snapshot):
    DATA.mkdir(parents=True, exist_ok=True)
    tmp = CACHE.with_name("snapshot.tmp")
    tmp.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, CACHE)


def collect_throttled():
    global _last_collect
    with _lock:
        now = time.time()
        if _last_collect and (now - _last_collect) < MIN_REFRESH:
            cached = load_cache()
            if cached:
                cached = dict(cached)
                cached["throttled"] = True
                return cached
        from collector import collect_snapshot
        snapshot = collect_snapshot()
        _last_collect = time.time()
        try:
            save_cache(snapshot)
        except Exception:
            pass
        return snapshot


def render(name, replacements):
    html = (TEMPLATES / name).read_text(encoding="utf-8")
    for key, value in replacements.items():
        html = html.replace(key, value)
    return html.encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    server_version = "hf310-dashboard"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _cookies(self):
        raw = self.headers.get("Cookie", "")
        out = {}
        for part in raw.split(";"):
            if "=" in part:
                k, v = part.strip().split("=", 1)
                out[k] = v
        return out

    def _authed(self):
        return valid_session(self._cookies().get(SESSION_COOKIE, ""))

    def _cookie_secure(self):
        if not COOKIE_SECURE:
            return False
        proto = self.headers.get("X-Forwarded-Proto", "").lower()
        return proto == "https" or self.headers.get("X-Forwarded-Ssl", "").lower() == "on"

    def _send(self, status, body=b"", ctype="text/html; charset=utf-8", extra=None, cache=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if cache:
            self.send_header("Cache-Control", cache)
        else:
            self.send_header("Cache-Control", "no-store")
        for key, value in SECURITY_HEADERS.items():
            self.send_header(key, value)
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _redirect(self, location, cookie=None):
        extra = {"Location": location}
        if cookie:
            extra["Set-Cookie"] = cookie
        self._send(303, b"", extra=extra)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/healthz":
            self._send(200, b"ok", "text/plain; charset=utf-8")
            return
        if path.startswith("/static/"):
            self._serve_static(path[len("/static/"):])
            return
        if path == "/login":
            if self._authed():
                self._redirect("/")
                return
            self._send(200, render("login.html", {"__ERROR_DISPLAY__": "none"}))
            return
        if path == "/":
            if not self._authed():
                self._redirect("/login")
                return
            cached = load_cache()
            payload = json.dumps(cached, ensure_ascii=False) if cached else "null"
            self._send(200, render("index.html", {"__SNAPSHOT_JSON__": payload}))
            return
        self._send(404, b"not found", "text/plain; charset=utf-8")

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        if path == "/login":
            form = parse_qs(raw.decode("utf-8", "replace"))
            password = (form.get("password") or [""])[0]
            if PASSWORD_HASH and verify_password(password, PASSWORD_HASH):
                cookie = "{}={}; Path=/; Max-Age={}; HttpOnly; SameSite=Strict{}".format(
                    SESSION_COOKIE, make_session(), TTL, "; Secure" if self._cookie_secure() else "")
                self._redirect("/", cookie=cookie)
            else:
                time.sleep(0.4)
                self._send(401, render("login.html", {"__ERROR_DISPLAY__": "block"}))
            return
        if path == "/logout":
            cookie = "{}={}; Path=/; Max-Age=0; HttpOnly; SameSite=Strict{}".format(
                SESSION_COOKIE, "", "; Secure" if self._cookie_secure() else "")
            self._redirect("/login", cookie=cookie)
            return
        if path == "/api/refresh":
            if not self._authed():
                self._send(401, json.dumps({"error": "unauthorized"}), "application/json; charset=utf-8")
                return
            try:
                snapshot = collect_throttled()
                self._send(200, json.dumps(snapshot, ensure_ascii=False), "application/json; charset=utf-8")
            except Exception as exc:
                self._send(500, json.dumps({"error": str(exc)}, ensure_ascii=False),
                           "application/json; charset=utf-8")
            return
        self._send(404, b"not found", "text/plain; charset=utf-8")

    def _serve_static(self, name):
        safe = name.split("?")[0]
        if "/" in safe or safe.startswith("."):
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        target = STATIC / safe
        if not target.is_file():
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        ctype = CONTENT_TYPES.get(target.suffix, "application/octet-stream")
        body = target.read_bytes()
        self._send(200, body, ctype, cache="public, max-age=31536000, immutable")


def main():
    DATA.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.daemon_threads = True
    print("hf310-dashboard listening on %s:%d" % (HOST, PORT), flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
