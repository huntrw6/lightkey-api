#!/usr/bin/env python3
"""lightkey-api: read-only live state of a running Lightkey, over HTTP.

Lightkey (macOS) accepts OSC/MIDI input but sends no state back. This small agent fills the gap:

  * it watches whether Lightkey is running,
  * it watches the sACN (E1.31) DMX stream Lightkey emits, so it knows the moment output changes,
  * it reads the list of active cues through Lightkey's own "Get Active Cues" Shortcuts action,
  * it serves the result (and a timestamped change log) as JSON over HTTP.

Python 3.9+ standard library only. Not affiliated with the makers of Lightkey.

Commands:
  run                 start the agent (what the LaunchAgent runs)
  check               verify Lightkey, the Shortcut and permissions, then print the active cues
  install-shortcut    build, sign and open the "Get Active Cues" Shortcut for one-time import
"""
import hashlib
import json
import logging
import logging.handlers
import os
import plistlib
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

__version__ = "0.1.0"

HOME = os.path.expanduser("~")
DEFAULT_CONFIG_PATH = os.path.join(HOME, "Library", "Application Support", "LightkeyAPI", "config.json")

DEFAULTS = {
    "shortcut": "Lightkey API Active Cues",  # name of the imported Shortcut
    "port": 8770,
    "bind": "",                      # "" = all interfaces, filtered by the allow-list below
    "allow_interfaces": [],          # extra interfaces (e.g. ["en5"]) whose address may be queried; loopback always may
    "allowed_local_ips": [],         # extra server-side addresses that may be queried
    "sacn_interfaces": "auto",       # "auto" (all IPv4 interfaces except loopback/link-local) or a list like ["en5"]
    "sacn_universes": [1, 2, 3, 4],  # universes to watch for change detection
    "process_name": "Lightkey",
    "backstop_s": 10.0,              # read at least this often while Lightkey runs, even if DMX looks idle
    "quiet_gap_s": 0.25,             # min gap between reads when DMX changes are sparse
    "busy_gap_s": 1.0,               # min gap when DMX changes constantly but the cue set does not (chases, fades)
    "max_parallel_reads": 3,
    "read_timeout_s": 15,
    "sample_offset_ms": 425,         # the Shortcut samples Lightkey this long after launch (measured, +-25 ms)
    "log_size": 500,                 # change events kept in memory
    "log_file": os.path.join(HOME, "Library", "Logs", "lightkey-api.log"),
}

log = logging.getLogger("lightkey-api")
CFG = dict(DEFAULTS)

now_ms = lambda: int(time.time() * 1000)

# ---------------------------------------------------------------- state
cv = threading.Condition()
S = {
    "online": False,        # Lightkey process is running
    "active": None,         # sorted list of active cue names, or None when unknown/offline
    "seq": 0,               # increments whenever (online, active) changes
    "changed_at": 0,
    "read_at": 0,           # ms epoch the last successful read finished
    "read_started_at": 0,
    "read_ms": 0,
    "dmx_changed_at": 0,
    "dmx_seen_at": 0,
    "error": None,
    "reads": 0,
}
LOG = deque(maxlen=500)     # cue-change events, oldest first
DMXLOG = deque(maxlen=400)  # (ms, universe) of DMX frames that differed
streak = [0]                # consecutive reads triggered by DMX activity that found no cue change
dirty_at = [0.0]
wake = threading.Event()
prev_sample = [0]


def _key(online, active):
    return (online, tuple(active) if active is not None else None)


def publish(**kw):
    """Update shared state; when the cue set or online flag changes, append a timestamped event."""
    with cv:
        before = _key(S["online"], S["active"])
        old_active = S["active"]
        S.update(kw)
        after = _key(S["online"], S["active"])
        if before != after:
            S["seq"] += 1
            S["changed_at"] = now_ms()
            ev = {"seq": S["seq"], "online": S["online"], "logged_at": S["changed_at"]}
            if "read_started_at" in kw and S["active"] is not None:
                sample = kw["read_started_at"] + CFG["sample_offset_ms"]
                lo = prev_sample[0] if prev_sample[0] else sample - 1000
                dm = [t for (t, _u) in DMXLOG if lo < t <= sample + 25]
                base = set(old_active or [])
                cur = set(S["active"])
                ev.update({
                    "added": sorted(cur - base),
                    "removed": sorted(base - cur),
                    "initial": old_active is None,
                    "window_start_ms": lo,
                    "sample_ms": sample,
                    "dmx_changes_ms": dm[:20],
                    # best estimate of WHEN the change happened on this machine's clock:
                    # the first DMX frame that differed, else the moment the read sampled Lightkey
                    "effective_ms": dm[0] if dm else sample,
                    "effective_src": "dmx" if dm else "sample",
                    "read_ms": kw.get("read_ms"),
                })
                streak[0] = 0
            LOG.append(ev)
        if "read_started_at" in kw and S["active"] is not None:
            prev_sample[0] = max(prev_sample[0], kw["read_started_at"] + CFG["sample_offset_ms"])
        cv.notify_all()


def snapshot():
    with cv:
        d = dict(S)
    d["version"] = __version__
    d["server_time"] = now_ms()
    d["count"] = len(d["active"]) if d["active"] is not None else None
    return d


# ---------------------------------------------------------------- Lightkey process
def lightkey_running():
    try:
        return subprocess.run(["/usr/bin/pgrep", "-x", CFG["process_name"]],
                              capture_output=True, timeout=5).returncode == 0
    except Exception:
        return S["online"]


def process_loop():
    while True:
        up = lightkey_running()
        if up != S["online"]:
            log.info("Lightkey %s", "started" if up else "stopped")
            if up:
                publish(online=True, error=None)
                dirty_at[0] = time.monotonic()
                wake.set()
            else:
                prev_sample[0] = 0
                publish(online=False, active=None)
        time.sleep(2.0)


# ---------------------------------------------------------------- network helpers
def interface_addrs():
    """{interface: ipv4} for every interface that has an address (parsed from ifconfig)."""
    out = {}
    try:
        txt = subprocess.run(["/sbin/ifconfig", "-a"], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return out
    cur = None
    for line in txt.splitlines():
        m = re.match(r"^([a-z0-9]+):", line)
        if m:
            cur = m.group(1)
            continue
        m = re.match(r"^\s+inet (\d+\.\d+\.\d+\.\d+)", line)
        if m and cur:
            out.setdefault(cur, m.group(1))
    return out


def sacn_ips():
    addrs = interface_addrs()
    want = CFG["sacn_interfaces"]
    if want == "auto":
        return sorted({ip for n, ip in addrs.items() if n != "lo0" and not ip.startswith("169.254.")})
    return sorted({addrs[n] for n in want if n in addrs})


# ---------------------------------------------------------------- sACN change detector
def sacn_loop():
    sock = None
    joined = None
    last = {}
    while True:
        ips = sacn_ips()
        if ips != joined:
            if sock:
                sock.close()
                sock = None
            joined = ips
            if ips:
                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    try:
                        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
                    except Exception:
                        pass
                    s.bind(("", 5568))
                    for ip in ips:
                        for u in CFG["sacn_universes"]:
                            g = socket.inet_aton("239.255.%d.%d" % (u >> 8, u & 255))
                            try:
                                s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, g + socket.inet_aton(ip))
                            except OSError:
                                pass
                    s.settimeout(1.0)
                    sock = s
                    log.info("watching sACN universes %s on %s", CFG["sacn_universes"], ips)
                except Exception as e:
                    log.warning("sACN listen failed: %s", e)
                    sock = None
                    joined = None
                    time.sleep(3)
            else:
                log.info("no usable interface for sACN yet; relying on the %ss backstop read", CFG["backstop_s"])
        if not sock:
            time.sleep(3)
            continue
        try:
            d, _ = sock.recvfrom(1500)
        except socket.timeout:
            continue
        except OSError:
            joined = None
            continue
        if len(d) < 126 or d[4:16] != b"ASC-E1.17\0\0\0":
            continue
        uni = struct.unpack(">H", d[113:115])[0]
        h = hashlib.blake2b(d[125:], digest_size=8).digest()   # start code + slots; excludes the sequence number
        t = now_ms()
        S["dmx_seen_at"] = t
        if last.get(uni) != h:
            first = uni not in last
            last[uni] = h
            if not first:
                S["dmx_changed_at"] = t
                DMXLOG.append((t, uni))
                dirty_at[0] = time.monotonic()
                wake.set()


# ---------------------------------------------------------------- active-cue reader
def read_active():
    """Run the Shortcut once; one output file per active cue."""
    out = tempfile.mkdtemp(prefix="lkapi-")
    try:
        p = subprocess.run(["/usr/bin/shortcuts", "run", CFG["shortcut"], "-o", os.path.join(out, "o")],
                           capture_output=True, text=True, timeout=CFG["read_timeout_s"])
        err = "\n".join(l for l in p.stderr.splitlines()
                        if l.strip() and not l.startswith("ERROR: Unrecognized attribute"))
        if p.returncode != 0 or err:
            raise RuntimeError(err or ("shortcuts exited %d" % p.returncode))
        d = os.path.join(out, "o")
        names = []
        if os.path.isdir(d):
            for f in sorted(os.listdir(d)):
                try:
                    txt = open(os.path.join(d, f), encoding="utf-8").read().strip()
                except Exception:
                    txt = ""
                names.append(txt or (f[:-4] if f.endswith(".txt") else f))
        return sorted(set(names))
    finally:
        shutil.rmtree(out, ignore_errors=True)


applied_start = [0]
apply_lock = threading.Lock()
fails = [0]


def one_read(started, inflight):
    try:
        names = read_active()
        done = now_ms()
        with apply_lock:
            if started < applied_start[0]:
                return                      # a newer read already published
            applied_start[0] = started
            with cv:
                S["reads"] += 1
            publish(active=names, read_at=done, read_started_at=started, read_ms=done - started, error=None)
        fails[0] = 0
    except Exception as e:
        fails[0] += 1
        log.warning("read failed (%d): %s", fails[0], e)
        publish(error=str(e)[:300])
    finally:
        inflight.release()


def reader_loop():
    inflight = threading.Semaphore(CFG["max_parallel_reads"])
    last_start = 0.0
    handled = 0.0
    while True:
        wake.wait(timeout=1.0)
        if not S["online"]:
            wake.clear()
            continue
        t = time.monotonic()
        due_dirty = dirty_at[0] > handled
        due_backstop = t - last_start >= CFG["backstop_s"]
        if not (due_dirty or due_backstop):
            wake.clear()
            continue
        gap = CFG["quiet_gap_s"] if streak[0] < 2 else CFG["busy_gap_s"]
        wait = max(gap - (t - last_start), 0.0)
        if fails[0]:
            wait = max(wait, min(2 ** fails[0], 15) - (t - last_start))
        if wait > 0:
            time.sleep(wait)
        if not inflight.acquire(timeout=5):
            continue
        wake.clear()
        handled = dirty_at[0]
        if due_dirty:
            streak[0] += 1
        last_start = time.monotonic()
        threading.Thread(target=one_read, args=(now_ms(), inflight), daemon=True).start()


# ---------------------------------------------------------------- HTTP
INDEX = {
    "name": "lightkey-api", "version": __version__,
    "endpoints": {
        "/state": "current snapshot (online, active cues, timestamps)",
        "/log?since=N": "cue-change events with seq > N, each with effective_ms (see README)",
        "/events": "Server-Sent Events: 'cues' per change and 'state' snapshots",
        "/time": "server clock in ms, for measuring the clock offset",
        "/health": "liveness and last error",
    },
}


def _allowed_ips():
    ips = {"127.0.0.1"} | set(CFG["allowed_local_ips"])
    addrs = interface_addrs()
    for n in CFG["allow_interfaces"]:
        if n in addrs:
            ips.add(addrs[n])
    return ips


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.connection.getsockname()[0] not in _allowed_ips():
            return self._json({"error": "not available on this interface"}, 403)
        path, _, query = self.path.partition("?")
        q = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
        if path in ("/", "/index.json"):
            return self._json(INDEX)
        if path == "/state":
            return self._json(snapshot())
        if path == "/time":
            return self._json({"t": now_ms()})
        if path == "/log":
            try:
                since = int(q.get("since", 0))
            except ValueError:
                return self._json({"error": "since must be an integer"}, 400)
            with cv:
                evs = [e for e in LOG if e["seq"] > since]
            return self._json({"events": evs, "server_time": now_ms(), "state": snapshot()})
        if path == "/health":
            s = snapshot()
            return self._json({"ok": True, "lightkey": s["online"], "reads": s["reads"], "error": s["error"],
                               "dmx_age_ms": (s["server_time"] - s["dmx_seen_at"]) if s["dmx_seen_at"] else None})
        if path == "/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            last = -1
            try:
                while True:
                    with cv:
                        cv.wait_for(lambda: S["seq"] != last, timeout=15)
                        changed = S["seq"] != last
                    if changed:
                        s = snapshot()
                        with cv:
                            evs = [e for e in LOG if e["seq"] > last]
                        last = s["seq"]
                        for e in evs:
                            self.wfile.write(("event: cues\ndata: %s\n\n"
                                              % json.dumps(dict(e, server_time=now_ms()))).encode())
                        self.wfile.write(("event: state\ndata: %s\n\n" % json.dumps(s)).encode())
                    else:
                        self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                return
        self._json({"error": "not found"}, 404)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


# ---------------------------------------------------------------- shortcut + check
def build_shortcut_plist():
    """The one-action Shortcut: Lightkey > Get Active Cues > output."""
    uid = "9F8A3C1E-5B7D-4C2A-8E6F-0A1B2C3D4E5F"
    action = {
        "WFWorkflowActionIdentifier": "de.monospc.Lightkey.GetActiveCuesIntent",
        "WFWorkflowActionParameters": {"UUID": uid, "IntentAppIdentifier": "de.monospc.Lightkey"},
    }
    output = {
        "WFWorkflowActionIdentifier": "is.workflow.actions.output",
        "WFWorkflowActionParameters": {"WFOutput": {
            "Value": {"string": "\ufffc", "attachmentsByRange": {"{0, 1}": {
                "OutputUUID": uid, "OutputName": "Active Cues", "Type": "ActionOutput"}}},
            "WFSerializationType": "WFTextTokenString"}},
    }
    return {
        "WFWorkflowClientVersion": "2900", "WFWorkflowClientRelease": "15.0",
        "WFWorkflowMinimumClientVersion": 900, "WFWorkflowMinimumClientVersionString": "900",
        "WFWorkflowIcon": {"WFWorkflowIconStartColor": 4282601983, "WFWorkflowIconGlyphNumber": 59511},
        "WFWorkflowTypes": [], "WFWorkflowInputContentItemClasses": [], "WFWorkflowImportQuestions": [],
        "WFWorkflowActions": [action, output],
    }


def install_shortcut():
    name = CFG["shortcut"]
    d = tempfile.mkdtemp(prefix="lkapi-sc-")
    src = os.path.join(d, name + ".shortcut")       # `shortcuts sign` only accepts a .shortcut extension
    dst = os.path.join(d, "signed", name + ".shortcut")
    os.makedirs(os.path.dirname(dst))
    with open(src, "wb") as f:
        plistlib.dump(build_shortcut_plist(), f, fmt=plistlib.FMT_XML)
    r = subprocess.run(["/usr/bin/shortcuts", "sign", "-m", "anyone", "-i", src, "-o", dst],
                       capture_output=True, text=True)
    if not os.path.exists(dst):
        print("Could not sign the Shortcut:\n" + r.stderr[-400:])
        return 1
    subprocess.run(["/usr/bin/open", dst])
    print("The Shortcuts app should now show an 'Add Shortcut' sheet named '%s'.\n"
          "1. Click Add Shortcut.\n"
          "2. Run:  python3 lightkey_api.py check   (macOS asks once: Allow \"%s\" to access \"Lightkey\"? -> Allow)" % (name, name))
    return 0


def check():
    ok = True

    def line(good, msg):
        nonlocal ok
        ok = ok and good
        print(("OK    " if good else "FAIL  ") + msg)

    line(sys.version_info >= (3, 9), "python %d.%d.%d (need 3.9+)" % sys.version_info[:3])
    line(os.path.isdir("/Applications/Lightkey.app") or os.path.isdir(os.path.join(HOME, "Applications", "Lightkey.app")),
         "Lightkey.app installed")
    running = lightkey_running()
    line(running, "Lightkey is running" + ("" if running else " (open it, with a project loaded)"))
    lst = subprocess.run(["/usr/bin/shortcuts", "list"], capture_output=True, text=True).stdout.splitlines()
    have = CFG["shortcut"] in [l.strip() for l in lst]
    line(have, "Shortcut '%s' %s" % (CFG["shortcut"], "found" if have else "missing; run: python3 lightkey_api.py install-shortcut"))
    if have and running:
        t = time.time()
        try:
            names = read_active()
            line(True, "read %d active cue(s) in %.0f ms" % (len(names), (time.time() - t) * 1000))
            for n in names:
                print("        - " + n)
        except Exception as e:
            line(False, "reading active cues failed: %s\n"
                        "        Most likely the one-time approval prompt is waiting: 'Allow \"%s\" to access \"Lightkey\"?'.\n"
                        "        It can be hidden. Quit the Shortcuts app, then run this command again from a Terminal on the\n"
                        "        Mac itself (not over SSH) and click Allow. Shortcuts also needs a logged-in, unlocked desktop."
                        % (str(e).strip()[:200], CFG["shortcut"]))
    print("sACN interfaces: %s" % (sacn_ips() or "none found; change detection falls back to the %ss backstop" % CFG["backstop_s"]))
    return 0 if ok else 1


# ---------------------------------------------------------------- main
def load_config(path):
    CFG.clear()
    CFG.update(DEFAULTS)
    if os.path.exists(path):
        with open(path) as f:
            CFG.update(json.load(f))
    LOG_MAX = CFG["log_size"]
    global LOG
    LOG = deque(LOG, maxlen=LOG_MAX)


def setup_logging():
    os.makedirs(os.path.dirname(CFG["log_file"]), exist_ok=True)
    log.setLevel(logging.INFO)
    h = logging.handlers.RotatingFileHandler(CFG["log_file"], maxBytes=1_000_000, backupCount=3)
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(h)


def run():
    setup_logging()
    log.info("lightkey-api %s starting; config=%s", __version__,
             json.dumps({k: v for k, v in CFG.items() if k != "log_file"}))
    S["online"] = lightkey_running()
    for fn in (process_loop, sacn_loop, reader_loop):
        threading.Thread(target=fn, daemon=True).start()
    dirty_at[0] = time.monotonic()
    wake.set()
    Server((CFG["bind"], CFG["port"]), Handler).serve_forever()


def main(argv):
    path = os.environ.get("LIGHTKEY_API_CONFIG", DEFAULT_CONFIG_PATH)
    load_config(path)
    cmd = argv[1] if len(argv) > 1 else "run"
    if cmd == "run":
        run()
    elif cmd == "check":
        return check()
    elif cmd == "install-shortcut":
        return install_shortcut()
    elif cmd in ("-V", "--version", "version"):
        print(__version__)
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
