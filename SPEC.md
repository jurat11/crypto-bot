# Build spec: multi-strategy engine + live dashboard

Status: approved to build everything except real-money trading. The user has no real Binance keys; LIVE stays disabled. Testnet keys (free, fake funds) will be added later as TESTNET_API_KEY / TESTNET_API_SECRET; until then build and unit-test the testnet code offline.

## Goal
A trading engine with a live web dashboard. Several strategies run side by side, each with its own $15 demo account on REAL live Binance prices, and I can watch the balance and every decision update every second. It also has a testnet mode that sends real orders to Binance's official Spot Testnet, so I can see real order placement and fills.

## Accounts and modes
- DEMO (always on): each strategy gets $15, starting at first launch, persisted across restarts. Fills are simulated against the LIVE order book, not the last price: pull depth from https://data-api.binance.vision/api/v3/depth, walk the book for the order size, then apply the 0.1% taker fee. This makes demo results as close to real as possible.
- TESTNET (optional, TESTNET=1 in .env): the selected strategy also sends MARKET orders to https://testnet.binance.vision/api using signed HMAC-SHA256 requests to /api/v3/order.
  - Respect LOT_SIZE, MIN_NOTIONAL and stepSize from /api/v3/exchangeInfo.
  - Use newClientOrderId so an order is never sent twice.
  - Only /api endpoints exist on testnet.
  - Keys go in .env as TESTNET_API_KEY / TESTNET_API_SECRET, taken from testnet.binance.vision.
  - Label testnet prices "TESTNET" on the dashboard, because they can differ from the real market.
- LIVE: keep bot/broker.py LiveBroker raising its current error. Do not read real exchange keys or call api.binance.com trading endpoints.

## Strategies (all spot, long or cash, BTC and ETH)
1. TREND_D1: the existing bot/strategy.py (SMA50 daily + 40% vol dial). This is the validated baseline. Do not change it.
2. TREND_H4: the same idea on 4-hour candles (close > SMA of 6 x 4h bars x 50/6 days, i.e. roughly SMA300 on 4h), decided at each 4h close.
3. BREAKOUT_H1: buy when the 1h close breaks the prior 48h high while price is above the daily SMA50. Exit on a 24h low or a 3% trailing stop.
4. MEANREV_H1: buy when RSI(2) on 1h < 5 and price is above the daily SMA50. Sell when RSI(2) > 70 or after 12 hours, with a 3% stop.

Before any strategy goes live in demo, run it through backtest.py-style checks: choose on 2018-22, report 2023+ out of sample, with 0.1% and 0.4% fees. Show me the table. Strategies that lose out of sample still run in demo, but flag them "FAILED BACKTEST" on the dashboard. Do not tune parameters to make them pass.

## Engine
- Market data websocket wss://data-stream.binance.vision/ws (btcusdt@kline_1m, ethusdt@kline_1m, @bookTicker), with REST polling fallback every 5 s and auto-reconnect.
- Each strategy decides only on closed candles of its own timeframe.
- Every minute: mark every account to market and run bot/risk.py per account (kill switch, stale data, daily loss limit, drawdown halt, per-asset cap). A risk block stops buys, but risk-reducing sells still go through.
- Every decision writes a receipt to SQLite (data/bot.db): inputs, signal, risk result, order, fill price, fee, slippage vs mid.
- No leverage, margin, futures, martingale, averaging down or grid logic, ever.

## Dashboard (FastAPI + one HTML page, Chart.js from a CDN, Server-Sent Events every second)
- Top: leaderboard of the 4 demo accounts plus "hold 50/50 BTC/ETH", showing balance, P&L $ and %, max drawdown, trade count, win rate and fees paid, sorted by P&L.
- Equity curves for all accounts on one chart since start.
- Live BTC and ETH price, spread, and each strategy's current state in plain English, e.g. "BREAKOUT_H1: waiting, BTC is 1.8% below the 48h high."
- Live activity feed of every check, signal, order and fill with timestamps.
- Order/fill table with a "real vs simulated slippage" column for testnet fills.
- Risk panel per account (green/red with reasons) and a STOP button with confirm (creates the STOP file).
- A banner that always shows "DEMO: no real money" or "TESTNET: fake funds, real orders."
- Only realized and marked-to-market numbers after fees. Never show projections.

## Alerts
Optional Telegram (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID): every trade, every risk block, and a daily 00:15 UTC leaderboard.

## Run
python3 -m bot.server starts everything, dashboard at http://localhost:8000. Add .env.example, and never print or log secrets. Don't touch forward_test/.

## Verification after every step
python3 -m unittest discover tests must pass. Add tests for:
- order-book fill simulation
- testnet signing with a fixed timestamp
- exchange-filter rounding
- each strategy's signal on hand-made candles
- the SSE payload
If the same test fails twice, stop, show the error and your proposed fix, and wait.

## Done means
1. The dashboard shows all 4 demo accounts at $15, updating live for 10 minutes.
2. With TESTNET=1, one ~$15 testnet BTC buy and sell go through and appear in the table.
3. STOP halts new buys within a minute.
4. The README explains each panel and how to get testnet keys.
