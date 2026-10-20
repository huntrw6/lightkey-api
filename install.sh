#!/bin/sh
# Install lightkey-api for the current user: copies the agent, writes a LaunchAgent, starts it.
# Safe to re-run. Does not overwrite an existing config.json.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
APP="$HOME/Library/Application Support/LightkeyAPI"
PLIST="$HOME/Library/LaunchAgents/io.github.lightkey-api.plist"
LABEL=io.github.lightkey-api
PY=/usr/bin/python3

[ "$(uname)" = Darwin ] || { echo "macOS only (Lightkey is a macOS app)."; exit 1; }
$PY -c 'import sys; sys.exit(sys.version_info < (3, 9))' 2>/dev/null || { echo "Need /usr/bin/python3 3.9+ (install the Xcode Command Line Tools: xcode-select --install)."; exit 1; }

mkdir -p "$APP" "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
cp "$HERE/lightkey_api.py" "$APP/lightkey_api.py"
[ -f "$APP/config.json" ] || echo '{}' > "$APP/config.json"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>$PY</string><string>-u</string><string>$APP/lightkey_api.py</string><string>run</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>ProcessType</key><string>Interactive</string>
  <key>StandardErrorPath</key><string>$HOME/Library/Logs/lightkey-api.err</string>
  <key>StandardOutPath</key><string>$HOME/Library/Logs/lightkey-api.out</string>
</dict></plist>
EOF
plutil -lint "$PLIST" >/dev/null

UIDN=$(id -u)
launchctl bootout "gui/$UIDN/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$UIDN" "$PLIST"
echo "Installed. Agent: $APP/lightkey_api.py   Config: $APP/config.json   Log: ~/Library/Logs/lightkey-api.log"
echo
echo "Next (one time): python3 \"$APP/lightkey_api.py\" install-shortcut   then   python3 \"$APP/lightkey_api.py\" check"
