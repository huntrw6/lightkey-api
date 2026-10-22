# lightkey-api

Read the live state of a running [Lightkey](https://lightkeyapp.com). The agent runs on the Lightkey Mac. It reads the DMX output and tells you the color, intensity, pan, tilt and every state of the fixtures in your current project file. It records Lightkey in OSC format and optionally streams every change to other computers.

## Install

```sh
curl -fsSL https://github.com/huntrw6/lightkey-api/releases/latest/download/install.sh | sh
```

The installer downloads the latest release, starts the agent at login, reads your fixtures, and checks that it answers. Run it again to upgrade to the latest version. Settings are persistent.

It needs the sACN output on (in Lightkey's DMX Output settings). Python 3 comes with the Xcode Command Line Tools (`xcode-select --install`). No other dependency. The agent is one 68 KB file and uses about 22 MB of memory.

## Use it from another computer

The API captures on the Lightkey Mac only, but it can answer on other comupters if you allow a network interface. Ethernet is recommended for reliability:

```sh
curl -fsSL https://github.com/huntrw6/lightkey-api/releases/latest/download/install.sh | sh -s -- --allow en0
```

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

After you add patches or change fixtures, run `export-fixtures` again:

```sh
python3 ~/Library/Application\ Support/LightkeyAPI/lightkey_api.py export-fixtures
```

If macOS blocks reading the project (it keeps projects in `~/Downloads` or `~/Documents`), run it from a Terminal or append the project path.

## Settings

`~/Library/Application Support/LightkeyAPI/config.json`. All keys are optional.

| key | default | meaning |
|---|---|---|
| `allow_interfaces` | `[]` | interfaces whose address may use the API, for example `["en0"]` |
| `port` | `8770` | HTTP port |
| `relay_port` | `21700` | UDP port of the live stream |
| `sacn_universes` | `[1,2,3,4]` | universes to read |
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
