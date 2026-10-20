#!/usr/bin/env python3
"""Send a Lightkey OSC cue command (Lightkey listens on UDP 21600). Not part of the agent; here for testing.

  python3 examples/osc_send.py 10.0.0.20 "Stars" activate        # idempotent; use this, not toggle, for replays
  python3 examples/osc_send.py 10.0.0.20 "Stars" deactivate
Verbs that work: activate, deactivate, toggle. The 0.0 argument is the fade time in seconds (0 = instant).
"""
import socket, struct, sys

host, cue, verb = sys.argv[1], sys.argv[2], sys.argv[3]
pad = lambda b: b + b"\0" * (4 - len(b) % 4)
addr = "/live/*/cue/%s/%s" % (cue, verb)            # '*' matches any control panel
socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(pad(addr.encode()) + pad(b",f") + struct.pack(">f", 0.0), (host, 21600))
print("sent", addr)
