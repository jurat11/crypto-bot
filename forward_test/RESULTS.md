# $15 demo results

Every bot starts with a demo balance of $15 and trades real Binance BTC/ETH prices. Fees are 0.4% per trade (0.1% shown where noted) plus 0.05% slippage. No real money is used.

## Part 1: last 12 months of the real market (Sep 24, 2025 to Sep 24, 2026)
Each article's recipe was set up **only with data from before Sep 24, 2025**. Then it ran through the following 12 months, which it had never seen. That is the "forward test" step from article #20, done on real past prices.

The market was down over this year. BTC fell from 112,231 to 83,951 (-25%) and ETH from 4,174 to 2,665 (-36%).

| Bot | $15 became (0.4% fee) | (0.1% fee) | Trades | Notes |
|---|---|---|---|---|
| **C: SMA50 trend + vol dial (this repo)** | **$17.08** | $17.98 | 75 switches | Max drawdown 20%. Sat in cash through most of the decline |
| A: Article #20 "best hour" bot | $13.53 | $14.99 | 17 | Picked 06:00 and 20:00 UTC from 1,522 trades in 2018-25 |
| A without the hour filter (plain EMA 9/21) | $2.33 | $7.34 | 191 | Fees destroyed it |
| B: Article #36 "strategy from the internet" (RSI 30/70, 5% stop) | $7.91 | $11.21 | 57 | Won 56-61% of trades and still lost money |
| Hold 50% BTC / 50% ETH | $10.32 | | 0 | |
| Hold BTC only | $11.20 | | 0 | |

**What happened to article #20's recipe, step by step:**
- **Step 3:** EMA 9/21 on BTC 1h, fees on, 2018-2025. $15 became $1.58 over 1,522 trades.
- **Step 4:** Keep only entries at the two best hours, 06 and 20 UTC. That version turned $15 into $25.36 on the same data. This looks great, but the hours were picked on that very data.
- **Step 5:** Check the same hours on other coins. They were not special. On ETH they ranked 9th and 21st of 24 hours, and on SOL 10th and 15th. On SOL the filter did worse ($14.19) than no filter ($64.30). The article's own rule says a pattern that only works on the coin you tuned is a fluke. **The recipe failed its own test.**
- **Step 7:** Forward test over the next 12 months. Hour 06 lost 2.2% and hour 20 made 2.4%. Hour 00, which had been the 3rd worst hour in 2018-25, became the 2nd best. $15 became $13.53.

## Part 2: live demo from today (rules frozen)
Start: **Sep 24, 2026, 16:00 UTC**. BTC 83,951, ETH 2,665. The code is in `forward_test.js` and must not be edited.

Positions at start:
- **Bot C:** $6.75 in BTC and $6.75 in ETH (90% of each sleeve, since both are above their 50-day average), plus $1.50 in cash.
- **Bot A:** in cash. It waits for an EMA 9/21 cross that happens at 06:00 or 20:00 UTC. Right now EMA9 is 83,847, below EMA21 at 84,114.
- **Bot B:** in cash. It waits for RSI to drop below 30. RSI is currently 46.
- **Hold:** $7.50 in BTC and $7.50 in ETH.

| Check-in | Date (UTC) | Bot A | Bot B | Bot C | Hold 50/50 |
|---|---|---|---|---|---|
| Start | 2026-09-24 | $15.00 | $15.00 | $15.00 | $15.00 |
| 7 days | 2026-10-01 | | | | |
| 30 days | 2026-10-24 | | | | |
| 90 days | 2026-12-23 | | | | |

To check it yourself at any time: open https://data-api.binance.vision/api/v3/ping in Chrome, open the console (Cmd+Option+J), paste `forward_test.js`, then run `await loadAll(); FWD(START)`.
