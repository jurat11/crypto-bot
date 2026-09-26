# Research log (Sep 24, 2026)

Data: Binance hourly and daily candles, Jan 2018 to Sep 23, 2026, for BTC, ETH and SOL. Rules were chosen on **2018-2022 (in-sample)** and then checked on **2023 to today (out-of-sample)**. Fees are 0.1% per trade unless noted. CAGR is compound annual growth. Max DD is the worst peak-to-trough drop.

## 1. Article #20's hack: "find the hour that pays"
The 24 UTC hours of BTC were ranked by average hourly return.

- The correlation between the 2018-22 hour ranking and the 2023+ ranking was **0.01**, so the pattern did not persist.
- The best in-sample hour (13:00 UTC, +3.8 bp) turned **negative** out-of-sample (-2.3 bp).
- Only 21:00 and 22:00 UTC were positive in both periods, at +2.6 to +4.5 bp per hour.
- A round trip costs 20 to 120 bp in fees. Trading one hour a day loses money to fees even when the hour is real.

The execution hour does not matter for the daily trend rule either. Moving the daily decision to 00, 04, 08, 12, 16, 20 or 22 UTC gave BTC out-of-sample CAGRs between 48% and 59%. That is noise, not an edge.

**Verdict:** the hour-of-day hack is overfitting. It is not used in this bot.

## 2. Candidate strategies (daily, long or cash)

| | BTC IS CAGR | BTC OOS CAGR | BTC OOS max DD | ETH IS CAGR | ETH OOS CAGR | ETH OOS max DD |
|---|---|---|---|---|---|---|
| Buy and hold | 4.3% | 54.4% | 53% | 9.6% | 23.9% | 68% |
| **Close > SMA50** | **38.4%** | **56.0%** | **27%** | **49.8%** | **52.8%** | **38%** |
| Close > SMA100 | 44.9% | 40.0% | 36% | 31.4% | 32.2% | 46% |
| EMA20 > EMA50 | 39.3% | 34.1% | 41% | 45.9% | 32.9% | 46% |
| Donchian 20/10 breakout | 43.3% | 26.1% | 38% | 60.1% | **-7.2%** | 62% |
| RSI(2) dip-buy above SMA200 | 1.2% | 5.8% | 24% | 6.6% | 13.4% | 22% |

The Donchian breakout had the best in-sample number on ETH and then lost money out of sample. That is exactly the trap articles #20 and #22 warn about. The SMA50 filter was the only candidate that stayed strong in both periods on both coins.

**Robustness check, BTC out-of-sample CAGR by SMA length:** 20: 28%, 30: 30%, 40: 57%, 50: 56%, 60: 51%, 80: 41%, 100: 40%, 150: 43%, 200: 40%. Every length made money. The result does not depend on one magic number.

**On SOL** (since 2021): SMA50 returned 90% CAGR with a 69% max DD, against 47% CAGR and a 96% max DD for holding.

## 3. Final portfolio: 50% BTC and 50% ETH sleeves, SMA50 filter

| Version (Jul 2018 to Sep 2026) | Growth | CAGR | Max DD |
|---|---|---|---|
| Fee 0.1% | x34.7 | 54% | 56% |
| Fee 0.4% (realistic small-account fee on a US exchange) | x21.4 | 45% | 60% |
| Fee 0.6% | x15.5 | 40% | 62% |
| Fee 0.4% + volatility dial 40% (**bot default**) | | **34%** | **44%** |
| Buy and hold 50/50 | x8.7 | 30% | 77% |

Out-of-sample only, with fee 0.4% and the vol dial: 37% CAGR and a 26% max DD.

**Year by year** (fee 0.4%, no vol dial), strategy vs hold:

| Year | Strategy | Hold |
|---|---|---|
| 2018 (from Jul) | -23% | -60% |
| 2019 | +31% | +59% |
| 2020 | +283% | +340% |
| 2021 | +143% | +160% |
| 2022 | -44% | -66% |
| 2023 | +85% | +120% |
| 2024 | +45% | +86% |
| 2025 | +26% | -8% |
| 2026 YTD | +21% | -6% |

## 4. What this does and does not show
- In strong bull years the strategy trails holding. It wins by losing less in crashes (2018, 2022). The edge is survival, not magic.
- Only about 23% of trades win. A few big trends pay for many small losses. Most weeks it will look like it is doing nothing or losing small amounts.
- 2020-21 dominates the totals, and a market like that may not come back.
- BTC and ETH were chosen partly because they survived. Coins that died are not in the test (survivorship bias).
- **Past results do not predict future results.** Three months of paper trading is the real test.

## 5. Signal on the last closed day (Sep 23, 2026)
- BTC: close 84,398, above its 50-day SMA of 74,558. 30-day vol 43%, so exposure is 90% of the BTC sleeve.
- ETH: close 2,685, above its 50-day SMA of 2,326. 30-day vol 47%, so exposure is 90% of the ETH sleeve.

## 6. Four demo strategies, backtested before demo (run Sep 24, 2026)
`python3 backtest_strategies.py` on Binance 1h candles (4h and 1d built from them). The rules were fixed in SPEC.md before this run, and nothing was tuned. The code is the same `decide()` code the live demo engine runs. Each strategy uses 50% BTC / 50% ETH sleeves, marked every hour. The trade count includes every buy and sell for both coins. In-sample is 2018-01-01 to 2022-12-31; out-of-sample (OOS) is 2023-01-01 to 2026-09-24.

| Strategy | Fee | IS CAGR | IS max DD | OOS CAGR | OOS return | OOS max DD | OOS trades | OOS win rate | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| TREND_D1 | 0.1% | 32.6% | 42% | 45.5% | 305.5% | 25% | 353 | 28% | PASSED |
| TREND_D1 | 0.4% | 27.5% | 45% | 37.3% | 226.3% | 27% | 353 | 25% | |
| TREND_H4 | 0.1% | 35.1% | 36% | 44.6% | 296.1% | 25% | 526 | 15% | PASSED |
| TREND_H4 | 0.4% | 24.8% | 41% | 29.3% | 161.2% | 31% | 526 | 12% | |
| BREAKOUT_H1 | 0.1% | 24.9% | 30% | 13.2% | 59.1% | 23% | 652 | 35% | FAILED BACKTEST |
| BREAKOUT_H1 | 0.4% | -3.3% | 42% | -12.8% | -40.1% | 53% | 652 | 32% | |
| MEANREV_H1 | 0.1% | -3.6% | 32% | -14.0% | -43.1% | 46% | 1834 | 57% | FAILED BACKTEST |
| MEANREV_H1 | 0.4% | -47.2% | 96% | -58.9% | -96.4% | 96% | 1834 | 18% | |
| Hold 50/50 BTC/ETH | none | 7.2% | 89% | 41.7% | 267.8% | 58% | 0 | n/a | benchmark |

What it shows:
- **TREND_D1 matches section 3.** At a 0.4% fee it made 37.3% a year OOS with a 27% max DD, against 37% and 26% before. That is an independent check of the new engine code.
- **Both trend strategies roughly match holding with half the drawdown.** OOS they made 45% a year with a 25% max DD, against 42% and 58% for hold. TREND_H4 trades 50% more, so the 0.4% fee costs it much more (29% vs 37% a year).
- **BREAKOUT_H1 depends on fees.** It made money at 0.1% (13% a year, far behind holding) and lost at 0.4%. It fails the "loses at either fee" rule.
- **MEANREV_H1 is the section 2 trap again.** It won 57% of trades and still lost 43% OOS at the cheapest fee. The losing trades were larger than the winners, and 1,834 trades paid a fee every time.
- All four still run in demo. The two that failed are flagged FAILED BACKTEST on the dashboard. **Past results do not predict future results.**

## 7. Scalping accounts: rules fixed before the backtest (Sep 25, 2026)
Asked for: fast trades that go long or short, close after a small move, and repeat. Two $20 demo accounts, SCALP_BTC_ETH and SCALP_MEME (DOGE, PEPE), with simulated 1x shorts. The rules below were written down before any backtest ran and will not be tuned:
- **Decide on every 1-minute close.** When flat in a coin: long if the last 15 minutes rose at least 0.1% and the close is above its 60-minute average; short on the mirror image.
- **Exit:** +0.3% take profit, -0.3% stop, or 30 minutes, whichever comes first.
- **Test window:** the last 90 days of 1-minute candles. The first 45 days are in-sample, the last 45 out-of-sample, at 0.1% and 0.4% fees. The same verdict rule as section 6 applies.

The expected problem, stated before the result: each round trip pays the fee twice. At 0.1% a side, a +0.3% win keeps +0.1% and a -0.3% loss costs -0.5%, so the rule needs to win about 83% of its trades to break even. Nothing in section 1 or 2 suggests a 15-minute move predicts the next few minutes that well. The report also prints the no-fee result for these two, which shows how much of any loss is fees.

The result goes here once the backtest runs on real Binance data (it needs network access to data-api.binance.vision).

**Added Sep 26, 2026, before any result for it: the +$1 variant.** After a day of watching, the idea was to close each trade as soon as it is up $1. On a $10 trade that is a 10% move, so SCALP_1USD (BTC, ETH) and SCALP_1USD_MEME (DOGE, PEPE) keep the same entries but close only at +$1 or -$1 (+-10%), with no time limit. The -$1 stop is part of the rule so one losing trade cannot be held forever. Expected, stated before the test: many fewer trades and far less fee drag, but no edge, because the entry signal looks 15 minutes back and these trades last days. The original SCALP_BTC_ETH and SCALP_MEME are unchanged: changing them after one day of live results would be tuning.

**Result (run on the user's Mac, Sep 26, 2026; 1-minute candles, in-sample Jun 28 to Aug 12, out-of-sample Aug 12 to Sep 26, 2026):**

| Account | OOS before fees | OOS after 0.1% fees | OOS trades | OOS win rate | Verdict |
|---|---|---|---|---|---|
| SCALP_BTC_ETH | -11.2% | -99.2% | 9,694 | 29% | FAILED BACKTEST |
| SCALP_MEME | -56.8% | -100.0% | 22,928 | 35% | FAILED BACKTEST |
| SCALP_1USD | +35.4% | +34.7% | 10 | 100% | PASSED |
| SCALP_1USD_MEME | -15.1% | -17.3% | 46 | 43% | FAILED BACKTEST |

What it shows:
- **Fast scalping lost even before fees.** Going with a 15-minute move for a +-0.3% target lost 11% (BTC/ETH) and 57% (DOGE/PEPE) with no fees at all. Over minutes, prices tended to snap back rather than keep going. The fees then did the rest: about 215 trades a day on BTC/ETH cost roughly 10% of the account a day.
- **The +-$1 rule on BTC/ETH passed, but on very little evidence.** 10 trades is about 5 closed round trips, and all of them won. Five coin flips all landing heads happens about 1 time in 32. The same rule on DOGE/PEPE lost 17% over 46 trades, and it lost in-sample too. Both halves of the BTC/ETH test were positive (about +13% in-sample), which may say more about how BTC and ETH trended in those 90 days than about the rule.
- **Fees are not the problem for the +-$1 rule** (35.4% before, 34.7% after). Its risk is having too few trades to judge.
- In demo, the two fast scalpers will hit the 8% daily-loss block most days and the 35% drawdown halt within about a week. That is the risk rules working.
- The strategies with real evidence are still the slow trend ones in section 6. **Past results do not predict future results.**
