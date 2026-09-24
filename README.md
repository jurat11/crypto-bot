# crypto-bot: BTC/ETH trend filter (paper trading)

A daily bot that holds BTC and ETH while each one is above its 50-day average and moves to cash when it drops below. Position size shrinks automatically when the market gets wild. It uses only the Python standard library, needs no API keys, and costs $0 to run.

**Status: paper mode.** It trades a simulated $20 against real prices, with realistic fees (0.4%) and slippage. Nothing touches real money. Read "Going live" before changing that.

## Why this strategy (short version of RESEARCH.md)
- I tested article #20's "find the best hour" hack on 8 years of BTC data. The best hours in 2018-22 did not stay the best in 2023-26 (correlation 0.01), and fees eat any single-hour edge. It was dropped.
- Of six candidate strategies chosen on 2018-22 data, only the SMA50 trend filter stayed strong on the unseen 2023-26 data for both BTC and ETH. It also held up across SMA lengths from 40 to 150.
- The default settings (0.4% fees, volatility dial) returned 34% a year over 2018-2026 with a 44% worst drawdown. Buy and hold returned 30% a year with a 77% drawdown. Out-of-sample alone: 37% a year with a 26% drawdown.
- It trails buy-and-hold in strong bull years. Only about 1 in 4 trades wins. The edge is losing less in crashes. **Past results do not predict the future.**

## How it works
| Piece | File | Idea from the articles |
|---|---|---|
| Price data (Binance public, Coinbase fallback) | `bot/data.py` | #20, #36 |
| Signal: SMA50 filter + volatility dial | `bot/strategy.py` | #20 method, checked out of sample |
| Risk manager in plain code: kill switch, stale-data guard, daily loss limit, drawdown halt, per-asset cap. A block stops buys, but sells that cut risk still go through | `bot/risk.py` | #22, #6 (gate on blast radius) |
| Paper broker with fees and slippage | `bot/broker.py` | #22 phase 3 |
| Ledger (`logs/ledger.csv`, `logs/daily.csv`), `MEMORY.md`, `learnings.md` | `bot/ledger.py` | #36 trade ledger |
| Telegram alerts (optional) | `bot/notify.py` | #36, #32 |
| Free daily hosting on GitHub Actions | `.github/workflows/daily.yml` | |

## Run it on your Mac (Terminal, from this folder)
```bash
python3 -m unittest discover tests    # 10 offline tests
python3 run.py --dry                  # see today's signal, change nothing
python3 run.py                        # one paper-trading step
python3 backtest.py                   # re-run the research on fresh data
python3 hour_test.py BTC              # the article #20 hour test
```
Run `python3 run.py` once a day, any time after 00:10 UTC (8:10 pm New York time). Running it more often does nothing harmful, because it only acts on closed daily candles.

## Run it for free every day without your laptop (GitHub Actions)
1. Create a new **private** GitHub repo called `crypto-bot` and push this folder to it.
2. Optional Telegram alerts: message @BotFather, send `/newbot`, and copy the token. Send your bot any message, then open `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy `chat.id`. In the repo go to Settings → Secrets → Actions and add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.
3. Actions tab → daily-paper-trade → Run workflow, to test it once. After that it runs every day at 00:10 UTC and commits its ledger back to the repo.

## Weekly review (10 minutes, Sunday)
- Read `MEMORY.md` and `logs/daily.csv`.
- Compare against the backtest. Is it roughly matching, given the market that week?
- Write one dated line in `learnings.md`. **Do not change the rules mid-test.** Test new ideas in `backtest.py` first (articles #20, #44: one change at a time).
- To pause everything, create an empty file named `STOP` in the folder. To resume after a drawdown halt, review what happened, then set `peak_equity` in `data/state.json` to the current equity.

## Going live (not yet)
Live trading is switched off in code (`LiveBroker`). It stays off until all of these are true:
1. **The account holder is 18+ and it is your own account.** Exchanges require this, and using someone else's account breaks their terms.
2. **You use an exchange that serves US residents**, such as Coinbase Advanced or Kraken. Binance.com and Bybit do not accept US residents.
3. **You have 90+ days of paper results** in `logs/daily.csv` that look like the backtest (article #20 step 7, article #22 phase 3).
4. **The API key is set to trade only, with withdrawals disabled**, IP-restricted, and never pasted into a chat (article #33).
5. **You start with money you can lose**, and remember every sell is a taxable event in the US.
6. You have checked with your DSO that personal investing fits your visa status.

When those are done, the live broker is a small class that sends the same orders to the exchange's API. Write it and read every line yourself.

## Money reality
At 34% a year, $20 becomes about $27. The bot will not produce income from $20. What it produces now is a verified track record, a portfolio project (CS + Finance), and build-in-public content for X (article #19). Profit only scales with capital you add later.
