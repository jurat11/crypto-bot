"""Demo accounts: $15 of simulated USDT each, filled against the live order book.

State is a plain dict so it can be saved to SQLite as JSON and survive restarts.
Numbers are realized and marked-to-market after fees: holdings are valued at
the live mid price, and every fee already paid is out of the balance.
"""
import time

ASSETS = ("BTC", "ETH")  # the original sleeves; an account can hold any coins


def utc_day(ms):
    return time.strftime("%Y-%m-%d", time.gmtime(ms / 1000))


def new_state(name, start_cash, now_ms, assets=ASSETS):
    return {
        "name": name, "start_cash": start_cash, "started_ms": now_ms,
        "cash": start_cash, "holdings": {a: 0.0 for a in assets}, "cost": {a: 0.0 for a in assets},
        "fees": 0.0, "trades": 0, "sells": 0, "wins": 0,
        "peak_equity": start_cash, "max_dd": 0.0, "last_equity": start_cash,
        "day": utc_day(now_ms), "day_start_equity": start_cash,
        "pos": {a: {} for a in assets}, "info": {a: {} for a in assets},
        "decided_ms": 0, "funded": False,
    }


class Account:
    def __init__(self, state):
        self.s = state

    @classmethod
    def create(cls, name, start_cash, now_ms, assets=ASSETS):
        return cls(new_state(name, start_cash, now_ms, assets))

    def ensure_assets(self, assets):
        """Accounts saved before a coin was added get empty entries for it."""
        for a in assets:
            self.s["holdings"].setdefault(a, 0.0)
            self.s["cost"].setdefault(a, 0.0)
            self.s["pos"].setdefault(a, {})
            self.s["info"].setdefault(a, {})

    @property
    def assets(self):
        return list(self.s["holdings"])

    def coins_in_use(self, sleeves):
        return set(sleeves) | {a for a, q in self.s["holdings"].items() if q > 0}

    @property
    def name(self):
        return self.s["name"]

    def value(self, asset, prices):
        return self.s["holdings"].get(asset, 0.0) * (prices.get(asset) or 0.0)

    def equity(self, prices):
        return self.s["cash"] + sum(self.value(a, prices) for a in self.s["holdings"])

    def weights(self, prices):
        eq = self.equity(prices)
        return {a: self.value(a, prices) / eq if eq else 0.0 for a in self.s["holdings"]}

    def apply_buy(self, asset, sim):
        """sim from orderbook.simulate_market_order (BUY)."""
        s = self.s
        s["cash"] -= sim["notional"]
        s["holdings"][asset] += sim["qty_net"]
        s["cost"][asset] += sim["notional"]
        s["fees"] += sim["fee_usd"]
        s["trades"] += 1

    def apply_sell(self, asset, sim):
        """Returns realized P&L of the sold part vs its average cost (fees included)."""
        s = self.s
        held = s["holdings"][asset]
        part = min(1.0, sim["qty"] / held) if held else 1.0
        cost = s["cost"][asset] * part
        s["holdings"][asset] = max(0.0, held - sim["qty"])
        s["cost"][asset] -= cost
        if s["holdings"][asset] <= 1e-12:
            s["holdings"][asset], s["cost"][asset] = 0.0, 0.0
        s["cash"] += sim["proceeds_net"]
        s["fees"] += sim["fee_usd"]
        s["trades"] += 1
        s["sells"] += 1
        pnl = sim["proceeds_net"] - cost
        if pnl > 0:
            s["wins"] += 1
        return pnl

    def mark(self, equity, now_ms):
        """Once a minute: update peak, max drawdown and the UTC-day starting equity."""
        s = self.s
        day = utc_day(now_ms)
        if day != s["day"]:
            s["day"], s["day_start_equity"] = day, equity
        s["peak_equity"] = max(s["peak_equity"], equity)
        s["max_dd"] = max(s["max_dd"], 1 - equity / s["peak_equity"] if s["peak_equity"] else 0.0)
        s["last_equity"] = equity

    def summary(self, prices):
        s = self.s
        eq = self.equity(prices)
        peak = max(s["peak_equity"], eq)
        return {
            "account": s["name"], "balance": eq, "cash": s["cash"],
            "holdings_usd": {a: self.value(a, prices) for a in self.s["holdings"]},
            "pnl_usd": eq - s["start_cash"], "pnl_pct": eq / s["start_cash"] - 1,
            "max_dd": max(s["max_dd"], 1 - eq / peak if peak else 0.0),
            "trades": s["trades"], "win_rate": s["wins"] / s["sells"] if s["sells"] else None,
            "fees": s["fees"], "started_ms": s["started_ms"],
        }
