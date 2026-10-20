#!/bin/sh
# Remove the LaunchAgent and the installed agent. Leaves the imported Shortcut and your config.json alone
# unless you pass --purge.
set -eu
APP="$HOME/Library/Application Support/LightkeyAPI"
PLIST="$HOME/Library/LaunchAgents/io.github.lightkey-api.plist"
launchctl bootout "gui/$(id -u)/io.github.lightkey-api" 2>/dev/null || true
rm -f "$PLIST" "$APP/lightkey_api.py"
if [ "${1:-}" = "--purge" ]; then rm -rf "$APP"; fi
echo "Removed. Delete the 'Lightkey API Active Cues' Shortcut in the Shortcuts app if you no longer want it."
