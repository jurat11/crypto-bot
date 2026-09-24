# Run crypto-bot 24/7 on a free cloud server

This puts the bot on an always-on computer in a Google data center:
- It keeps trading its demo accounts while your Mac is off.
- You open the dashboard from your phone or Mac, privately.
- It takes about 20 minutes, most of it clicking through Google's sign-up.

**Cost: $0 on Google Cloud's free tier.** That covers one `e2-micro` server in `us-west1`, `us-central1` or `us-east1`, 30 GB of standard disk, and 1 GB of outbound data a month.
- Google asks for a card to verify you. Staying inside the free tier costs nothing.
- The free-tier terms can change, so check https://cloud.google.com/free before you start.
- Delete the server when you no longer need it.

**Pick a region in your own country.** Binance's testnet block depends on where you are. Picking a server abroad to get around it is exactly the workaround the spec rules out. From a US server, testnet shows "blocked by exchange location", just like on your Mac, and demo runs normally.

## 1. Create the server (in the browser)
1. Open https://console.cloud.google.com, sign in, create a project (for example `crypto-bot`), and turn on billing when asked. This is only for card verification.
2. Menu → **Compute Engine** → **VM instances**. Enable the API if asked, then click **Create instance**.
3. Choose these settings:
   - **Name:** `crypto-bot`
   - **Region:** `us-central1`, `us-east1` or `us-west1`
   - **Machine type:** `e2-micro` (General purpose → E2 → Shared core)
4. **Boot disk** → Change:
   - **Ubuntu 24.04 LTS** (x86/64)
   - **Standard persistent disk**, 30 GB
   - Click **Select**.
5. Leave the HTTP/HTTPS firewall boxes **unticked**. Nothing needs to be open to the internet. Click **Create**.
6. When the dot turns green, click **SSH** in its row. A terminal opens in your browser.

## 2. Install the bot (one paste)
In that SSH window, paste:
```bash
curl -fsSL https://raw.githubusercontent.com/jurat11/crypto-bot/main/deploy/install.sh | bash
```
Until the pull request is merged into `main`, use the branch instead:
```bash
curl -fsSL https://raw.githubusercontent.com/jurat11/crypto-bot/refs/heads/claude/trading-bot-backtest-dashboard-kpwh7o/deploy/install.sh | BRANCH=claude/trading-bot-backtest-dashboard-kpwh7o bash
```
The install:
- Sets the bot up as a service that starts at boot and restarts itself if it stops.
- On the first start, runs the backtest (about a minute) and then opens the demo accounts ($15 each, $20 for the variants).

To watch it, run `journalctl -u crypto-bot -f`. Ctrl+C stops watching; the bot keeps running.

## 3. Open the dashboard from your phone and Mac (Tailscale, free)
The dashboard only listens inside the server. Tailscale gives your own devices a private, encrypted link to it, and nothing is opened to the internet. It is only for reaching your own server: the bot's connections to Binance still go straight out from the server.

1. In the SSH window, run:
   ```bash
   curl -fsSL https://tailscale.com/install.sh | sh
   sudo tailscale up
   ```
   Open the link it prints and sign in (Google, Apple, GitHub or Microsoft).
2. Run:
   ```bash
   sudo tailscale serve --bg 8000
   ```
   It prints an address like `https://crypto-bot.tail1234.ts.net`. If it says HTTPS is not enabled for your tailnet, open the link it shows, click enable, and run the command again.
3. Install the **Tailscale** app on your phone and your Mac, and sign in with the same account.
4. Open the address from step 2 and bookmark it.

## 4. Settings
Edit the settings with `nano ~/crypto-bot/.env`. To save, press Ctrl+O, then Enter, then Ctrl+X. Then run `sudo systemctl restart crypto-bot`. The settings you can add:
- `DASHBOARD_PASSWORD=...`: recommended. The page then asks for it (any user name).
- `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`: every trade and risk block on your phone, plus a daily leaderboard at 00:15 UTC.
- `TESTNET_API_KEY` and `TESTNET_API_SECRET`: testnet keys, if you want to try testnet.

## Everyday commands (in the SSH window)
| What | Command |
|---|---|
| Is it running? | `systemctl status crypto-bot` |
| Live log | `journalctl -u crypto-bot -f` |
| Restart | `sudo systemctl restart crypto-bot` |
| Update to the latest code | run the install command from step 2 again |
| Stop new buys | the STOP button, or `touch ~/crypto-bot/STOP` (delete the file to resume) |
| Stop the bot | `sudo systemctl stop crypto-bot` (add `sudo systemctl disable crypto-bot` to keep it off after reboots) |
| Start every account over | `sudo systemctl stop crypto-bot && rm ~/crypto-bot/data/bot.db* && sudo systemctl start crypto-bot` |

## Good to know
- **The cloud bot has its own accounts.** They start fresh at $15 / $20; the accounts on your Mac stay on your Mac. Stop the Mac copy (Ctrl+C) so you are not comparing two versions.
- **Data allowance.** The free tier includes about 1 GB of outbound data a month. An open dashboard streams about 8 KB a second (roughly 30 MB an hour), so close the tab when you are not watching. Telegram alerts use almost nothing.
- **Memory.** The server has 1 GB of memory; the bot uses well under 200 MB, including the backtest.
- **Deleting it:** Compute Engine → VM instances → tick `crypto-bot` → Delete.
