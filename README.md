# lightkey-api

Read the live state of a running [Lightkey](https://lightkeyapp.com). The agent runs on the Lightkey Mac. It reads the DMX output and tells you the color, intensity, pan, tilt and every state of the fixtures in your current project file. It records Lightkey in OSC format and optionally streams every change to other computers.

## Install

```sh
curl -fsSL https://github.com/huntrw6/lightkey-api/releases/latest/download/install.sh | sh
```

The installer downloads the latest release, starts the agent at login, reads your fixtures, and checks that it answers. Run it again to upgrade to the latest version. Settings are persistent.

**Offline:** put `install.sh`, `lightkey_api.py`, `SHA256SUMS` (and `lightkey_cues.py` for `--cues`) in one folder and run `sh install.sh` there. The installer checks every file against `SHA256SUMS` and downloads nothing. Without `SHA256SUMS` next to it (a git clone) it copies the files as they are.

**Projects in Documents or Downloads: click Allow.** Lightkey projects usually live in `~/Documents` or `~/Downloads`, which macOS keeps from background programs, and it only asks about a program it can name. So the installer starts the agent through a tiny signed app called **Lightkey API** (it compiles it on the spot with the Mac's own developer tools; nothing is downloaded). After an install or upgrade the agent lists both folders once, so macOS asks "Lightkey API would like to access files in your Documents folder". Click **Allow** for each. The installer prints this and waits up to 20 seconds for the answer. The agent only uses the access to find and read your Lightkey project. If you clicked Don't Allow, run the installer again: it tells macOS to forget the answer, so you are asked once more (or switch on Lightkey API under System Settings > Privacy & Security > Files and Folders). `GET /health` shows each folder as `allowed`, `denied` or `unknown` under `watch.folders`.

It needs the sACN output on (in Lightkey's DMX Output settings). Python 3 comes with the Xcode Command Line Tools (`xcode-select --install`). No other dependency. The agent is one 68 KB file and uses about 22 MB of memory.

## Use it from another computer

The API captures on the Lightkey Mac only, but it can answer on other comupters if you allow a network interface. Ethernet is recommended for reliability:

```sh
curl -fsSL https://github.com/huntrw6/lightkey-api/releases/latest/download/install.sh | sh -s -- --allow en0
```

If you know the address of the other computer, let the installer pick the interface that reaches it (it asks `route`; the address below is the other computer's):

```sh
curl -fsSL https://github.com/huntrw6/lightkey-api/releases/latest/download/install.sh | sh -s -- --allow-for 10.30.0.5
```

If the other computer reaches this Mac on a different network than this Mac uses to reach it, name this Mac's own address instead (the one the other computer is set to connect to). The installer allows the interface that has that address, and ignores `--allow-for`:

```sh
curl -fsSL https://github.com/huntrw6/lightkey-api/releases/latest/download/install.sh | sh -s -- --allow-address 10.30.0.211
```

`--allow` and `--allow-for` can be used together. StagePilot's setup command passes both `--allow-for` and `--allow-address` (when its Lighting address is a local IP).

Then, from the other computer you can poll the API (replace the address):

```sh
curl http://10.30.0.211:8770/fixtures/state     # current fixture states
curl http://10.30.0.211:8770/fixtures/osc       # current fixture states in Lightkey OSC format
```

## Capture during songs with timecode

```sh
curl -X POST http://10.30.0.211:8770/record/start -d '{"name": "Song 1"}'
curl -X POST http://10.30.0.211:8770/record/stop
curl http://10.30.0.211:8770/takes/Song%201
```

A take holds Lightkey OSC commands with times. To replay it, send them to Lightkey on UDP 21600. Details: [docs/api.md](docs/api.md).

## Stream changes live

Other computers can subscribe to the UDP stream of every fixture change, with the DMX output and its timecode. [docs/relay.md](docs/relay.md) describes it. `examples/relay_client.py` is a receiver.

## Fixtures changed?

Since 0.5.0 the agent keeps the fixture map current by itself. Every `project_check_s` seconds (default 180) it looks at the project Lightkey has open. When that file changed (another project, or a saved patch change) it reads it again and replaces the map. Saving a project without changing its fixtures leaves the map, and the map id the live stream sends, untouched. If Lightkey is closed, has no saved project, or the file cannot be read, the agent keeps the earlier map and says why in `GET /health` (`watch.state`).

To read the project right now, for example after adding a fixture to the open project:

```sh
curl -X POST http://127.0.0.1:8770/fixtures/refresh
```

The manual command still works, and also takes a project path:

```sh
python3 ~/Library/Application\ Support/LightkeyAPI/lightkey_api.py export-fixtures
```

## Settings

`~/Library/Application Support/LightkeyAPI/config.json`. All keys are optional.

| key | default | meaning |
|---|---|---|
| `allow_interfaces` | `[]` | interfaces whose address may use the API, for example `["en0"]` |
| `port` | `8770` | HTTP port |
| `relay_port` | `21700` | UDP port of the live stream |
| `sacn_universes` | `[1,2,3,4]` | universes to read |
| `project_check_s` | `180` | seconds between checks of the open project. Never below 30 |
| `cues` | `false` | read active cue names (needs `lightkey_cues.py`, see below) |

The API is passwordless. Only allow on a trusted network.

## Optional: active cue names

Lightkey gives cue names only through its Shortcuts action. This part is a second file, `lightkey_cues.py`, and is off unless you install it. It needs a logged-in desktop and one click:

```sh
curl -fsSL https://github.com/huntrw6/lightkey-api/releases/latest/download/install.sh | sh -s -- --cues
python3 ~/Library/Application\ Support/LightkeyAPI/lightkey_api.py install-shortcut
```

Click **Add Shortcut**. Restart the agent and Run `lightkey_api.py check`. Click **Allow** when macOS asks. If no prompt appears, quit Shortcuts. Then run `check` from Terminal. This adds `/state`, `/log` and `/events`.

## Limits

- Fixtures show the result of cues, overrides and effects. The agent cannot say which one caused a change.
- A replay holds the fixtures it controls. Send `/fixture/<name>/overrides/clear` to give them back to the cues.
- Wheel and gobo positions replay to the right slot, not the exact DMX value. Pan and tilt speed have no OSC command.
- Over OSC, Lightkey sees a multi-beam fixture as one fixture. It gets the average color.
- Over Wi-Fi, sACN timing is unreliable. Use a wired network.

## Uninstall

```sh
curl -fsSL https://github.com/huntrw6/lightkey-api/releases/latest/download/uninstall.sh | sh
```

Add `-s -- --purge` after `sh` to delete settings.

GNU GPL v3. See [LICENSE](LICENSE).
