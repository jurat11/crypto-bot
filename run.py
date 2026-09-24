"""Run once a day, right after 00:00 UTC (8 pm New York time in summer).

  python3 run.py            paper-trade one step and update logs
  python3 run.py --dry      show what it would do, change nothing
"""
import json
import sys
import time

from bot import broker, data, ledger, notify, risk, strategy


def main(dry=False):
    cfg = json.load(open("config.json"))
    if cfg["mode"] != "paper":
        broker.LiveBroker()  # exits with an explanation

    closes, prices, source, newest = {}, {}, None, None
    for asset in cfg["assets"]:
        rows, source = data.daily_candles(asset, days=cfg["sma_days"] + cfg["vol_lookback_days"] + 30)
        rows = data.drop_open_candle(rows)
        closes[asset] = [r[4] for r in rows]
        prices[asset] = rows[-1][4]
        newest = rows[-1][0] + data.DAY_MS  # close time of last closed candle
    age_h = (time.time() * 1000 - newest) / 3_600_000

    state = broker.load_state(cfg["paper_start_cash"])
    targets, notes = strategy.portfolio_targets(closes, cfg["assets"], cfg)
    targets = risk.clamp_targets(targets, cfg)
    eq_before = broker.equity(state, prices)
    orders = broker.plan_orders(state, prices, targets, cfg["min_trade_usd"])
    blocked, dd = risk.check(cfg, state, eq_before, age_h, orders)

    print(f"data={source} age={age_h:.1f}h equity=${eq_before:,.2f} targets={targets}")
    for a, n in notes.items():
        print(f"  {a}: {n.get('reason')}")
    if blocked:
        print("BLOCKED:", "; ".join(blocked))
    if dry:
        print("orders (dry run):", orders)
        return

    fills = []
    # A block stops new risk (buys). Sells that reduce risk still go through,
    # except when the kill switch is on, which freezes everything.
    kill = any("kill switch" in b for b in blocked)
    allowed = [] if kill else [o for o in orders if not blocked or o["side"] == "sell"]
    if allowed:
        pb = broker.PaperBroker(cfg["fee_rate"], cfg["slippage"])
        for o in allowed:
            f = pb.execute(state, o, prices[o["asset"]])
            fills.append(f)
            ledger.log_fill(f, notes[o["asset"]].get("reason", ""))

    eq = broker.equity(state, prices)
    state["runs"] += 1
    state["peak_equity"] = max(state.get("peak_equity", eq), eq)
    state["last_equity"] = eq
    broker.save_state(state)
    ledger.log_run(eq, state, targets, notes, blocked, source)
    ledger.write_memory(eq, state, targets, notes, blocked, dd, fills)

    msg = [f"Crypto bot (paper) equity ${eq:,.2f} ({eq / state['start_cash'] - 1:+.1%})"]
    msg += [f"{a}: {notes[a].get('reason')}" for a in notes]
    msg += [f"{f['side']} {f['asset']} ${f['usd']}" for f in fills] or ["no trades"]
    if blocked:
        msg.append("BLOCKED: " + "; ".join(blocked))
    notify.send("\n".join(msg))
    print("\n".join(msg))


if __name__ == "__main__":
    main(dry="--dry" in sys.argv)
