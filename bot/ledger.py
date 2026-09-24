"""Trade ledger and memory files (article #36 pattern).

logs/ledger.csv   one row per fill
logs/daily.csv    one row per run: equity, exposure, signals
MEMORY.md         human-readable latest state, rewritten every run
learnings.md      append-only notes (you or an AI reviewer add lessons here)
"""
import csv
import os
from datetime import datetime, timezone


def _append_csv(path, row):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    new = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(row.keys()))
        if new:
            w.writeheader()
        w.writerow(row)


def log_fill(fill, reason):
    _append_csv("logs/ledger.csv", {
        "time_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "asset": fill["asset"], "side": fill["side"], "usd": fill["usd"],
        "fill_price": fill["fill"], "qty": round(fill["qty"], 8), "reason": reason})


def log_run(eq, state, targets, notes, blocked, source):
    row = {"time_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "equity": round(eq, 2), "cash": round(state["cash"], 2), "data": source,
           "blocked": "; ".join(blocked)}
    for a, w in targets.items():
        row[f"{a}_target"] = w
        row[f"{a}_close"] = notes[a].get("close")
        row[f"{a}_sma"] = notes[a].get("sma")
    _append_csv("logs/daily.csv", row)


def write_memory(eq, state, targets, notes, blocked, dd, fills):
    start = state["start_cash"]
    lines = [
        "# Bot memory (rewritten every run)", "",
        f"Last run (UTC): {datetime.now(timezone.utc):%Y-%m-%d %H:%M}",
        f"Mode: paper | Equity: ${eq:,.2f} | Start: ${start:,.2f} | P&L: {eq / start - 1:+.1%} | Drawdown from peak: {dd:.1%}",
        f"Runs so far: {state['runs']}", "", "## Signals",
    ]
    for a, info in notes.items():
        lines.append(f"- {a}: {info.get('reason')} (close {info.get('close')}, SMA {info.get('sma')}, vol {info.get('vol', '-')}) -> target {targets[a]:.0%} of equity")
    lines += ["", "## This run"]
    lines += [f"- {f['side']} {f['asset']} ${f['usd']} at {f['fill']}" for f in fills] or ["- no trades"]
    if blocked:
        lines += ["", "## BLOCKED by risk manager"] + [f"- {b}" for b in blocked]
    with open("MEMORY.md", "w") as f:
        f.write("\n".join(lines) + "\n")
    if not os.path.exists("learnings.md"):
        with open("learnings.md", "w") as f:
            f.write("# Learnings (append-only)\n\nAdd one dated line per lesson. Never change the strategy mid-test; write the idea here and test it in backtest.py first.\n")
