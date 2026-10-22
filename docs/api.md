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
| `GET /health` | liveness, last error, age of the last DMX frame |
| `GET /time` | `{"t": <ms>}` the Mac's clock, to measure clock offset |
| `GET /relay` | live stream status: port, subscribers, packets sent |
| `GET /state`, `GET /log?since=N`, `GET /events` | active cue names. Need the optional `lightkey_cues.py` and Shortcut |
