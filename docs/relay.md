# Relay

The relay sends decoded fixture changes from the Lightkey computer to other computers as OSC over UDP. It is built for a receiver that records or replays the show in sync with other media.

## Transport

- UDP, port `relay_port` (default 21700), on every address that can query the HTTP API (`allow_interfaces`).
- Use the wired control network. Multicast is not used, so the stream crosses routers.
- Each packet is one [OSC 1.0 bundle](https://opensoundcontrol.stanford.edu/spec-1_0.html). Packets stay under `relay_mtu` bytes (default 1400).
- The bundle time tag is the event time: Unix seconds on the Lightkey computer's clock, NTP format (about 0.2 ns steps). For an event, it is the time the first changed DMX frame was received.

## Bundle layout

The first element is always `/relay/seq`:

```
/relay/seq  seq:int  kind:string  part:int  parts:int
```

| kind | following elements |
|---|---|
| `event` | Lightkey fixture commands that changed |
| `keyframe` | Lightkey fixture commands for every fixture (the full state) |
| `hello` | `/relay/hello version:int stream:int project:string fixtures:int map:int` |

Fixture commands are the Lightkey OSC messages that reproduce the state, for example `/fixture/MH1/overrides/panAngle 90.0`. A receiver can forward them to Lightkey unchanged. Commands that cannot be exact carry no marker here. Read `GET /fixtures/osc` for the `approx` flags.

`seq` counts up by 1 for each packet sent to one subscriber and wraps at 2^31. A command set that does not fit one packet is split in `parts`. All parts share the time tag and are applied together.

## Subscribing

| Send | Effect |
|---|---|
| `/relay/subscribe [port]` | start the stream to the sender (to `port` if given). Replies with a hello, then a keyframe. Renew at least every `relay_ttl_s` (15 s). Renew every 5 s. |
| `/relay/unsubscribe [port]` | stop the stream |
| `/relay/keyframe` | send a keyframe now |
| `/relay/ping t1:double` | reply `/relay/pong t1 t2 t3` (doubles: your t1, server receive time, server send time) |

`relay_targets` lists fixed receivers (`"ip:port"`) that get the stream without subscribing. The agent also sends keyframes every `relay_keyframe_s` (10 s) and when the fixture map changes.

## Receiver rules

1. **Clock offset.** Send 10 or more pings. For each, `rtt = (t4 - t1) - (t3 - t2)` and `offset = ((t2 - t1) + (t3 - t4)) / 2`. Keep the sample with the smallest `rtt`. Then `local_time = tag - offset`. Repeat every few minutes.
2. **Loss.** UDP can lose packets. If `seq` skips, apply the next keyframe, or send `/relay/keyframe`. A keyframe replaces all state.
3. **Restart.** A new `stream` value in a hello means the agent restarted. Start the sequence count again.
4. **Recording.** Store each event as `at = local_time - take_start`. Use the take format in the README: `initial` from the first keyframe, then `events`. OSC reaches the lights about 45 ms after you send it. Replay each event `lead` seconds early.
5. **Order.** Apply events in `seq` order, not arrival order.

## Measured

Between the Graphics iMac and the Lights Mac over the wired path:

| | |
|---|---|
| Clock ping round trip | 0.9 ms |
| OSC command to DMX change | 37 to 51 ms |
| DMX change to packet received | 1 to 2 ms |
| Lost or out-of-order packets in 30 events | 0 |

`examples/relay_client.py` is a standalone reference receiver that prints these values and can write a take file.

## Moving to Rust

The wire format is the interface. A Rust relay can replace the Python one with no change to a receiver. The pure logic (fixture map, DMX to properties, properties to OSC) is in `Fixtures` and `LevelTracker`, with golden tests in `tests/test_fixtures.py`. Reuse the tests as golden vectors for the port.
