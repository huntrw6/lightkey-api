#!/usr/bin/env python3
"""Reference receiver for the lightkey-api OSC relay (docs/relay.md). Python 3.9+, standard library only.

  python3 relay_client.py 10.30.0.211                  print every change with its latency
  python3 relay_client.py 10.30.0.211 --record 20 out.take.json
                                                       record 20 s into a take file the HTTP API's /takes format uses

It shows the three things a receiver (for example a StagePilot plugin) has to do:
  1. subscribe, renew every few seconds, and notice a stream restart (new stream id) or a gap (seq jump);
  2. measure the clock offset to the Lightkey computer with /relay/ping, using the lowest-delay sample;
  3. turn each bundle's time tag into the receiver's own clock:  local_time = tag - offset.
The OSC codec below is a copy of the one in lightkey_api.py so this file stands alone.
"""
import json, socket, struct, sys, time

NTP = 2208988800
pad = lambda b: b + b"\0" * (4 - len(b) % 4)


def message(addr, args=()):
    tags, body = ",", b""
    for a in args:
        if isinstance(a, int):
            tags += "i"; body += struct.pack(">i", a)
        elif isinstance(a, float):
            tags += "d"; body += struct.pack(">d", a)
    return pad(addr.encode()) + pad(tags.encode()) + body


def cstr(b, i):
    j = b.index(b"\0", i)
    return b[i:j].decode(), ((j + 4) // 4) * 4


def parse(b):
    if b.startswith(b"#bundle\0"):
        sec, frac = struct.unpack(">II", b[8:16])
        i, els = 16, []
        while i + 4 <= len(b):
            n = struct.unpack(">i", b[i:i + 4])[0]; i += 4
            els += parse(b[i:i + n]); i += n
        return [("bundle", sec - NTP + frac / 4294967296.0, els)]
    addr, i = cstr(b, 0); tags, i = cstr(b, i); args = []
    for c in tags[1:]:
        if c == "i": args.append(struct.unpack(">i", b[i:i + 4])[0]); i += 4
        elif c == "f": args.append(struct.unpack(">f", b[i:i + 4])[0]); i += 4
        elif c == "d": args.append(struct.unpack(">d", b[i:i + 8])[0]); i += 8
        elif c == "s": v, i = cstr(b, i); args.append(v)
        elif c == "T": args.append(True)
        elif c == "F": args.append(False)
        else: raise ValueError(c)
    return [("msg", addr, args)]


class Receiver:
    def __init__(self, host, port=21700):
        self.dst = (host, port)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("", 0)); self.sock.settimeout(0.25)
        self.offset = None            # seconds: remote clock minus local clock
        self.rtt = None
        self.stream = None; self.last_seq = None; self.gaps = 0
        self.next_sub = 0.0

    def ping(self, n=12):
        """Estimate the clock offset. Keeps the sample with the smallest round trip."""
        best = None
        for _ in range(n):
            t1 = time.time()
            self.sock.sendto(message("/relay/ping", [t1]), self.dst)
            end = time.time() + 0.5
            while time.time() < end:
                try:
                    d, _ = self.sock.recvfrom(2000)
                except socket.timeout:
                    continue
                t4 = time.time()
                m = parse(d)[0]
                if m[0] == "msg" and m[1] == "/relay/pong" and m[2][0] == t1:
                    _, t2, t3 = m[2]
                    rtt = (t4 - t1) - (t3 - t2)
                    off = ((t2 - t1) + (t3 - t4)) / 2
                    if best is None or rtt < best[0]:
                        best = (rtt, off)
                    break
            time.sleep(0.02)
        if best:
            self.rtt, self.offset = best
        return best

    def poll(self):
        """Yield (kind, remote_time, local_time, commands, flags) for each packet; renews the subscription."""
        if time.monotonic() >= self.next_sub:
            self.sock.sendto(message("/relay/subscribe"), self.dst); self.next_sub = time.monotonic() + 5
        try:
            d, _ = self.sock.recvfrom(2000)
        except socket.timeout:
            return None
        top = parse(d)[0]
        if top[0] != "bundle":
            return None
        t, els = top[1], top[2]
        head = els[0]
        if head[1] != "/relay/seq":
            return None
        seq, kind, part, parts = head[2]
        flags = {}
        if kind != "hello" and self.last_seq is not None and seq != (self.last_seq + 1) % 2147483648 and seq != 0:
            self.gaps += 1; flags["gap"] = True
        if kind != "hello" or seq != 0:
            self.last_seq = seq
        cmds = [(e[1], e[2]) for e in els[1:] if e[0] == "msg"]
        if kind == "hello" and cmds and cmds[0][0] == "/relay/hello":
            ver, stream, project, nfix, mapid = cmds[0][1]
            if self.stream not in (None, stream):
                flags["restarted"] = True; self.last_seq = None
            self.stream = stream
        local = t - self.offset if self.offset is not None else t
        return kind, t, local, cmds, flags


def main(argv):
    host = argv[1]
    rec_s = float(argv[argv.index("--record") + 1]) if "--record" in argv else 0
    out = argv[argv.index("--record") + 2] if "--record" in argv else None
    r = Receiver(host)
    best = r.ping()
    print("clock offset %.1f ms (round trip %.1f ms)" % (r.offset * 1000, r.rtt * 1000) if best else "no pong; is the relay on?")
    t_start = time.time(); take = {"initial": [], "events": []}; first = None
    while not rec_s or time.time() - t_start < rec_s:
        p = r.poll()
        if not p:
            continue
        kind, t, local, cmds, flags = p
        if kind == "event":
            lat = (time.time() - local) * 1000
            print("event  tag=%.3f  arrived %.0f ms after the DMX change  %s %s" % (t, lat, [c[0].split("/fixture/")[-1] + str(c[1]) for c in cmds][:3], flags or ""))
            if rec_s:
                first = first if first is not None else local
                take["events"].append({"at": round(local - t_start, 3), "osc": [{"a": a, "v": v} for a, v in cmds]})
        elif kind == "keyframe" and rec_s and not take["initial"]:
            take["initial"] = [{"a": a, "v": v} for a, v in cmds]
    if out:
        take.update(format=1, name="relay", duration=round(rec_s, 3), lead=0.045, scope="all", fixtures=[], truncated=False)
        json.dump(take, open(out, "w"))
        print("wrote %s: %d events, gaps=%d" % (out, len(take["events"]), r.gaps))


if __name__ == "__main__":
    main(sys.argv)
