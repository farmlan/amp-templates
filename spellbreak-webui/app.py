#!/usr/bin/env python3
"""Spellbreak LAN web UI: join page, live status and PIN-protected admin
controls for an AMP-managed Spellbreak instance. Python stdlib only."""
import hashlib, hmac, json, os, secrets, threading, time, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
CFG = {
    "AMP_URL": os.environ.get("AMP_URL", "http://127.0.0.1:8080").rstrip("/"),
    "AMP_USER": os.environ.get("AMP_USER", ""),
    "AMP_PASS": os.environ.get("AMP_PASS", ""),
    "AMP_INSTANCE": os.environ.get("AMP_INSTANCE", "Spellbreak01"),
    "ADMIN_PIN": os.environ.get("ADMIN_PIN", ""),
    "SERVER_ADDR": os.environ.get("SERVER_ADDR", "192.168.5.15"),
    "LISTEN": os.environ.get("LISTEN", "0.0.0.0"),
    "PORT": int(os.environ.get("PORT", "8095")),
}
SECRET = secrets.token_bytes(32)          # admin tokens reset on restart
STATES = {-1: "Unknown", 0: "Offline", 5: "Pre-start", 7: "Configuring", 10: "Starting",
          20: "Online", 30: "Restarting", 40: "Stopping", 45: "Preparing to sleep",
          50: "Sleeping", 60: "Waiting", 70: "Installing", 75: "Updating",
          80: "Awaiting input", 100: "Failed", 200: "Suspended", 250: "Maintenance"}
MODES = ["Solo", "Duo", "Squad", "Capture"]   # Capture = Dominion


class AMP:
    """Minimal AMP API client that talks to one instance through the ADS proxy."""
    def __init__(self):
        self.lock = threading.Lock()
        self.ads_sid = None
        self.inst_id = None
        self.inst_sid = None
        self.port = None
        self.nodes = {}

    def _post(self, path, data, sid=None):
        body = dict(data or {})
        if sid:
            body["SESSIONID"] = sid
        req = urllib.request.Request(CFG["AMP_URL"] + path, json.dumps(body).encode(),
                                     {"Content-Type": "application/json", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read().decode() or "null"
        out = json.loads(raw)
        if isinstance(out, dict) and out.get("Title") == "Unauthorized Access":
            raise PermissionError("session expired")
        return out

    def _login(self):
        creds = {"username": CFG["AMP_USER"], "password": CFG["AMP_PASS"], "token": "", "rememberMe": False}
        r = self._post("/API/Core/Login", creds)
        if not r.get("success"):
            raise RuntimeError("AMP login failed - check AMP_USER / AMP_PASS")
        self.ads_sid = r["sessionID"]
        targets = self._post("/API/ADSModule/GetInstances", {}, self.ads_sid)
        targets = targets.get("result", targets) if isinstance(targets, dict) else targets
        for t in targets or []:
            for i in t.get("AvailableInstances", []):
                if CFG["AMP_INSTANCE"].lower() in (i.get("InstanceName", "").lower(), i.get("FriendlyName", "").lower()):
                    self.inst_id = i["InstanceID"]
                    for ep in i.get("ApplicationEndpoints", []):
                        if "Game" in ep.get("DisplayName", "") and ":" in ep.get("Endpoint", ""):
                            self.port = ep["Endpoint"].rsplit(":", 1)[1]
        if not self.inst_id:
            raise RuntimeError(f"AMP instance '{CFG['AMP_INSTANCE']}' not found")
        try:
            r = self._post(f"/API/ADSModule/Servers/{self.inst_id}/API/Core/Login", creds)
            self.inst_sid = r["sessionID"] if r.get("success") else self.ads_sid
        except Exception:
            self.inst_sid = self.ads_sid
        self.nodes = {}

    def call(self, method, data=None):
        with self.lock:
            for attempt in (1, 2):
                try:
                    if not self.inst_sid:
                        self._login()
                    r = self._post(f"/API/ADSModule/Servers/{self.inst_id}/API/{method}", data, self.inst_sid)
                    return r.get("result", r) if isinstance(r, dict) and "result" in r else r
                except PermissionError:
                    self.inst_sid = None
                    if attempt == 2:
                        raise

    def node(self, field):
        """Find the AMP settings node for a template FieldName (e.g. GameMode)."""
        if field not in self.nodes:
            spec = self.call("Core/GetSettingsSpec") or {}
            for items in spec.values():
                for s in items:
                    n = s.get("Node", "")
                    if n.endswith("." + field) or s.get("Name") == field:
                        self.nodes[field] = n
        return self.nodes.get(field)

    def get_setting(self, field):
        n = self.node(field)
        if not n:
            return None
        r = self.call("Core/GetConfig", {"node": n})
        return r.get("CurrentValue") if isinstance(r, dict) else r

    def set_setting(self, field, value):
        n = self.node(field)
        if not n:
            raise RuntimeError(f"setting {field} not found on instance")
        return self.call("Core/SetConfig", {"node": n, "value": str(value)})


amp = AMP()
console = []          # rolling console buffer fed by Core/GetUpdates
console_lock = threading.Lock()


def poll_console():
    while True:
        try:
            u = amp.call("Core/GetUpdates") or {}
            entries = u.get("ConsoleEntries", [])
            if entries:
                with console_lock:
                    console.extend(f"{e.get('Source', '')}: {e.get('Contents', '')}" for e in entries)
                    del console[:-300]
        except Exception:
            pass
        time.sleep(2)


def status():
    s = amp.call("Core/GetStatus") or {}
    users = amp.call("Core/GetUserList") or {}
    names = list(users.values()) if isinstance(users, dict) else [u.get("Name", u) if isinstance(u, dict) else u for u in users]
    m = s.get("Metrics", {})
    au = m.get("Active Users", {})
    st = s.get("State", -1)
    return {
        "state": STATES.get(st, str(st)), "online": st == 20, "busy": st in (5, 7, 10, 30, 40, 70, 75),
        "uptime": s.get("Uptime", ""), "players": names,
        "playerCount": au.get("RawValue", len(names)), "maxPlayers": au.get("MaxValue", 0),
        "cpu": m.get("CPU Usage", {}).get("Percent", 0), "mem": m.get("Memory Usage", {}).get("RawValue", 0),
        "mode": amp.get_setting("GameMode"), "map": amp.get_setting("MapName"),
        "address": CFG["SERVER_ADDR"], "port": amp.port or "7777",
    }


def make_token():
    exp = str(int(time.time()) + 12 * 3600)
    return exp + "." + hmac.new(SECRET, exp.encode(), hashlib.sha256).hexdigest()


def valid_token(tok):
    try:
        exp, sig = tok.split(".", 1)
        return int(exp) > time.time() and hmac.compare_digest(sig, hmac.new(SECRET, exp.encode(), hashlib.sha256).hexdigest())
    except Exception:
        return False


fails = {}


class H(BaseHTTPRequestHandler):
    server_version = "SpellbreakUI/1.0"

    def log_message(self, fmt, *a):
        pass

    def send(self, code, obj=None, ctype="application/json", extra=None):
        body = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def admin(self):
        for part in self.headers.get("Cookie", "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == "sbadmin" and valid_token(v):
                return True
        return False

    def body(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def do_GET(self):
        try:
            if self.path in ("/", "/index.html"):
                return self.send(200, (HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
            if self.path == "/api/status":
                d = status()
                d["admin"] = self.admin()
                return self.send(200, d)
            if self.path == "/api/console":
                if not self.admin():
                    return self.send(403, {"error": "PIN required"})
                with console_lock:
                    return self.send(200, {"lines": console[-150:]})
            self.send(404, {"error": "not found"})
        except Exception as e:
            self.send(502, {"error": str(e)})

    def do_POST(self):
        try:
            ip = self.client_address[0]
            if self.path == "/api/login":
                if fails.get(ip, (0, 0))[0] >= 5 and time.time() - fails[ip][1] < 300:
                    return self.send(429, {"error": "Too many attempts, wait 5 minutes"})
                pin = str(self.body().get("pin", ""))
                if CFG["ADMIN_PIN"] and hmac.compare_digest(pin, CFG["ADMIN_PIN"]):
                    fails.pop(ip, None)
                    return self.send(200, {"ok": True}, extra={"Set-Cookie": f"sbadmin={make_token()}; HttpOnly; SameSite=Strict; Path=/; Max-Age=43200"})
                c = fails.get(ip, (0, 0))[0] + 1
                fails[ip] = (c, time.time())
                return self.send(401, {"error": "Wrong PIN"})
            if self.path == "/api/logout":
                return self.send(200, {"ok": True}, extra={"Set-Cookie": "sbadmin=; Path=/; Max-Age=0"})
            if not self.admin():
                return self.send(403, {"error": "PIN required"})
            b = self.body()
            if self.path in ("/api/start", "/api/stop", "/api/restart"):
                amp.call("Core/" + self.path.rsplit("/", 1)[1].capitalize())
                return self.send(200, {"ok": True})
            if self.path == "/api/mode":
                mode, mp = b.get("mode"), (b.get("map") or "").strip()
                if mode not in MODES:
                    return self.send(400, {"error": "Unknown mode"})
                amp.set_setting("GameMode", mode)
                if mp:
                    if not all(ch.isalnum() or ch in "_-" for ch in mp):
                        return self.send(400, {"error": "Invalid map name"})
                    amp.set_setting("MapName", mp)
                if b.get("restart", True):
                    amp.call("Core/Restart")
                return self.send(200, {"ok": True})
            if self.path == "/api/command":
                msg = str(b.get("command", "")).strip()[:200]
                if msg:
                    amp.call("Core/SendConsoleMessage", {"message": msg})
                return self.send(200, {"ok": True})
            self.send(404, {"error": "not found"})
        except Exception as e:
            self.send(502, {"error": str(e)})


if __name__ == "__main__":
    if not CFG["ADMIN_PIN"]:
        print("WARNING: ADMIN_PIN is empty, admin controls are disabled")
    threading.Thread(target=poll_console, daemon=True).start()
    print(f"Spellbreak UI on http://{CFG['LISTEN']}:{CFG['PORT']} -> AMP {CFG['AMP_URL']} [{CFG['AMP_INSTANCE']}]")
    ThreadingHTTPServer((CFG["LISTEN"], CFG["PORT"]), H).serve_forever()
