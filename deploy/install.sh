#!/usr/bin/env bash
# Install (or update) crypto-bot on an always-on Ubuntu server and run it 24/7.
#
#   curl -fsSL https://raw.githubusercontent.com/jurat11/crypto-bot/main/deploy/install.sh | bash
#
# It installs Python and git, downloads the bot into ~/crypto-bot, installs the
# packages in a virtualenv, and registers a systemd service that starts at boot
# and restarts itself if it ever stops. Running it again updates the bot and
# restarts it. The dashboard listens on 127.0.0.1:8000 on the server only; see
# deploy/DEPLOY.md for opening it from your phone or Mac.
set -euo pipefail

REPO="${REPO:-https://github.com/jurat11/crypto-bot.git}"
BRANCH="${BRANCH:-main}"
DIR="${DIR:-$HOME/crypto-bot}"
PORT="${PORT:-8000}"
UNIT_PATH="${UNIT_PATH:-/etc/systemd/system/crypto-bot.service}"

echo "==> Installing system packages"
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git python3 python3-venv >/dev/null

echo "==> Downloading the bot ($BRANCH) into $DIR"
if [ -d "$DIR/.git" ]; then
  git -C "$DIR" fetch -q origin "$BRANCH"
  git -C "$DIR" checkout -q "$BRANCH"
  git -C "$DIR" pull -q --ff-only origin "$BRANCH"
else
  git clone -q -b "$BRANCH" "$REPO" "$DIR"
fi

echo "==> Installing Python packages"
python3 -m venv "$DIR/.venv"
"$DIR/.venv/bin/pip" install -q --upgrade pip
"$DIR/.venv/bin/pip" install -q -r "$DIR/requirements.txt"

if [ ! -f "$DIR/.env" ]; then
  cp "$DIR/.env.example" "$DIR/.env"
  echo "==> Created $DIR/.env (edit it later for Telegram, testnet keys, dashboard password)"
fi
chmod 600 "$DIR/.env"

echo "==> Registering the crypto-bot service"
sudo tee "$UNIT_PATH" >/dev/null <<UNIT
[Unit]
Description=crypto-bot demo engine and dashboard
After=network-online.target
Wants=network-online.target

[Service]
User=$(id -un)
WorkingDirectory=$DIR
ExecStart=$DIR/.venv/bin/python -m bot.server --no-browser --host 127.0.0.1 --port $PORT
Restart=always
RestartSec=15

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable -q crypto-bot
sudo systemctl restart crypto-bot

echo
echo "Done. The bot is running and will restart by itself after crashes and reboots."
echo "The first start runs the backtest (about a minute). Watch it with:"
echo "  journalctl -u crypto-bot -f"
echo "Dashboard on the server: http://127.0.0.1:$PORT  (see deploy/DEPLOY.md to open it from your phone or Mac)"
