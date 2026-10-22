# lightkey-api

Read-only HTTP API for the live state of a running [Lightkey](https://lightkeyapp.com) on macOS. Reports which cues are active. Unofficial.

Python 3.9+, standard library only. The only dependency is Lightkey.

## Requirements

- macOS with Lightkey installed and a project open
- A logged-in, unlocked desktop session for the user running Lightkey (Shortcuts does not run at the login window)
- Automatic logout disabled on a show machine (`com.apple.autologout.AutoLogOutDelay`)
- Lightkey sACN output enabled (Preferences > DMX Output) for fast change detection. Without it the agent falls back to a 10 s poll.

## Setup

```sh
git clone https://github.com/huntrw6/lightkey-api && cd lightkey-api
./install.sh
python3 ~/Library/Application\ Support/LightkeyAPI/lightkey_api.py install-shortcut
python3 ~/Library/Application\ Support/LightkeyAPI/lightkey_api.py check
```

1. `install.sh` copies the agent and starts a LaunchAgent (`io.github.lightkey-api`).
2. `install-shortcut` builds and signs a Shortcut and opens it in Shortcuts. Click **Add Shortcut**.
3. `check` verifies Lightkey, the Shortcut and permissions, then prints the active cues.

On first run macOS asks `Allow "Lightkey API Active Cues" to access "Lightkey"?`. Click **Allow**.
If the prompt never appears, `check` times out. Quit the Shortcuts app, run `check` again from a Terminal on the Mac itself (not over SSH), and click Allow.

Optional, for fixture levels: export the fixture map from the project that Lightkey has open.

```sh
python3 ~/Library/Application\ Support/LightkeyAPI/lightkey_api.py export-fixtures
```

Lightkey keeps its project in `~/Downloads` or `~/Documents`, which macOS protects. If the command cannot read the file, give it the path from a Terminal on the Mac itself. Run it again after you patch or change fixtures. The agent loads the new map within a few seconds.

Remove with `./uninstall.sh` (`--purge` also deletes the config).

## Configuration

`~/Library/Application Support/LightkeyAPI/config.json`. All keys are optional. The defaults are at the top of `lightkey_api.py`.

| key | default | meaning |
|---|---|---|
| `port` | `8770` | HTTP port |
| `shortcut` | `"Lightkey API Active Cues"` | Shortcut name |
| `allow_interfaces` | `[]` | interfaces whose address may query the API, e.g. `["en5"]` |
| `sacn_interfaces` | `"auto"` | where to listen for sACN, or a list such as `["en5"]` |
| `sacn_universes` | `[1,2,3,4]` | universes watched for changes |

The API has no authentication. It answers on `127.0.0.1` only until an interface is listed in `allow_interfaces`. Use the wired control-network interface. Other addresses get `403`.

## Usage

```
GET /state        {"online": true, "active": ["Stars", "25% Frost"], "count": 2, "read_ms": 780, ...}
GET /log?since=N  cue changes with seq > N
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

`panAngle` and `tiltAngle` are in degrees and match the values that Lightkey's `panAngle` and `tiltAngle` OSC properties use. `lit` means the fixture produces light now. Each `/fixtures/log` event has `seq`, `t` (the first changed DMX frame, on the Lightkey Mac's clock), `universe` and `changes`, which holds only the properties that changed.

The agent shows the result of cues, presets, overrides and effects. It does not say which of them caused a change. Use `/state` for the active cues.

Timing, measured on a wired network:

- An OSC command (fade 0.0) changes the first DMX frame after about 40 ms.
- One read of the active cues takes about 0.8 s.
- A command shows in `/state` after 0.85 to 0.95 s.

The 0.8 s is the cost of reading. It is not a delay on the lights. For timing-critical work, send commands early. Use `/state` as confirmation.

Each `/log` event has `seq`, `added`, `removed`, `read_ms` and `effective_ms`. `effective_ms` is the best estimate of when the change happened, on the Lightkey Mac's clock. With `effective_src: "dmx"` it is the first changed DMX frame. With `"sample"` it is the read time.

To compare with another machine, measure the clock offset first. Use the lowest-round-trip sample of several `/time` calls. Measure it again each session. See `examples/client.py`.

Watch changes from another machine:

```sh
python3 examples/client.py 10.30.0.211
```

Drive Lightkey for testing over OSC on UDP 21600. Use `activate` and `deactivate`, which are idempotent. The `*` matches any control panel. The float argument is the fade in seconds.

```sh
python3 examples/osc_send.py 10.30.0.211 "Stars" activate
python3 examples/osc_send.py 10.30.0.211 "Stars" deactivate
```

`toggle` also works. `start`, `stop`, `on`, `off`, `trigger`, `release`, `enable`, `disable` and `flash` do nothing.

## Limits

- `/state` reports which cues are active. Fixture levels come from the DMX output, so they show results, not causes.
- Fixtures with a custom channel layout report that channel under its name from the profile. The agent does not know what the value means.
- Fixture levels need the fixture map and an sACN output that Lightkey sends. They do not work with Art-Net only.
- Resolution is one read (about 0.8 s). A change that happens and reverts inside one read can be missed.
- It needs a desktop session and the Shortcuts app. It cannot run headless.
- It relies on the Lightkey action name `GetActiveCuesIntent`. A future Lightkey release can change it.

## Development

```sh
python3 -m unittest discover -s tests
```


GNU GPL v3. See [LICENSE](LICENSE).
