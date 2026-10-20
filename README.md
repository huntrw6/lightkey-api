# lightkey-api

Live state of a running [Lightkey](https://lightkeyapp.com) (macOS), read over HTTP.

Lightkey can be *controlled* over OSC and MIDI but it sends nothing back, so a show controller cannot
ask "which cues are on right now?". `lightkey-api` is a small background agent that answers that question.

> Unofficial. Not affiliated with, or endorsed by, the makers of Lightkey. It only uses things Lightkey
> already exposes: its Shortcuts action, and the sACN DMX stream it already sends.

## What it gives you

```
GET /state        {"online": true, "active": ["Stars", "25% Frost", ...], "count": 11, "read_ms": 780, ...}
GET /log?since=N  cue changes after sequence N, each timestamped (see "Timing")
GET /events       Server-Sent Events: a "cues" event per change, "state" snapshots
GET /time         the Mac's clock in ms, to measure the clock offset between machines
GET /health       liveness, last error, age of the last DMX frame
```

It runs only while you are logged in, reports `online: false` (and `active: null`) when Lightkey is closed,
and never sends anything *to* Lightkey. It is read-only.

## How it works

1. A background loop checks whether Lightkey is running.
2. It listens to Lightkey's **sACN** (E1.31) multicast output. A change in the DMX data means "something
   happened", so a read is started right then instead of on a fixed timer.
3. It reads the active cues by running a one-action macOS **Shortcut** that uses Lightkey's own
   *Get Active Cues* action. The Shortcut returns one file per active cue.
4. Overlapping reads are allowed (max 3), results are applied newest-wins, failures back off.

Python 3.9+ standard library only (the `/usr/bin/python3` that ships with the Xcode Command Line Tools).
No packages to install. The only external dependency is Lightkey itself.

## Requirements

- macOS with Lightkey installed and a project open. Developed on macOS 15, Lightkey 4.
- A **logged-in desktop session** for the user that runs Lightkey (Shortcuts does not run at the login
  window). Turn off automatic logout (`com.apple.autologout.AutoLogOutDelay`) on a show machine.
- Lightkey's sACN output enabled (Preferences > DMX Output) for fast change detection. Without it the agent
  still works and falls back to a 10-second read.

## Install

```sh
git clone https://github.com/huntrw6/lightkey-api && cd lightkey-api
./install.sh                                              # copies the agent, starts a LaunchAgent
python3 ~/Library/Application\ Support/LightkeyAPI/lightkey_api.py install-shortcut
python3 ~/Library/Application\ Support/LightkeyAPI/lightkey_api.py check
```

`install-shortcut` builds and signs the Shortcut and opens it in the Shortcuts app: click **Add Shortcut**.
The first time the Shortcut runs, macOS asks `Allow "Lightkey API Active Cues" to access "Lightkey"?`: click
**Allow**. That prompt can fail to appear, in which case `check` just times out. If it does: quit the Shortcuts app,
then run `check` again **from a Terminal on the Mac itself** (not over SSH) while someone is looking at the
screen, and click Allow. `check` tells you exactly which step is missing.

Remove with `./uninstall.sh` (`--purge` also deletes the config).

## Configuration

`~/Library/Application Support/LightkeyAPI/config.json` (all keys optional; see the top of
`lightkey_api.py` for the full list and defaults):

| key | default | meaning |
|---|---|---|
| `port` | `8770` | HTTP port |
| `shortcut` | `"Lightkey API Active Cues"` | name of the imported Shortcut |
| `allow_interfaces` | `[]` | interfaces whose address may be queried, e.g. `["en5"]` |
| `sacn_interfaces` | `"auto"` | where to listen for sACN, or a list such as `["en5"]` |
| `sacn_universes` | `[1,2,3,4]` | universes watched for changes |

**Security:** there is no authentication. By default the API answers on `127.0.0.1` only. Add the interface
that faces your control network to `allow_interfaces` (use the wired one) and keep it off public networks.

## Timing (measured)

On a wired network, Lightkey Mac 2019 Intel MacBook Pro:

| step | time |
|---|---|
| OSC command (fade 0.0) to the first changed DMX frame | ~40 ms |
| One read of the active cues (the Shortcut) | ~0.8 s |
| OSC command until it shows in `/state` | ~0.85-0.95 s |

The 0.8 s is the cost of reading, **not** a delay on the lights. For timing-critical work, send commands
early and treat `/state` as confirmation. Each event in `/log` carries:

- `effective_ms` - best estimate of *when the change happened*, on the Lightkey Mac's clock: the first DMX frame
  that differed, or (`effective_src: "sample"`) the moment the read sampled Lightkey (about 425 ms after it
  started, +-25 ms).
- `added`, `removed`, `read_ms`, `seq`.

To use these on another machine, measure the clock offset first (the lowest-round-trip sample of several
`/time` calls; see `examples/client.py`) and re-measure every session: clocks drift.

## Controlling Lightkey (for reference)

Lightkey takes OSC on UDP 21600. `examples/osc_send.py` sends cue commands. Idempotent verbs, which are the
right ones for replay: `/live/*/cue/<name>/activate` and `/deactivate` (the `*` matches any control panel;
the float argument is the fade time in seconds). `toggle` also works; `start`, `stop`, `on`, `off`,
`trigger`, `release`, `enable`, `disable` and `flash` do nothing.

## Limits

- Reports **which cues are active**, not fader values, chase positions or fixture levels.
- Resolution is one read (~0.8 s); changes that happen and revert inside a read can be missed.
- Needs a desktop session and the Shortcuts app; it cannot run headless.
- Relies on Lightkey's Shortcuts action name (`GetActiveCuesIntent`). A future Lightkey release could change it.

## Development

```sh
python3 -m unittest discover -s tests
```

MIT licensed.
