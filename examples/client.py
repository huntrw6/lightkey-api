#!/usr/bin/env python3
"""Minimal client: print cue changes as they happen, with the measured clock offset to the Lightkey Mac.

  python3 examples/client.py 10.0.0.20          # host running lightkey-api (port 8770)
"""
import json, sys, time, urllib.request

host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
base = "http://%s:8770" % host
get = lambda p: json.load(urllib.request.urlopen(base + p, timeout=3))


def clock_offset(samples=15):
    """server_clock - my_clock in ms, from the lowest-round-trip sample (NTP style). Re-measure each session."""
    best = None
    for _ in range(samples):
        t0 = time.time() * 1000
        r = get("/time")["t"]
        t1 = time.time() * 1000
        if best is None or t1 - t0 < best[0]:
            best = (t1 - t0, r - (t0 + t1) / 2)
    return best


rtt, off = clock_offset()
print("clock offset %+.1f ms (best round trip %.1f ms)" % (off, rtt))
seq = get("/log")["state"]["seq"]
print("watching; Ctrl-C to stop")
while True:
    for e in get("/log?since=%d" % seq)["events"]:
        seq = max(seq, e["seq"])
        if "added" in e:
            when = (e["effective_ms"] - off) / 1000.0     # on MY clock
            print("%s  +%s  -%s  (%s)" % (time.strftime("%H:%M:%S", time.localtime(when)) + ".%03d" % (when % 1 * 1000),
                                         e["added"], e["removed"], e["effective_src"]))
        else:
            print("Lightkey is now %s" % ("running" if e["online"] else "closed"))
    time.sleep(0.1)
