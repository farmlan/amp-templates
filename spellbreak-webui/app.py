#!/usr/bin/env python3
"""FarmLAN Spellbreak web UI: public join page plus a PIN-protected admin site
(overview, match setup, players, console, settings) for an AMP-managed
Spellbreak instance. Python stdlib only: no pip installs needed."""
import hashlib, hmac, json, os, re, secrets, sqlite3, threading, time, urllib.request
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
CFG = {
    "AMP_URL": os.environ.get("AMP_URL", "http://192.168.5.13:8080").rstrip("/"),
    "AMP_USER": os.environ.get("AMP_USER", ""),
    "AMP_PASS": os.environ.get("AMP_PASS", ""),
    "AMP_INSTANCE": os.environ.get("AMP_INSTANCE", "Spellbreak01"),
    "ADMIN_PIN": os.environ.get("ADMIN_PIN", ""),
    "SERVER_ADDR": os.environ.get("SERVER_ADDR", "192.168.5.15"),
    "LISTEN": os.environ.get("LISTEN", "0.0.0.0"),
    "PORT": int(os.environ.get("PORT", "8095")),
}
STATE = Path(os.environ.get("STATE_DIRECTORY", "/var/lib/spellbreak-webui").split(":")[0])
SETTINGS = STATE / "settings.json"     # written by the first-run setup page
DB_PATH = STATE / "history.db"

STATES = {-1: "Unknown", 0: "Offline", 5: "Pre-start", 7: "Configuring", 10: "Starting",
          20: "Online", 30: "Restarting", 40: "Stopping", 45: "Preparing to sleep",
          50: "Sleeping", 60: "Waiting", 70: "Installing", 75: "Updating",
          80: "Awaiting input", 100: "Failed", 200: "Suspended", 250: "Maintenance"}
BUSY = {5, 7, 10, 30, 40, 70, 75}
MODES = {"Solo": "Solo", "Duo": "Duo", "Squad": "Squad", "Capture": "Dominion"}


# Log patterns (the launcher strips the trailing !json{...} metadata)
JOIN_RE = re.compile(r"Recieved join options,\{ PlayerName => (?P<name>[^,]+),")
LEAVE_RES = [re.compile(p) for p in (
    r"HaxeLog: .*(?:Logout|PlayerLeft|left the game|Player disconnected)[^A-Za-z0-9]*(?:player|name)?\s*=?>?\s*(?P<name>[A-Za-z0-9_\-]+)?",
    r"LogNet: .*UNetConnection::Close.*?(?:Name|PlayerName)[:=] ?(?P<name>[A-Za-z0-9_\-]+)",
)]
MATCH_RE = re.compile(r"MatchStartConditions: \((?P<n>\d+)/(?P<max>\d+)\)")


# ---------------------------------------------------------------- settings
def hash_pin(pin, salt=None):
    salt = salt or secrets.token_hex(16)
    return salt + "$" + hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt), 200_000).hex()


def pin_ok(pin):
    h = CFG.get("PIN_HASH", "")
    if h and "$" in h:
        return hmac.compare_digest(hash_pin(pin, h.split("$", 1)[0]), h)
    return bool(CFG["ADMIN_PIN"]) and hmac.compare_digest(pin, CFG["ADMIN_PIN"])


def load_settings():
    try:
        CFG.update(json.loads(SETTINGS.read_text()))
    except Exception:
        pass


def save_settings(**kw):
    CFG.update(kw)
    STATE.mkdir(parents=True, exist_ok=True)
    keep = {k: CFG[k] for k in ("AMP_USER", "AMP_PASS", "PIN_HASH", "SERVER_ADDR", "AMP_INSTANCE") if CFG.get(k)}
    tmp = SETTINGS.with_suffix(".tmp")
    tmp.write_text(json.dumps(keep))
    os.chmod(tmp, 0o600)
    tmp.replace(SETTINGS)


def configured():
    return bool(CFG["AMP_USER"] and CFG["AMP_PASS"] and (CFG.get("PIN_HASH") or CFG["ADMIN_PIN"]))


load_settings()
SECRET = secrets.token_bytes(32)          # admin sessions reset when the service restarts


# ---------------------------------------------------------------- history store
class Store:
    def __init__(self, path):
        STATE.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.lock = threading.Lock()
        with self.lock:
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS metrics(t INTEGER, cpu REAL, mem REAL, players INTEGER, state INTEGER);
                CREATE TABLE IF NOT EXISTS events(t INTEGER, kind TEXT, detail TEXT);
                CREATE TABLE IF NOT EXISTS sessions(id INTEGER PRIMARY KEY, name TEXT, joined INTEGER, left INTEGER);
                CREATE INDEX IF NOT EXISTS mt ON metrics(t);
                CREATE INDEX IF NOT EXISTS et ON events(t);
                UPDATE sessions SET left = joined WHERE left IS NULL;
            """)
            self.db.commit()

    def q(self, sql, args=()):
        with self.lock:
            cur = self.db.execute(sql, args)
            rows = cur.fetchall()
            self.db.commit()
            return rows

    def event(self, kind, detail=""):
        self.q("INSERT INTO events VALUES(?,?,?)", (int(time.time()), kind, detail))

    def prune(self):
        cut = int(time.time()) - 14 * 86400
        self.q("DELETE FROM metrics WHERE t < ?", (cut,))
        self.q("DELETE FROM events WHERE t < ?", (cut,))


store = Store(DB_PATH)


# ---------------------------------------------------------------- AMP client
class AMP:
    """Minimal AMP API client that talks to one instance through the ADS controller."""
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
            raise RuntimeError(f"AMP rejected the login for user '{CFG['AMP_USER']}' (code {r.get('result')}: "
                               f"{r.get('resultReason')}). Check the exact username (case-sensitive) and password.")
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
            raise RuntimeError(f"AMP instance '{CFG['AMP_INSTANCE']}' not found for this user")
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
            raise RuntimeError(f"setting {field} not found on instance (check the AMP user's permissions)")
        return self.call("Core/SetConfig", {"node": n, "value": str(value)})


amp = AMP()


# ---------------------------------------------------------------- live state + poller
class Live:
    def __init__(self):
        self.lock = threading.Lock()
        self.status = {"state": "Connecting", "stateCode": -1, "online": False, "busy": False}
        self.error = None
        self.console = deque(maxlen=3000)      # (seq, t, source, text)
        self.seq = 0
        self.online = {}                       # name -> session id
        self.match = None                      # (n, max) from MatchStartConditions
        self.settings = {"mode": None, "map": None, "bots": None}
        self.last_metric = 0
        self.last_settings = 0
        self.last_state = None

    def add_console(self, t, src, text):
        with self.lock:
            self.seq += 1
            self.console.append((self.seq, t, src, text))

    def player_join(self, name, t):
        if name in self.online:
            return
        sid = store.q("INSERT INTO sessions(name, joined) VALUES(?,?) RETURNING id", (name, t))[0][0]
        self.online[name] = sid
        store.event("join", name)

    def player_leave(self, name, t):
        sid = self.online.pop(name, None)
        if sid:
            store.q("UPDATE sessions SET left=? WHERE id=?", (t, sid))
            store.event("leave", name)

    def everyone_left(self, why):
        t = int(time.time())
        for name in list(self.online):
            sid = self.online.pop(name)
            store.q("UPDATE sessions SET left=? WHERE id=?", (t, sid))
        if why:
            store.event("system", why)


live = Live()


def parse_line(text, t):
    m = JOIN_RE.search(text)
    if m:
        live.player_join(m.group("name").strip(), t)
        return
    m = MATCH_RE.search(text)
    if m:
        live.match = (int(m.group("n")), int(m.group("max")))
    for rx in LEAVE_RES:
        m = rx.search(text)
        if m and m.groupdict().get("name") in live.online:
            live.player_leave(m.group("name"), t)


def poll_once():
    now = int(time.time())
    s = amp.call("Core/GetStatus") or {}
    st = s.get("State", -1)
    m = s.get("Metrics", {})
    au = m.get("Active Users", {})
    mem = m.get("Memory Usage", {})

    u = amp.call("Core/GetUpdates") or {}
    for e in u.get("ConsoleEntries", []) or []:
        text = str(e.get("Contents", ""))
        live.add_console(now, e.get("Source", ""), text)
        parse_line(text, now)

    if live.last_state is not None and st != live.last_state:
        store.event("state", f"{STATES.get(live.last_state, live.last_state)} → {STATES.get(st, st)}")
        if st != 20:
            live.everyone_left(None)
            live.match = None
    live.last_state = st

    if now - live.last_settings > 20:
        bots = amp.get_setting("Bots")
        live.settings = {"mode": amp.get_setting("GameMode"), "map": amp.get_setting("MapName"),
                         "bots": int(bots) if str(bots or "").isdigit() else None}
        live.last_settings = now

    players = sorted(live.online)
    with live.lock:
        live.status = {
            "state": STATES.get(st, str(st)), "stateCode": st, "online": st == 20, "busy": st in BUSY,
            "uptime": s.get("Uptime", ""), "players": players,
            "playerCount": len(players) if players else int(au.get("RawValue", 0) or 0),
            "maxPlayers": au.get("MaxValue", 0) or (live.match[1] if live.match else 42),
            "cpu": round(float(m.get("CPU Usage", {}).get("Percent", 0) or 0), 1),
            "memMB": float(mem.get("RawValue", 0) or 0), "memMaxMB": float(mem.get("MaxValue", 0) or 0),
            "mode": live.settings["mode"], "modeLabel": MODES.get(live.settings["mode"] or "", live.settings["mode"]),
            "map": live.settings["map"], "bots": live.settings.get("bots"), "matchFill": live.match,
        }
        live.error = None
    if now - live.last_metric >= 15:
        store.q("INSERT INTO metrics VALUES(?,?,?,?,?)",
                (now, live.status["cpu"], live.status["memMB"], live.status["playerCount"], st))
        live.last_metric = now


def poller():
    last_prune = 0
    while True:
        if configured():
            try:
                poll_once()
            except Exception as e:
                amp.inst_sid = None
                with live.lock:
                    live.error = str(e)
                    live.status = dict(live.status, state="Status unavailable", online=False, busy=False)
        if time.time() - last_prune > 3600:
            try:
                store.prune()
            except Exception:
                pass
            last_prune = time.time()
        time.sleep(3)


def public_status(admin):
    with live.lock:
        d = dict(live.status)
        err = live.error
    d.update(address=CFG["SERVER_ADDR"], port=amp.port or "7777", admin=admin,
             configured=configured(), error=err if admin else None)
    return d


# ---------------------------------------------------------------- auth helpers
def make_token():
    exp = str(int(time.time()) + 12 * 3600)
    return exp + "." + hmac.new(SECRET, exp.encode(), hashlib.sha256).hexdigest()


def valid_token(tok):
    try:
        exp, sig = tok.split(".", 1)
        return int(exp) > time.time() and hmac.compare_digest(sig, hmac.new(SECRET, exp.encode(), hashlib.sha256).hexdigest())
    except Exception:
        return False


COOKIE = "sbadmin={}; HttpOnly; SameSite=Strict; Path=/; Max-Age=43200"
fails = {}


# ---------------------------------------------------------------- HTTP
class H(BaseHTTPRequestHandler):
    server_version = "SpellbreakUI/2.0"

    def log_message(self, fmt, *a):
        pass

    def send(self, code, obj=None, ctype="application/json", extra=None):
        body = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
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

    # ---- GET
    def do_GET(self):
        try:
            url = urlparse(self.path)
            qs = {k: v[0] for k, v in parse_qs(url.query).items()}
            p = url.path
            if p in ("/", "/index.html"):
                return self.send(200, (HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
            if p == "/api/status":
                return self.send(200, public_status(self.admin()))
            if p == "/api/setup":
                return self.send(200, {"needed": not configured(), "instance": CFG["AMP_INSTANCE"],
                                       "ampUrl": CFG["AMP_URL"], "serverAddr": CFG["SERVER_ADDR"],
                                       "ampUser": CFG["AMP_USER"] if self.admin() or not configured() else ""})
            if not self.admin():
                return self.send(403, {"error": "PIN required"})
            if p == "/api/history":
                mins = max(5, min(int(qs.get("minutes", 60)), 7 * 1440))
                since = int(time.time()) - mins * 60
                rows = store.q("SELECT t,cpu,mem,players,state FROM metrics WHERE t>=? ORDER BY t", (since,))
                step = max(1, len(rows) // 240)                # cap points sent to the browser
                pts = [{"t": r[0], "cpu": r[1], "mem": r[2], "players": r[3], "state": r[4]} for r in rows[::step]]
                return self.send(200, {"points": pts, "minutes": mins})
            if p == "/api/events":
                lim = max(1, min(int(qs.get("limit", 100)), 500))
                rows = store.q("SELECT t,kind,detail FROM events ORDER BY t DESC LIMIT ?", (lim,))
                return self.send(200, {"events": [{"t": r[0], "kind": r[1], "detail": r[2]} for r in rows]})
            if p == "/api/players":
                now = int(time.time())
                recent = store.q("SELECT name,joined,left FROM sessions ORDER BY joined DESC LIMIT 200")
                totals = store.q("SELECT name, COUNT(*), SUM(COALESCE(left,?)-joined), MAX(joined) FROM sessions "
                                 "GROUP BY name ORDER BY MAX(joined) DESC", (now,))
                return self.send(200, {
                    "online": [{"name": n, "since": store.q("SELECT joined FROM sessions WHERE id=?", (sid,))[0][0]}
                               for n, sid in sorted(live.online.items())],
                    "sessions": [{"name": r[0], "joined": r[1], "left": r[2]} for r in recent],
                    "totals": [{"name": r[0], "sessions": r[1], "seconds": r[2] or 0, "lastSeen": r[3]} for r in totals],
                })
            if p == "/api/console":
                after = int(qs.get("after", 0))
                with live.lock:
                    lines = [{"seq": s, "t": t, "src": src, "text": tx} for s, t, src, tx in live.console if s > after]
                    last = live.seq
                return self.send(200, {"lines": lines[-1000:], "last": last})
            if p == "/api/console.txt":
                with live.lock:
                    txt = "\n".join(f"{time.strftime('%H:%M:%S', time.localtime(t))} {tx}" for _, t, _, tx in live.console)
                return self.send(200, txt.encode(), "text/plain; charset=utf-8",
                                 {"Content-Disposition": 'attachment; filename="spellbreak-console.txt"'})
            self.send(404, {"error": "not found"})
        except Exception as e:
            self.send(502, {"error": str(e)})

    # ---- POST
    def do_POST(self):
        try:
            ip = self.client_address[0]
            p = urlparse(self.path).path
            if p in ("/api/setup", "/api/settings"):
                if configured() and not self.admin():
                    return self.send(403, {"error": "Setup is already complete"})
                b = self.body()
                user = str(b.get("ampUser", "")).strip() or CFG["AMP_USER"]
                pw = str(b.get("ampPass", "")) or CFG["AMP_PASS"]
                pin, addr = str(b.get("pin", "")), str(b.get("serverAddr", "")).strip() or CFG["SERVER_ADDR"]
                if not configured() or pin:
                    if not (pin.isdigit() and 4 <= len(pin) <= 12):
                        return self.send(400, {"error": "PIN must be 4 to 12 digits"})
                if not user or not pw:
                    return self.send(400, {"error": "AMP username and password are required"})
                old = (CFG["AMP_USER"], CFG["AMP_PASS"])
                CFG["AMP_USER"], CFG["AMP_PASS"] = user, pw
                try:
                    with amp.lock:
                        amp.inst_sid = None
                        amp._login()
                except Exception as e:
                    CFG["AMP_USER"], CFG["AMP_PASS"] = old
                    return self.send(400, {"error": f"Could not connect to AMP: {e}"})
                save_settings(AMP_USER=user, AMP_PASS=pw, SERVER_ADDR=addr,
                              **({"PIN_HASH": hash_pin(pin)} if pin else {}))
                store.event("admin", "Web UI settings saved")
                return self.send(200, {"ok": True}, extra={"Set-Cookie": COOKIE.format(make_token())})
            if p == "/api/login":
                if fails.get(ip, (0, 0))[0] >= 5 and time.time() - fails[ip][1] < 300:
                    return self.send(429, {"error": "Too many attempts, wait 5 minutes"})
                if pin_ok(str(self.body().get("pin", ""))):
                    fails.pop(ip, None)
                    return self.send(200, {"ok": True}, extra={"Set-Cookie": COOKIE.format(make_token())})
                fails[ip] = (fails.get(ip, (0, 0))[0] + 1, time.time())
                return self.send(401, {"error": "Wrong PIN"})
            if p == "/api/logout":
                return self.send(200, {"ok": True}, extra={"Set-Cookie": "sbadmin=; Path=/; Max-Age=0"})
            if not self.admin():
                return self.send(403, {"error": "PIN required"})
            b = self.body()
            if p in ("/api/start", "/api/stop", "/api/restart"):
                act = p.rsplit("/", 1)[1]
                amp.call("Core/" + act.capitalize())
                store.event("admin", f"Server {act} requested")
                return self.send(200, {"ok": True})
            if p == "/api/mode":
                mode = b.get("mode")
                if mode not in MODES:
                    return self.send(400, {"error": "Unknown mode"})
                try:
                    bots = max(0, min(41, int(b.get("bots", 0))))
                except (TypeError, ValueError):
                    return self.send(400, {"error": "Bots must be a number"})
                amp.set_setting("GameMode", mode)
                amp.set_setting("Bots", bots)
                live.last_settings = 0
                store.event("admin", f"Match set to {MODES[mode]} with {bots or 'no'} bots")
                if b.get("restart", True):
                    amp.call("Core/Restart")
                return self.send(200, {"ok": True})
            self.send(404, {"error": "not found"})
        except Exception as e:
            self.send(502, {"error": str(e)})


if __name__ == "__main__":
    if not configured():
        print("Not configured yet: open the web UI to run first-time setup")
    threading.Thread(target=poller, daemon=True).start()
    print(f"Spellbreak UI on http://{CFG['LISTEN']}:{CFG['PORT']} -> AMP {CFG['AMP_URL']} [{CFG['AMP_INSTANCE']}]")
    ThreadingHTTPServer((CFG["LISTEN"], CFG["PORT"]), H).serve_forever()
