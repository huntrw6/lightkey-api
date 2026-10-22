# lightkey-api

Live state of a running [Lightkey](https://lightkeyapp.com) on macOS. It decodes the DMX output into per-fixture state, records takes as Lightkey OSC, and streams every change over the network. Unofficial.

Python 3.9+, standard library only. The only dependency is Lightkey.

## Requirements

- macOS with Lightkey installed and a project open
- Automatic logout disabled on a show machine (`com.apple.autologout.AutoLogOutDelay`)
- Lightkey sACN output enabled (Preferences > DMX Output) The agent decodes this stream.

## Setup

```sh
git clone https://github.com/huntrw6/lightkey-api && cd lightkey-api
./install.sh
python3 ~/Library/Application\ Support/LightkeyAPI/lightkey_api.py export-fixtures
```

1. `install.sh` copies the agent and starts a LaunchAgent (`io.github.lightkey-api`).
2. `export-fixtures` reads the fixture map from the project that Lightkey has open.

Lightkey keeps its project in `~/Downloads` or `~/Documents`, which macOS protects. If the command cannot read the file, give it the path from a Terminal on the Mac itself. Run it again after you patch or change fixtures. The agent loads the new map within a few seconds.

### Optional: active cue names

Lightkey does not give cue names over OSC or MIDI. The only way to read them is Lightkey's "Get Active Cues" Shortcuts action. This needs a logged-in, unlocked desktop session and a one-time approval.

```sh
python3 ~/Library/Application\ Support/LightkeyAPI/lightkey_api.py install-shortcut
```

1. `install-shortcut` builds and signs a Shortcut, opens it in Shortcuts, and sets `"cues": true` in the config.
2. Click **Add Shortcut**. Then restart the agent.
3. Run `lightkey_api.py check`. macOS asks `Allow "Lightkey API Active Cues" to access "Lightkey"?`. Click **Allow**.

If the prompt never appears, `check` times out. Quit the Shortcuts app, run `check` again from a Terminal on the Mac itself (not over SSH), and click Allow.

Without the Shortcut, `/state` and `/log` report no cue names. Everything else works.

Remove with `./uninstall.sh` (`--purge` also deletes the config).

## Configuration

`~/Library/Application Support/LightkeyAPI/config.json`. All keys are optional. The defaults are at the top of `lightkey_api.py`.

| key | default | meaning |
|---|---|---|
| `port` | `8770` | HTTP port |
| `cues` | `false` | read active cue names through the Shortcut |
| `shortcut` | `"Lightkey API Active Cues"` | Shortcut name |
| `relay` | `true` | stream fixture changes over OSC (see below) |
| `relay_port` | `21700` | UDP port of the relay |
| `relay_targets` | `[]` | fixed receivers, `"ip:port"`, that get the stream without subscribing |
| `allow_interfaces` | `[]` | interfaces whose address may query the API, e.g. `["en5"]` |
| `sacn_interfaces` | `"auto"` | where to listen for sACN, or a list such as `["en5"]` |
| `sacn_universes` | `[1,2,3,4]` | universes watched for changes |

The API has no authentication. It answers on `127.0.0.1` only until an interface is listed in `allow_interfaces`. Use the wired control-network interface. Other addresses get `403`.

## Usage

```
GET /state        (needs the Shortcut) {"online": true, "active": ["Stars", "25% Frost"], "count": 2, "read_ms": 780, ...}
GET /log?since=N  (needs the Shortcut) cue changes with seq > N
GET /events       Server-Sent Events: "cues" per change, "state" snapshots
GET /time         {"t": <ms>}  the Mac's clock, for measuring clock offset
GET /health       liveness, last error, age of the last DMX frame
```

`/state` reports `online: false` and `active: null` while Lightkey is closed.

With a fixture map, the agent also decodes the DMX output into the state of each fixture:

```
GET /fixtures               every fixture: name, profile, universe, address, properties
GET /fixtures/state         every fixture's properties now
GET /fixtures/log?since=N   timestamped property changes with seq > N
GET /levels                 raw DMX slots per universe (?u=1&u=2)
```

Properties are normalized to 0 to 1. A fixture reports only the properties its profile has, for example:

```
"P1":  {"color": [1.0, 0.0, 0.0], "lit": true}
"MH1": {"dimmer": 0.8, "panAngle": 90.0, "tiltAngle": 45.0, "pan": 0.64, "tilt": 0.67,
        "colorWheel": {"dmx": 10, "color": [1.0, 0.0, 0.0]}, "gobo": 0.11, "zoom": 1.0, "lit": true}
```

`panAngle` and `tiltAngle` are in degrees and use the same zero as Lightkey's OSC `panAngle` and `tiltAngle`. `lit` means the fixture produces light now.

### OSC output

Every change is also given as the Lightkey OSC commands that reproduce it. Replay needs no conversion.

```
GET /fixtures/osc                  commands for the current state (?fixture=MH1&fixture=P1 to select)
GET /fixtures/log?since=N          events: {"seq", "t", "universe", "changes", "osc"}
```

```
{"a": "/fixture/MH1/overrides/panAngle", "v": [90.0]}
{"a": "/fixture/MH1/overrides/gobo1",    "v": [5], "approx": true}
```

Send each command to Lightkey on UDP 21600. Use `a` as the OSC address and `v` as the arguments. A decimal is a float, a whole number is an integer, and text is a string. `approx` marks a command that Lightkey cannot map back exactly. Wheel positions are an example: the agent sees the DMX value and sends the wheel slot that contains it.

A color is sent as `color r g b` after a `dimmer` command. A fixture without a dimmer channel gets `dimmer 1.0`. Stop a replay with `/fixture/<name>/overrides/clear`, which gives control back to the cues.

### Relay

The agent can also send every change to other computers as OSC over UDP. A receiver subscribes with `/relay/subscribe` on port 21700. Each packet is an OSC bundle. The time tag is the time the change appeared on the DMX output, on the Lightkey computer's clock. The format, the clock offset method and a reference receiver are in [docs/relay.md](docs/relay.md) and `examples/relay_client.py`.

### Recording

A take is a file with the starting OSC state and timed OSC commands. It stays small, because only changes are stored.

```sh
curl -X POST localhost:8770/record/start -d '{"name": "Song 1"}'
curl -X POST localhost:8770/record/stop
curl localhost:8770/takes                 # saved takes
curl localhost:8770/takes/Song%201        # one take
curl -X DELETE localhost:8770/takes/Song%201
```

`/record/start` accepts `fixtures` and `scope`. `fixtures` is a list of names. The default is all fixtures. The default scope, `changed`, starts only the fixtures that change during the take. The cues and the operator keep everything else. With `all`, a replay sets every fixture.

```
{"format": 1, "name": "Song 1", "duration": 212.4, "lead": 0.045,
 "initial": [{"a": "/fixture/P1/overrides/dimmer", "v": [1.0]}, ...],
 "events":  [{"at": 3.25, "osc": [{"a": "/fixture/P1/overrides/color", "v": [1.0, 0.0, 0.0]}]}, ...]}
```

To replay, send `initial` at once. Then send each `events` entry `lead` seconds before its `at`. The `at` value is when the change appeared on the DMX output, in seconds from the start of the take. OSC reaches the lights about `lead` seconds later. Takes are in `~/Library/Application Support/LightkeyAPI/takes`.

The agent shows the result of cues, presets, overrides and effects. It does not say which of them caused a change. Use `/state` for the active cues.

## Development

```sh
python3 -m unittest discover -s tests
```


GNU GPL v3. See [LICENSE](LICENSE).
