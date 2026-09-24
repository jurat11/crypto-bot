#!/bin/bash
# Double-click this file in Finder (or run ./start.command) to start the bot and open the dashboard.
cd "$(dirname "$0")" || exit 1
python3 -m pip install -q -r requirements.txt || exit 1
python3 -m bot.server
