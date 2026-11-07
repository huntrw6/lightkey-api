# API

All answers are JSON. Replace `HOST` with the Lightkey Mac. Port 8770.

## Fixtures

| request | answer |
|---|---|
| `GET /fixtures` | every fixture: name, profile, universe, address, properties |
| `GET /fixtures/state` | the properties of every fixture now |
| `GET /fixtures/osc` | Lightkey OSC commands that give the same state. `?fixture=MH1&fixture=P1` selects fixtures |
| `GET /fixtures/log?since=N` | changes with `seq` above N: `{"seq", "t", "universe", "changes", "osc"}` |
| `GET /levels` | raw DMX per universe. `?u=1&u=2` selects universes |

A fixture lists only the properties its profile has:

```
"P1":  {"color": [1.0, 0.0, 0.0], "lit": true}
"MH1": {"dimmer": 0.8, "panAngle": 90.0, "tiltAngle": 45.0, "gobo": 0.11, "zoom": 1.0, "lit": true}
```

Values are 0 to 1. `panAngle` and `tiltAngle` are degrees, with the same zero as Lightkey's OSC. `lit` means the fixture makes light now.

## OSC commands

```
{"a": "/fixture/MH1/overrides/panAngle", "v": [90.0]}
{"a": "/fixture/MH1/overrides/gobo1",    "v": [5], "approx": true}
```

Send each command to Lightkey on UDP 21600. `a` is the address. `v` is the list of arguments: a decimal is a float, a whole number is an integer, text is a string. `approx` means the DMX value fell inside a wheel slot, so the slot was sent.

A color follows a `dimmer` command. A fixture without a dimmer channel gets `dimmer 1.0`.

## Fixture map

The agent re-reads the open Lightkey project every `project_check_s` seconds (default 180, minimum 30) and replaces the fixture map only when the fixtures differ. Names are base names only; no answer contains a path or user name.

`POST /fixtures/refresh` does the same read now. Empty body. It is available on the same interfaces as every other endpoint, and one read runs at a time.

| status | body |
|---|---|
| 200 | `{"ok": true, "project": "Show.lightkeyproj", "count": 66, "map": 1794027080, "changed": true}`. `changed` is false when the fixtures are the same as the current map. The map and its `map` id are then left untouched |
| 409 | `{"ok": false, "code": "lightkey_closed" \| "no_project" \| "busy", "error": "..."}` |
| 403 | `{"ok": false, "code": "permission_denied", "folder": "Documents", "error": "macOS has not allowed this helper to read your Documents folder. Open System Settings > Privacy & Security > Files and Folders and allow python3, then press Read fixtures again."}`. `folder` is `Documents`, `Downloads` or null |
| 422 | `{"ok": false, "code": "unreadable", "error": "..."}` |

On every error the earlier map stays in use.

## Takes

| request | effect |
|---|---|
| `POST /record/start` | start. Body: `{"name": "Song 1", "fixtures": ["MH1"], "scope": "changed"}`. All keys are optional |
| `POST /record/stop` | stop and save |
| `GET /record` | recording status |
| `GET /takes`, `GET /takes/NAME` | list takes, read one |
| `DELETE /takes/NAME` | delete one |

`fixtures` limits the take to those fixtures. `scope` `changed` (default) starts only the fixtures that change during the take. `all` starts every fixture. Files are in `~/Library/Application Support/LightkeyAPI/takes`.

```
{"format": 1, "name": "Song 1", "duration": 212.4, "lead": 0.045,
 "initial": [{"a": "/fixture/P1/overrides/dimmer", "v": [1.0]}, ...],
 "events":  [{"at": 3.25, "osc": [{"a": "/fixture/P1/overrides/color", "v": [1.0, 0.0, 0.0]}]}, ...]}
```

To replay: send `initial` at once. Then send each `events` entry `lead` seconds before its `at`. OSC takes about `lead` seconds to reach the lights. When done, send `/fixture/<name>/overrides/clear` for each fixture. Send it twice. A single wildcard clear can leave fixtures held.

## Other

| request | answer |
|---|---|
| `GET /health` | liveness, last error, age of the last DMX frame, version, fixture map and project watcher (below) |
| `GET /time` | `{"t": <ms>}` the Mac's clock, to measure clock offset |
| `GET /relay` | live stream status: port, subscribers, packets sent |
| `GET /state`, `GET /log?since=N`, `GET /events` | active cue names. Need the optional `lightkey_cues.py` and Shortcut |

## /health

```
{"ok": true, "lightkey": true, "error": null, "dmx_age_ms": 23, "reads": 0,
 "version": "0.5.0",
 "fixture_map": {"project": "Show.lightkeyproj", "count": 66, "read_at": 1794027080123, "map": 1794027080},
 "watch": {"enabled": true, "interval_s": 180, "last_check_at": 1794027200000, "open_project": "Show.lightkeyproj",
           "state": "ok", "folder": null, "detail": null,
           "folders": {"Documents": "allowed", "Downloads": "allowed"}}}
```

- `reads` is present only when `cues` is on. A helper older than 0.5.0 has no `version`.
- `fixture_map` is null until a map exists. `map` is the number the relay hello sends as its last value (the modification time of `fixtures.json`, in seconds). `read_at` is when the project was last read, in ms since the epoch.
- `watch.state`: `ok` the map matches the open project; `changed` the map was replaced in the latest check; `lightkey_closed`; `no_project` (Lightkey has no saved project open); `permission_denied` (`folder` and `detail` say what to allow); `unreadable` (the file could not be read or the map not saved). In every state except `ok` and `changed` the earlier map stays in use.
- `watch.folders`: whether macOS lets the agent list `~/Documents` and `~/Downloads`: `allowed`, `denied` or `unknown`. The agent lists them once after an install or upgrade (flag file `tcc_primed` in the settings folder), and checks a denied folder again at each project check.
