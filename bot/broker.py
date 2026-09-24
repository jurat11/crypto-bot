"""Brokers. PaperBroker simulates fills with fees and slippage and stores
everything in state.json. LiveBroker is intentionally not wired up (see README,
"Going live")."""
import json
import os

STATE_FILE = "data/state.json"


def load_state(start_cash):
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"cash": start_cash, "holdings": {}, "peak_equity": start_cash,
            "last_equity": start_cash, "start_cash": start_cash, "runs": 0}


def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def equity(state, prices):
    return state["cash"] + sum(q * prices[a] for a, q in state["holdings"].items())


def plan_orders(state, prices, targets, min_trade_usd):
    eq = equity(state, prices)
    orders = []
    for asset, w in targets.items():
        cur_val = state["holdings"].get(asset, 0.0) * prices[asset]
        diff = w * eq - cur_val
        if abs(diff) >= min_trade_usd:
            orders.append({"asset": asset, "side": "buy" if diff > 0 else "sell", "usd": round(abs(diff), 2)})
    orders.sort(key=lambda o: o["side"] != "sell")  # sells first to free cash
    return orders


class PaperBroker:
    def __init__(self, fee_rate, slippage):
        self.fee, self.slip = fee_rate, slippage

    def execute(self, state, order, price):
        a = order["asset"]
        if order["side"] == "buy":
            usd = min(order["usd"], state["cash"])
            fill = price * (1 + self.slip)
            qty = usd * (1 - self.fee) / fill
            state["cash"] -= usd
            state["holdings"][a] = state["holdings"].get(a, 0.0) + qty
        else:
            fill = price * (1 - self.slip)
            qty = min(order["usd"] / fill, state["holdings"].get(a, 0.0))
            state["holdings"][a] = state["holdings"].get(a, 0.0) - qty
            state["cash"] += qty * fill * (1 - self.fee)
            usd = qty * fill
        return {**order, "usd": round(usd, 2), "fill": round(fill, 2), "qty": qty}


class LiveBroker:
    def __init__(self, *_, **__):
        raise SystemExit(
            "Live trading is not enabled in this build. Exchange accounts require the account "
            "holder to be 18+, and the plan is to forward-test on paper for at least 3 months "
            "first. See README 'Going live'.")
