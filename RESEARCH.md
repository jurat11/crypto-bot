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
