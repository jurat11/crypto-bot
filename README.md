# crypto-bot: BTC/ETH strategies on $15 demo accounts, with a live dashboard

Four spot strategies (long or cash, BTC and ETH) each run their own **$15 demo account** on **real live Binance prices**. A fifth account buys 50/50 BTC/ETH and holds, as the benchmark. You watch every balance, decision and fill update every second in your browser. Nothing touches real money.

- **DEMO (always on):** each account starts with $15 at first launch and is saved in `data/bot.db`, so it survives restarts. Every order fills against the **live order book** (the bot walks it level by level for the order size), then pays Binance's 0.1% taker fee and follows Binance's real order rules ($5 minimum order, quantity steps).
- **TESTNET (TESTNET=1 in `.env`):** the selected strategy (`engine.testnet_strategy` in `config.json`, default TREND_D1) also sends real MARKET orders to Binance's official Spot Testnet, which uses fake funds. This shows real order placement and fills. From your location, testnet.binance.vision answers HTTP 451 ("restricted location"). The dashboard then shows **"TESTNET: blocked by exchange location"**, re-checks at most once every 15 minutes, and demo keeps running. If testnet becomes reachable, it starts working with no code change. There are no VPN or proxy workarounds, on purpose.
- **LIVE: off.** `bot/broker.py` `LiveBroker` still refuses to start. The code never reads real exchange keys and never calls api.binance.com trading endpoints.

## Quick start (Mac)
First time: paste this into Terminal. It downloads the bot into a folder named `crypto-bot-live` in your home folder, installs 4 small packages, and starts everything:
```bash
cd ~
git clone https://github.com/jurat11/crypto-bot.git crypto-bot-live
cd crypto-bot-live
python3 -m pip install -r requirements.txt
python3 -m bot.server
```
On the **first launch** it runs the backtest (about a minute), prints the table, and only then starts the demo accounts. A strategy with no backtest result never trades. Your browser then opens **http://localhost:8000**, and the same table is on the dashboard. Leave the Terminal window open while the bot runs. Ctrl+C stops it.

Next time: double-click `start.command` in the `crypto-bot-live` folder, or run `cd ~/crypto-bot-live && python3 -m bot.server`. Every account carries on where it left off.

Optional: `cp .env.example .env`, then add testnet keys and/or Telegram (see below). `python3 -m unittest discover tests` runs the offline tests (about 3 seconds). `python3 backtest_strategies.py` re-runs the backtest on fresh data.

Which strategies may trade is set by `engine.strategies` in `config.json` (all four by default). The Hold 50/50 benchmark always runs.

## Keep it running
The bot runs **on your computer**. If the Mac is shut down, asleep, or the Terminal window is closed, nothing happens: no prices, no decisions, no trades, and no stop-losses. When you start it again, every account picks up where it left off:
- Coins it holds are valued at the new price, so price moves while it was off do show up in P&L.
- Signals that came and went while it was off are simply missed.

- **Mac:** start it with `start.command` (or `caffeinate -is python3 -m bot.server`), keep it plugged in, and keep the lid open. The screen can turn off. `caffeinate` stops the Mac from sleeping, but closing the lid still puts it to sleep.
- **24/7 without your Mac:** follow [deploy/DEPLOY.md](deploy/DEPLOY.md). It covers a free Google Cloud server, a one-paste install (`deploy/install.sh`) that runs the bot as a service and restarts it by itself, and opening the dashboard privately from your phone with Tailscale. Set `DASHBOARD_PASSWORD` in `.env` whenever the dashboard can be reached from another device.

## The strategies
| Account | Timeframe | Rule |
|---|---|---|
| TREND_D1 | daily close | The validated baseline in `bot/strategy.py`, unchanged: hold while the close is above the 50-day SMA, sized by a 40% volatility dial. |
| TREND_H4 | 4h close | The same idea on 4-hour candles: close above SMA300 (50 days x 6 bars), vol dial over 30 days. |
| BREAKOUT_H1 | 1h close | Buy when the hourly close breaks the prior 48h high while price is above the daily SMA50. Exit on a close below the prior 24h low, or a 3% trailing stop from the highest close since entry. |
| MEANREV_H1 | 1h close | Buy when RSI(2) on 1h is below 5 while price is above the daily SMA50. Sell when RSI(2) is above 70, after 12 hours, or at a 3% stop. |
| Hold 50/50 | none | Buys $7.50 BTC and $7.50 ETH at start and never trades again. |

**Variations ($20 each, for comparing short-term):** three copies of TREND_D1 that each change one thing, set in `engine.variants` in `config.json`:
| Account | What changes | Why it's there |
|---|---|---|
| D1_ALL_IN | no volatility dial: always the full sleeve when the trend is up | riskier: bigger positions, bigger swings |
| D1_FAST | SMA20 instead of SMA50 | riskier and more trades: switches more often and pays more fees |
| D1_HIGH_FEE | pays 0.4% per trade instead of 0.1% | shows what a small account on a US exchange pays |

They go through the same backtest before they trade. None of them uses leverage.

**More coins ($20 each):** altcoins, memecoins and gold, each as a pair: the TREND_D1 rule, plus a "just hold" benchmark on the same coins. They are set in `engine.variants` in `config.json`, where you can add or swap coins.
| Account | Coins | Rule |
|---|---|---|
| ALTS_TREND / ALTS_HOLD | SOL, XRP | hold while the daily close is above SMA50, else cash / buy once and hold |
| MEME_TREND / MEME_HOLD | DOGE, PEPE | same pair of rules |
| GOLD_TREND / GOLD_HOLD | PAXG, a token backed by 1 troy ounce of gold | same pair of rules |

- **Half the account per coin.** The per-coin cap in the risk rules is 50%, so the gold accounts keep the other half in cash.
- **No volatility dial.** With $10 per coin, the dial would size memecoin orders below Binance's $5 minimum.
- **No silver.** Binance spot has no silver token.
- **Short histories.** PEPE only started trading in 2023, so it has no 2018-22 in-sample data. It is judged on 2023+ alone, and the table shows "n/a" for in-sample.

The first launch after adding coins downloads their price history for the backtest, which takes a few minutes. A coin without a live price only pauses the accounts that use it.

Each strategy runs a 50% BTC and a 50% ETH sleeve and decides **only on closed candles of its own timeframe**. There is no leverage, margin, futures, martingale, averaging down or grid logic anywhere.

**Backtest first.** `python3 backtest_strategies.py` runs the same decision code on Binance hourly candles since 2017. It reports **in-sample 2018-2022** (where rules are chosen) against **out-of-sample 2023 to today** (never used for choosing), at **0.1% and 0.4% fees**. A strategy that loses money out of sample at either fee still runs in demo, but the dashboard flags it **FAILED BACKTEST**. Parameters are never tuned to make a strategy pass.

## What each panel shows
1. **Banner** (top): always "DEMO: no real money". With TESTNET=1 there is a second banner: "TESTNET: fake funds, real orders." when it works, or the reason it does not (for example "blocked by exchange location"). A red "STOPPED" banner appears while the STOP file exists.
2. **Leaderboard:** all demo accounts, sorted by P&L. For each: balance to 4 decimals (it flashes green or red as prices move), P&L in $ and %, max drawdown since start, trade count, win rate (share of sells that made money after fees), and fees paid. Badges: *FAILED BACKTEST / backtest passed / not backtested*, *benchmark*, and *TESTNET* for the strategy mirrored to testnet. Under each name you see its cash and the $ value held in BTC and ETH.
3. **P&L since start (%):** every account on one chart. It uses percent because accounts start with $15 or $20. There is one point per minute, and the last point moves every second. Hover to read every account at one time. The leaderboard shows the same numbers as a table.
4. **Backtest before demo:** the in-sample (2018-22) and out-of-sample (2023+) table for each strategy at 0.1% and 0.4% fees, next to Hold 50/50, with the PASSED / FAILED BACKTEST verdict.
5. **What history says:** for each account, in dollars at its own size and fee: the typical (median) day, week and year from the 2023-2026 backtest, the range that 8 out of 10 periods fell in, how often each period lost money, and trades per week. **This is history, not a forecast.** 2023-2026 was mostly a rising market for crypto, and the year figures rest on only about three separate years.
6. **Live prices:** BTC and ETH mid price, bid, ask, spread in $ and basis points, where the price came from (websocket or REST fallback), and how old it is.
7. **What each strategy is doing:** one plain-English line per coin, for example "BTC: waiting, BTC is 1.8% below the 48h high (86,000)", plus the time of its next decision.
8. **Risk per account:** green OK or red BLOCKED with the reasons: kill switch, stale prices (older than 3 minutes), daily loss over 8% (UTC day), drawdown over 35%. A block stops buys. Sells that reduce risk still go through. Risk is checked every minute and again right before every buy.
9. **STOP button:** after you confirm, it creates the `STOP` file, and new buys halt at once (the risk panel turns red within a minute). Delete the `STOP` file to resume.
10. **Activity:** every check, signal, order, fill, risk event and testnet result, with UTC timestamps. The chips filter it; the once-a-minute "checks" are hidden unless you turn them on.
11. **Orders & fills:** every demo and testnet order: quantity, average fill price, $ value, fee, and slippage against the mid price. For testnet fills, **"Real vs sim slip"** compares what the exchange actually filled with what the simulator predicted from the same testnet order book. Testnet prices are labelled TESTNET, because testnet prices can differ from the real market.

Balances are realized and marked-to-market numbers after fees. Holdings are valued at the live mid price, and fees already paid are deducted. The only forward-looking numbers are the "what history says" ranges, which come from the backtest and are labelled as history.

## Getting testnet keys (fake money)
1. Go to https://testnet.binance.vision and click **Log In with GitHub**.
2. Click **Generate HMAC_SHA256 Key**, give it any label, and copy the **API Key** and **Secret Key**. The secret is shown only once.
3. Put them in `.env` (never in `config.json`, a commit or a chat):
   ```
   TESTNET=1
   TESTNET_API_KEY=...
   TESTNET_API_SECRET=...
   ```
4. Check it: `python3 -m bot.testnet --status`. For a one-off ~$15 BTC buy and sell that shows up in the dashboard table: `python3 -m bot.testnet --roundtrip`.

Testnet balances are fake and Binance resets them from time to time. If testnet.binance.vision answers HTTP 451 from where you are, the status says "blocked by exchange location". That is expected, and nothing else is affected.

How testnet orders stay safe:
- Every order gets a fixed `newClientOrderId` (strategy, coin, side and candle time). It is saved in SQLite *before* sending, so the same decision can never go out twice.
- A timeout is resolved by looking the order up, never by sending it again.
- Quantities respect LOT_SIZE, MARKET_LOT_SIZE and MIN_NOTIONAL from `/api/v3/exchangeInfo`.

## What $15 really buys
- **Binance's minimum order is $5.** Each coin's sleeve is $7.50, so a vol-dial position under ~67% (under $5) cannot be placed, and TREND_D1's small 10% rebalances ($0.75) are skipped. The activity feed says so each time.
- **Memecoins trade in whole units.** $10 of PEPE is about a million tokens, and prices show 6 significant digits.
- **Quantities come in exchange steps** (0.00001 BTC, about $0.85 at today's price, and 0.0001 ETH, about $0.27). A buy takes the step nearest its target, as long as that stays under the 50% per-coin cap; otherwise it rounds down. Selling can leave a few cents of "dust".
- Every round trip pays about 0.2% in fees plus the spread. The leaderboard shows fees paid, so you can see how much they add up to.

## Files
| File | What it does |
|---|---|
| `bot/server.py` | `python3 -m bot.server`: starts the feed, the engine, the testnet worker, and the FastAPI dashboard (Server-Sent Events every second) |
| `bot/static/index.html` | the dashboard page (Chart.js from a CDN) |
| `bot/engine.py` | per-minute marks, risk checks, decisions on closed candles, fills, receipts |
| `bot/market.py` | websocket feed (`kline_1m`, `bookTicker`) with auto-reconnect, 5 s REST fallback, closed-candle store |
| `bot/strategies.py` | the four strategies and their plain-English state lines |
| `bot/orderbook.py` | market-order fill simulation against the live book |
| `bot/exchange.py` | generic exchange adapter, Binance signing, exchange-filter rounding |
| `bot/testnet.py` | testnet gate (451 handling), idempotent order sender, `--status` / `--roundtrip` |
| `bot/accounts.py`, `bot/db.py` | $15 accounts and the SQLite store (`data/bot.db`: accounts, equity, receipts, fills, events) |
| `bot/alerts.py` | optional Telegram: every trade, every risk block, daily 00:15 UTC leaderboard |
| `backtest_strategies.py` | the in-sample/out-of-sample table for all four strategies |
| `start.command` | double-click launcher for the Mac |
| `deploy/install.sh`, `deploy/DEPLOY.md` | run it 24/7 on a free cloud server |
| `bot/net.py` | TLS certificates (falls back to certifi on python.org Python for Mac); verification is never turned off |
| `bot/devmarket.py` | `--fake-market` for working on the page offline (made-up prices, `data/dev.db`, labelled FAKE) |

Every decision writes a **receipt** to `data/bot.db` (table `receipts`). It records the inputs, the signal, the risk result, the order, and the fill price, fee and slippage against the mid price. To look at receipts: `sqlite3 data/bot.db "select * from receipts order by id desc limit 20"`.

To start all accounts over at $15, stop the server and delete `data/bot.db`.

---

## The original daily paper bot (`run.py`)

A daily bot that holds BTC and ETH while each one is above its 50-day average and moves to cash when it drops below. Position size shrinks automatically when the market gets wild. It uses only the Python standard library, needs no API keys, and costs $0 to run. The TREND_D1 account above runs the same rule.

**Status: paper mode.** It trades a simulated $20 against real prices, with realistic fees (0.4%) and slippage. Nothing touches real money. Read "Going live" before changing that.

### Why this strategy (short version of RESEARCH.md)
- I tested article #20's "find the best hour" hack on 8 years of BTC data. The best hours in 2018-22 did not stay the best in 2023-26 (correlation 0.01), and fees eat any single-hour edge. It was dropped.
- Of six candidate strategies chosen on 2018-22 data, only the SMA50 trend filter stayed strong on the unseen 2023-26 data for both BTC and ETH. It also held up across SMA lengths from 40 to 150.
- The default settings (0.4% fees, volatility dial) returned 34% a year over 2018-2026 with a 44% worst drawdown. Buy and hold returned 30% a year with a 77% drawdown. Out-of-sample alone: 37% a year with a 26% drawdown.
- It trails buy-and-hold in strong bull years. Only about 1 in 4 trades wins. The edge is losing less in crashes. **Past results do not predict the future.**

### How it works
| Piece | File | Idea from the articles |
|---|---|---|
| Price data (Binance public, Coinbase fallback) | `bot/data.py` | #20, #36 |
| Signal: SMA50 filter + volatility dial | `bot/strategy.py` | #20 method, checked out of sample |
| Risk manager in plain code: kill switch, stale-data guard, daily loss limit, drawdown halt, per-asset cap. A block stops buys, but sells that cut risk still go through | `bot/risk.py` | #22, #6 (gate on blast radius) |
| Paper broker with fees and slippage | `bot/broker.py` | #22 phase 3 |
| Ledger (`logs/ledger.csv`, `logs/daily.csv`), `MEMORY.md`, `learnings.md` | `bot/ledger.py` | #36 trade ledger |
| Telegram alerts (optional) | `bot/notify.py` | #36, #32 |
| Free daily hosting on GitHub Actions | `.github/workflows/daily.yml` | |

### Run it on your Mac (Terminal, from this folder)
```bash
python3 -m unittest discover tests    # offline tests
python3 run.py --dry                  # see today's signal, change nothing
python3 run.py                        # one paper-trading step
python3 backtest.py                   # re-run the research on fresh data
python3 hour_test.py BTC              # the article #20 hour test
```
Run `python3 run.py` once a day, any time after 00:10 UTC (8:10 pm New York time). Running it more often does nothing harmful, because it only acts on closed daily candles.

### Run it for free every day without your laptop (GitHub Actions)
1. Create a new **private** GitHub repo called `crypto-bot` and push this folder to it.
2. Optional Telegram alerts: message @BotFather, send `/newbot`, and copy the token. Send your bot any message, then open `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy `chat.id`. In the repo go to Settings → Secrets → Actions and add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.
3. Actions tab → daily-paper-trade → Run workflow, to test it once. After that it runs every day at 00:10 UTC and commits its ledger back to the repo.

### Weekly review (10 minutes, Sunday)
- Read `MEMORY.md` and `logs/daily.csv`.
- Compare against the backtest. Is it roughly matching, given the market that week?
- Write one dated line in `learnings.md`. **Do not change the rules mid-test.** Test new ideas in `backtest.py` first (articles #20, #44: one change at a time).
- To pause everything, create an empty file named `STOP` in the folder. To resume after a drawdown halt, review what happened, then set `peak_equity` in `data/state.json` to the current equity.

### Going live (not yet)
Live trading is switched off in code (`LiveBroker`). It stays off until all of these are true:
1. **The account holder is 18+ and it is your own account.** Exchanges require this, and using someone else's account breaks their terms.
2. **You use an exchange that serves US residents**, such as Coinbase Advanced or Kraken. Binance.com and Bybit do not accept US residents.
3. **You have 90+ days of paper results** in `logs/daily.csv` that look like the backtest (article #20 step 7, article #22 phase 3).
4. **The API key is set to trade only, with withdrawals disabled**, IP-restricted, and never pasted into a chat (article #33).
5. **You start with money you can lose**, and remember every sell is a taxable event in the US.
6. You have checked with your DSO that personal investing fits your visa status.

When those are done, the live broker is a small class that sends the same orders to the exchange's API. Write it and read every line yourself.

### Money reality
At 34% a year, $20 becomes about $27. The bot will not produce income from $20. What it produces now is a verified track record, a portfolio project (CS + Finance), and build-in-public content for X (article #19). Profit only scales with capital you add later.
