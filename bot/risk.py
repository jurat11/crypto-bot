"""Risk manager: plain code, runs before every order (article #22 pattern).

Hard rules live here, not in a prompt. Any rule that fails blocks trading for
this run and the reason is written to the ledger.
"""
import os
import time

KILL_SWITCH = "STOP"  # create an empty file named STOP in the project folder to halt everything


def check(cfg, state, equity, data_age_hours, orders, loss_label="since last run"):
    r = cfg["risk"]
    problems = []
    if os.path.exists(KILL_SWITCH):
        problems.append("kill switch file STOP exists")
    if data_age_hours > r["max_data_age_hours"]:
        if data_age_hours == float("inf"):
            problems.append("no price data yet")
        else:
            age = f"{data_age_hours:.0f}h" if data_age_hours >= 1 else f"{data_age_hours * 60:.0f} min"
            problems.append(f"price data is {age} old")
    peak = max(state.get("peak_equity", equity), equity)
    dd = 1 - equity / peak if peak else 0
    if dd >= r["max_drawdown_halt_pct"]:
        problems.append(f"drawdown {dd:.0%} hit the halt level {r['max_drawdown_halt_pct']:.0%}; review before resuming")
    last = state.get("last_equity")
    if last and 1 - equity / last >= r["max_daily_loss_pct"]:
        problems.append(f"lost {1 - equity / last:.0%} {loss_label} (limit {r['max_daily_loss_pct']:.0%})")
    if len(orders) > r["max_trades_per_run"]:
        problems.append(f"{len(orders)} orders exceeds max {r['max_trades_per_run']}")
    return problems, dd


def clamp_targets(targets, cfg):
    cap = cfg["risk"]["max_weight_per_asset"]
    return {a: min(w, cap) for a, w in targets.items()}


def now_ms():
    return int(time.time() * 1000)
