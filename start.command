#!/bin/bash
# Double-click this file in Finder (or run ./start.command) to start the bot and open the dashboard.
# caffeinate keeps the Mac awake while the bot runs (the screen can still turn off).
# Closing the lid or shutting down still stops the bot.
cd "$(dirname "$0")" || exit 1
python3 -m pip install -q -r requirements.txt || exit 1
if command -v caffeinate >/dev/null 2>&1; then
  exec caffeinate -is python3 -m bot.server
fi
exec python3 -m bot.server
