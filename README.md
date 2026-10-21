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

Remove with `./uninstall.sh` (`--purge` also deletes the config).

## Configuration

`~/Library/Application Support/LightkeyAPI/config.json`. All keys optional; defaults are at the top of `lightkey_api.py`.

| key | default | meaning |
|---|---|---|
| `port` | `8770` | HTTP port |
| `shortcut` | `"Lightkey API Active Cues"` | Shortcut name |
| `allow_interfaces` | `[]` | interfaces whose address may query the API, e.g. `["en5"]` |
| `sacn_interfaces` | `"auto"` | where to listen for sACN, or a list such as `["en5"]` |
| `sacn_universes` | `[1,2,3,4]` | universes watched for changes |

The API has no authentication. It answers on `127.0.0.1` only until an interface is listed in `allow_interfaces`. Use the wired control-network interface; other addresses get `403`.

## Usage

```
GET /state        {"online": true, "active": ["Stars", "25% Frost"], "count": 2, "read_ms": 780, ...}
GET /log?since=N  cue changes with seq > N
GET /events       Server-Sent Events: "cues" per change, "state" snapshots
GET /time         {"t": <ms>}  the Mac's clock, for measuring clock offset
GET /health       liveness, last error, age of the last DMX frame
```

`/state` reports `online: false` and `active: null` while Lightkey is closed.

Each `/log` event has `seq`, `added`, `removed`, `read_ms` and `effective_ms`, the best estimate of when the change happened on the Lightkey Mac's clock (`effective_src: "dmx"` is the first changed DMX frame; `"sample"` is the read time). To compare with another machine, measure the offset from the lowest-round-trip of several `/time` calls and re-measure each session. See `examples/client.py`.

Watch changes from another machine:

```sh
python3 examples/client.py 10.30.0.211
```

Drive Lightkey for testing (OSC, UDP 21600). Use `activate` and `deactivate`, which are idempotent. `*` matches any control panel; the float argument is the fade in seconds.

```sh
python3 examples/osc_send.py 10.30.0.211 "Stars" activate
python3 examples/osc_send.py 10.30.0.211 "Stars" deactivate
```

`toggle` also works. `start`, `stop`, `on`, `off`, `trigger`, `release`, `enable`, `disable` and `flash` do nothing.

## Development

```sh
python3 -m unittest discover -s tests
```

MIT licensed.
