"""The trading engine behind the dashboard.

Every second   read live prices (for the dashboard only; no trading decisions).
Every minute   mark every account to market, save an equity point, run bot/risk.py per
               account, then let each strategy decide if a new candle of ITS timeframe
               closed. Orders fill against the live order book (bot/orderbook.py) with the
               0.1% taker fee and the real exchange filters (min $5 order, stepSize).
Always         a risk block stops buys; sells that reduce risk still go through.
               Every decision writes a receipt to SQLite (data/bot.db).
Testnet        the selected strategy's fills are also sent to Binance Spot Testnet from
               a separate thread (bot/testnet.py); demo never waits on it.

No leverage, margin, futures, martingale, averaging down or grid logic, ever.
"""
import json
import math
import os
import threading
import time

from . import risk, strategies
from .accounts import ASSETS, Account
from .alerts import Alerts
from .candles import INTERVAL_MS, last_close_ms
from .orderbook import simulate_market_order

HOLD = "HOLD_50_50"
LABELS = {HOLD: "Hold 50/50 BTC/ETH"}
BACKTEST_FILE = "data/backtest_results.json"


def sym(asset):
    return f"{asset}USDT"


def load_backtest(path=BACKTEST_FILE):
    try:
        with open(path) as f:
            rep = json.load(f)
        return {n: {"verdict": s["verdict"], "why": s["why"]} for n, s in rep.get("strategies", {}).items()}
    except (OSError, ValueError, KeyError):
        return {}


def clean(x):
    """JSON-safe copy: NaN and infinity become null (JSON.parse rejects them)."""
    if isinstance(x, float):
        return x if math.isfinite(x) else None
    if isinstance(x, dict):
        return {k: clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [clean(v) for v in x]
    return x


def sse(payload):
    """One Server-Sent Events message."""
    return "data: " + json.dumps(clean(payload), separators=(",", ":")) + "\n\n"


def _hhmm(ms):
    return time.strftime("%H:%M UTC", time.gmtime(ms / 1000))


class Engine:
    def __init__(self, cfg, store, feed, candle_store, public, strategy_list, backtest=None,
                 testnet=None, gate=None, alerts=None, clock=time.time, market_label=""):
        self.cfg, self.ecfg = cfg, cfg["engine"]
        self.store, self.feed, self.candles, self.public = store, feed, candle_store, public
        self.strategies = {s.name: s for s in strategy_list}
        self.backtest = backtest if backtest is not None else load_backtest()
        self.testnet, self.gate = testnet, gate
        self.alerts = alerts or Alerts(store, enabled=False)
        self.clock = clock
        self.market_label = market_label
        self.fee = self.ecfg["fee_rate"]
        self.fees = {st.name: getattr(st, "fee_rate", None) or self.fee for st in strategy_list}
        self.start_cash = {st.name: getattr(st, "start_cash", None) or self.ecfg["start_cash"] for st in strategy_list}
        self.risk_cfg = {"risk": self.ecfg["risk"]}
        self.delay_ms = int(self.ecfg.get("decision_delay_seconds", 5) * 1000)
        self.lock = threading.RLock()
        self.risk_state = {}
        self.last_minute = None
        self.first_try = {}
        self.waiting_logged = False
        self._snap = (None, None)
        self.accounts = self._load_accounts()

    # ---------- setup ----------
    def now_ms(self):
        return int(self.clock() * 1000)

    def _load_accounts(self):
        saved = self.store.load_accounts()
        out = {}
        for name in [HOLD] + list(self.strategies):
            if name in saved:
                out[name] = Account(saved[name])
            else:
                cash = self.start_cash.get(name, self.ecfg["start_cash"])
                out[name] = Account.create(name, cash, self.now_ms())
                self.store.save_account(name, out[name].s)
                self.event(name, "account", f"{self.label(name)} demo account opened with ${cash:.2f}")
            self.risk_state[name] = {"ok": True, "reasons": [], "checked_ms": None}
        return out

    def fee_for(self, name):
        return self.fees.get(name, self.fee)

    def label(self, name):
        return LABELS.get(name, name)

    def event(self, account, kind, text):
        self.store.add_event(account, kind, text)

    def save(self):
        for name, a in self.accounts.items():
            self.store.save_account(name, a.s)

    def prices(self):
        return {a: self.feed.mid(sym(a)) for a in ASSETS}

    # ---------- main loop ----------
    def tick(self):
        minute = int((self.clock() * 1000 - self.delay_ms) // 60_000)
        if minute != self.last_minute and self.on_minute():
            self.last_minute = minute  # until prices arrive, try again every second

    def run_forever(self, stop):
        last_err = ""
        while not stop.is_set():
            try:
                self.tick()
            except Exception as e:  # keep running; show the problem in the feed once
                msg = f"engine error: {type(e).__name__}: {str(e)[:150]}"
                if msg != last_err:
                    self.event("engine", "error", msg)
                    last_err = msg
            stop.wait(1.0 - (self.clock() % 1.0) + 0.01)

    def on_minute(self):
        with self.lock:
            now = self.now_ms()
            prices = self.prices()
            if any(p is None for p in prices.values()):
                if not self.waiting_logged:
                    self.event("engine", "check", f"waiting for live prices (feed: {self.feed.status})")
                    self.waiting_logged = True
                return False
            self.waiting_logged = False
            self.fund_hold(prices, now)
            points = {}
            for name, a in self.accounts.items():
                eq = a.equity(prices)
                a.mark(eq, now)
                points[name] = eq
            self.store.add_equity(now // 60_000 * 60_000, points)
            blocked = {n: self.check_risk(a, prices, now) for n, a in self.accounts.items()}
            bad = {n: r for n, r in blocked.items() if r}
            if not bad:
                risk_text = "risk OK for all"
            elif len(bad) == len(blocked) and len({tuple(r) for r in bad.values()}) == 1:
                risk_text = "risk BLOCKED for all: " + "; ".join(next(iter(bad.values())))
            else:
                risk_text = "risk BLOCKED for " + ", ".join(f"{n} ({'; '.join(r)})" for n, r in bad.items())
            text = f"marked {len(points)} accounts at BTC {prices['BTC']:,.2f} / ETH {prices['ETH']:,.2f}; {risk_text}"
            self.event("engine", "check", text)
            for name, strat in self.strategies.items():
                self.maybe_decide(strat, self.accounts[name], now)
            self.save()
            self.alerts.maybe_daily(now, self.leaderboard(prices))
            return True

    # ---------- risk ----------
    def check_risk(self, acct, prices, now, orders=()):
        eq = acct.equity(prices)
        age_h = self.feed.age_s() / 3600
        state = {"peak_equity": acct.s["peak_equity"], "last_equity": acct.s["day_start_equity"]}
        problems, _ = risk.check(self.risk_cfg, state, eq, age_h, list(orders), loss_label="today (UTC)")
        self.risk_state[acct.name] = {"ok": not problems, "reasons": problems, "checked_ms": now}
        self.alerts.risk(acct.name, problems)
        return problems

    # ---------- orders ----------
    def market_order(self, acct, asset, side, usd=None, qty=None, max_usd=None):
        """Fill against the live book. Returns (status, sim, why); status is
        'filled', 'skipped' (exchange rules; not retried) or 'retry' (data problem).

        A buy aims at `usd`. The exchange only accepts whole quantity steps (0.00001 BTC is
        about $0.85), so it takes the nearest step as long as that costs no more than
        `max_usd` (the per-asset cap) and the cash available; otherwise it rounds down."""
        try:
            book = self.public.depth(sym(asset), 100)
            filters = self.public.symbol_filters(sym(asset)) if self.ecfg.get("apply_exchange_filters", True) else None
        except Exception as e:
            return "retry", None, f"order book unavailable ({str(e)[:80]})"
        fee = self.fee_for(acct.name)
        if side == "BUY":
            usd = min(usd, acct.s["cash"])
            if usd <= 0:
                return "skipped", None, "no cash"
            qty = simulate_market_order(book, "BUY", fee, quote_qty=usd)["qty"]
            if filters:
                limit = min(acct.s["cash"], usd if max_usd is None else max_usd)
                near = float(filters.round_qty(qty + float(filters.step_size) / 2))
                if near > float(filters.round_qty(qty)):
                    alt = simulate_market_order(book, "BUY", fee, base_qty=near)
                    if alt["complete"] and alt["notional"] <= limit + 1e-9:
                        qty = near
        mid = (book["bids"][0][0] + book["asks"][0][0]) / 2 if book["bids"] and book["asks"] else None
        if filters:
            qty, why = filters.market_qty(qty, mid or 0)
            if not qty:
                return "skipped", None, why
        if not qty or qty <= 0:
            return "skipped", None, "nothing to trade"
        sim = simulate_market_order(book, side, fee, base_qty=qty)
        if not sim["complete"]:
            return "retry", None, "order book too thin for this order"
        if side == "BUY" and sim["notional"] > acct.s["cash"] + 1e-9:
            sim = simulate_market_order(book, "BUY", fee,
                                        base_qty=float(filters.round_qty(qty * 0.995)) if filters else qty * 0.995)
        return "filled", sim, ""

    def book_fill(self, acct, asset, side, sim, reason, decision_ms, exposure=None):
        held_before = acct.s["holdings"][asset]
        if side == "BUY":
            acct.apply_buy(asset, sim)
        else:
            acct.apply_sell(asset, sim)
        if exposure is not None:
            strategies.on_fill(acct.s["pos"][asset], side, sim["avg_price"], decision_ms, exposure)
        self.store.add_fill(account=acct.name, venue="demo", asset=asset, side=side, qty=sim["qty"],
                            price=sim["avg_price"], notional=sim["notional"], fee=sim["fee_usd"], mid=sim["mid"],
                            slippage_bps=sim["slippage_bps"], status="filled", reason=reason)
        self.event(acct.name, "fill",
                   f"{self.label(acct.name)} {side} {sim['qty']:.6f} {asset} at {sim['avg_price']:,.2f} "
                   f"(${sim['notional']:.2f}, fee ${sim['fee_usd']:.4f}, {sim['slippage_bps']:.1f} bp vs mid, "
                   f"{sim['levels']} book level{'s' if sim['levels'] != 1 else ''})")
        self.alerts.trade(self.label(acct.name), "DEMO", side, asset, sim["qty"], sim["avg_price"],
                          sim["notional"], sim["fee_usd"], reason)
        if self.testnet and acct.name == self.ecfg.get("testnet_strategy"):
            job = {"account": acct.name, "asset": asset, "side": side, "decision_ms": decision_ms, "reason": reason}
            if side == "BUY":
                job["usd"], job["qty"] = sim["notional"], sim["qty"]
            elif exposure == 0 or not held_before:
                job["fraction"] = 1.0  # a full exit sells the whole testnet position (demo keeps sub-step dust)
            else:
                job["fraction"] = min(1.0, sim["qty"] / held_before)
            self.testnet.submit(**job)

    def fund_hold(self, prices, now):
        a = self.accounts[HOLD]
        if a.s["funded"]:
            return
        each = a.s["start_cash"] / len(ASSETS)
        for asset in ASSETS:
            if a.s["holdings"][asset] > 0:
                continue
            status, sim, why = self.market_order(a, asset, "BUY", usd=min(each, a.s["cash"]))
            if status == "filled":
                self.book_fill(a, asset, "BUY", sim, "buy and hold benchmark", now)
            else:
                self.event(HOLD, "order", f"benchmark {asset} buy not filled yet: {why}")
                return
        a.s["funded"] = True

    # ---------- decisions ----------
    def maybe_decide(self, strat, acct, now):
        close_ms = last_close_ms(strat.timeframe, now - self.delay_ms)
        if acct.s["decided_ms"] >= close_ms:
            return
        retry_until = close_ms + self.delay_ms + self.ecfg.get("order_retry_minutes", 10) * 60_000
        give_up = now >= retry_until
        try:
            windows = {a: {iv: self.candles.get(sym(a), iv, n, now) for iv, n in strat.needs.items()}
                       for a in ASSETS}
        except Exception as e:
            if self.first_try.get(strat.name) != close_ms:
                self.first_try[strat.name] = close_ms
                self.event(strat.name, "check", f"waiting for the {_hhmm(close_ms)} {strat.timeframe} candle ({str(e)[:80]})")
            if give_up:
                acct.s["decided_ms"] = close_ms
                self.event(strat.name, "error", f"skipped the {_hhmm(close_ms)} decision: no candle data")
            return
        prices = self.prices()
        decisions = {}
        for a in ASSETS:
            pos = acct.s["pos"][a]
            exp, info = strat.decide(a, windows[a], pos, close_ms)
            acct.s["info"][a] = info
            decisions[a] = exp
            self.event(strat.name, "signal",
                       f"{strat.name} {a} {strat.timeframe} close {windows[a][strat.timeframe][-1][4]:,.2f}: "
                       f"{info.get('reason', '')} (target {exp:.0%} of the {a} sleeve)")
        wants = [a for a in ASSETS if decisions[a] != acct.s["pos"][a].get("exp", 0.0)]
        wants.sort(key=lambda a: decisions[a] > acct.s["pos"][a].get("exp", 0.0))  # sells first
        problems = self.check_risk(acct, prices, now, orders=wants)
        pending = False
        for a in ASSETS:
            if a in wants:
                pending |= self.execute(strat, acct, a, decisions[a], close_ms, problems, prices, give_up) == "retry"
            else:
                self.store.add_receipt(acct.name, a, close_ms, acct.s["info"][a],
                                       {"exposure": decisions[a], "action": "none"},
                                       {"ok": not problems, "reasons": problems}, None)
        if not pending or give_up:
            acct.s["decided_ms"] = close_ms

    def execute(self, strat, acct, asset, exp, decision_ms, problems, prices, give_up):
        s = acct.s
        prev = s["pos"][asset].get("exp", 0.0)
        side = "BUY" if exp > prev else "SELL"
        target_w = risk.clamp_targets({asset: strategies.SLEEVES[asset] * exp}, self.risk_cfg)[asset]
        info = s["info"][asset]
        reason = info.get("reason", "")
        eq = acct.equity(prices)
        risk_r = {"ok": not problems, "reasons": problems}
        signal = {"exposure": exp, "previous": prev, "target_weight": target_w, "equity": round(eq, 4)}
        if side == "BUY" and problems:
            note = "buy blocked by risk: " + "; ".join(problems)
            self.store.add_receipt(acct.name, asset, decision_ms, info, signal, risk_r, None, note=note)
            self.event(acct.name, "risk", f"{strat.name} {asset} {note}")
            return "blocked"
        if side == "BUY":
            order = {"side": "BUY", "usd": round(max(0.0, target_w * eq - acct.value(asset, prices)), 4)}
            nothing = order["usd"] < 0.01
        else:
            held = s["holdings"][asset]
            excess = acct.value(asset, prices) - target_w * eq
            qty = held if exp == 0 else min(held, max(0.0, excess) / prices[asset])
            order = {"side": "SELL", "qty": qty}
            nothing = qty * prices[asset] < 0.01
        if nothing:  # price moves already put the sleeve at its target
            s["pos"][asset]["exp"] = exp
            self.store.add_receipt(acct.name, asset, decision_ms, info, signal, risk_r, order,
                                   note="already at target, no order needed")
            return "done"
        self.event(acct.name, "order", f"{strat.name} {asset} market {side} "
                   + (f"${order['usd']:.2f}" if side == "BUY" else f"{order['qty']:.6f} {asset}") + f": {reason}")
        cap_room = self.risk_cfg["risk"]["max_weight_per_asset"] * eq - acct.value(asset, prices)
        status, sim, why = self.market_order(acct, asset, side, usd=order.get("usd"), qty=order.get("qty"),
                                             max_usd=cap_room if side == "BUY" else None)
        if status == "filled":
            self.book_fill(acct, asset, side, sim, reason, decision_ms, exposure=exp)
            self.store.add_receipt(acct.name, asset, decision_ms, info, signal, risk_r, order,
                                   fill_price=sim["avg_price"], fee=sim["fee_usd"], slippage_bps=sim["slippage_bps"],
                                   note=f"filled {sim['qty']:.8f} over {sim['levels']} level(s), mid {sim['mid']:.2f}")
            return "filled"
        if status == "skipped" and side == "SELL" and exp == 0 and s["holdings"][asset] * prices[asset] < 5.5:
            # dust below the exchange minimum can never be sold: treat the exit as done
            strategies.on_fill(s["pos"][asset], "SELL", prices[asset], decision_ms, 0.0)
        note = f"{status}: {why}"
        self.store.add_receipt(acct.name, asset, decision_ms, info, signal, risk_r, order, note=note)
        if status == "retry" and not give_up:
            self.event(acct.name, "order", f"{strat.name} {asset} {side} not filled, will retry next minute: {why}")
        else:
            self.event(acct.name, "order", f"{strat.name} {asset} {side} not sent: {why}")
        return status

    # ---------- dashboard ----------
    def leaderboard(self, prices):
        rows = []
        for name, a in self.accounts.items():
            r = a.summary(prices)
            r["label"] = self.label(name)
            if name == HOLD:
                r["backtest"], r["backtest_why"] = "BENCHMARK", "buys $7.50 BTC and $7.50 ETH at start and holds"
                r["timeframe"], r["rule"] = "", "Buy and hold 50% BTC / 50% ETH"
            else:
                bt = self.backtest.get(name, {"verdict": "NOT BACKTESTED", "why": "run python3 backtest_strategies.py"})
                r["backtest"], r["backtest_why"] = bt["verdict"], bt["why"]
                r["timeframe"], r["rule"] = self.strategies[name].timeframe, self.strategies[name].rule
            r["testnet"] = bool(self.testnet) and name == self.ecfg.get("testnet_strategy")
            r["start_cash"], r["fee_rate"] = a.s["start_cash"], self.fee_for(name)
            r["note"] = getattr(self.strategies.get(name), "note", "") if name != HOLD else ""
            rows.append(r)
        rows.sort(key=lambda r: -r["pnl_pct"])  # accounts start with $15 or $20, so compare in %
        return rows

    def states(self, prices, now):
        out = []
        for name, a in self.accounts.items():
            if name == HOLD:
                h = {x: a.value(x, prices) for x in ASSETS}
                text = ([f"holding ${h['BTC']:.2f} of BTC and ${h['ETH']:.2f} of ETH since "
                         f"{time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(a.s['started_ms'] / 1000))}"]
                        if a.s["funded"] else ["waiting to buy the 50/50 benchmark"])
                out.append({"account": name, "label": self.label(name), "lines": text, "next": None})
                continue
            st = self.strategies[name]
            lines = []
            for x in ASSETS:
                info, pos = a.s["info"].get(x) or {}, a.s["pos"][x]
                lines.append(st.describe(x, info, prices[x], pos) if info and prices[x] else f"{x}: waiting for the first decision")
            nxt = last_close_ms(st.timeframe, now) + INTERVAL_MS[st.timeframe]
            out.append({"account": name, "label": name, "lines": lines, "next": nxt,
                        "next_text": f"next {st.timeframe} decision at {_hhmm(nxt)}"})
        return out

    def snapshot(self):
        sec = int(self.clock())
        if self._snap[0] == sec:
            return self._snap[1]
        # The minute cycle holds the lock while it talks to the exchange. Rather than stall
        # the live stream, serve the previous snapshot for that second or two.
        if not self.lock.acquire(timeout=0.3):
            return self._snap[1] or {"ts": self.now_ms(), "busy": True}
        try:
            return self._build_snapshot(sec)
        finally:
            self.lock.release()

    def _build_snapshot(self, sec):
        now = self.now_ms()
        prices = self.prices()
        ok_prices = all(p is not None for p in prices.values())
        market = {"status": self.feed.status, "label": self.market_label}
        for x in ASSETS:
            q = self.feed.quote(sym(x))
            market[x] = {"price": q["mid"], "bid": q["bid"], "ask": q["ask"], "spread": q["spread"],
                         "spread_bps": q["spread_bps"], "age_s": q["age_s"], "source": q["source"]}
        stopped = os.path.exists(risk.KILL_SWITCH)
        risk_rows = []
        for name in self.accounts:
            r = dict(self.risk_state.get(name, {"ok": True, "reasons": []}))
            reasons = list(r.get("reasons", []))
            if stopped and not any("kill switch" in x for x in reasons):
                reasons.insert(0, "kill switch file STOP exists")
            risk_rows.append({"account": name, "label": self.label(name), "ok": not reasons,
                              "reasons": reasons, "checked_ms": r.get("checked_ms")})
        gate = self.gate
        snap = clean({
            "ts": now,
            "banner": {"demo": "DEMO: no real money",
                       "testnet": gate.message if gate and gate.status != "off" else None,
                       "testnet_status": gate.status if gate else "off",
                       "market_label": self.market_label},
            "market": market,
            "leaderboard": self.leaderboard(prices) if ok_prices else [],
            "states": self.states(prices, now) if ok_prices else [],
            "risk": risk_rows,
            "stopped": stopped,
            "feed": self.store.recent_events(80),
            "fills": self.store.recent_fills(50),
            "strategies_wired": list(self.strategies),
        })
        self._snap = (sec, snap)
        return snap

    def history(self):
        return {"accounts": {n: self.label(n) for n in self.accounts},
                "start": {n: a.s["start_cash"] for n, a in self.accounts.items()},
                "equity": self.store.equity_history()}

    def press_stop(self, who="dashboard"):
        with open(risk.KILL_SWITCH, "w") as f:
            f.write(f"created from the {who} at {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}\n")
        self.event("engine", "risk", f"STOP pressed on the {who}: new buys are halted for every account; "
                   "sells that cut risk still go through. Delete the STOP file to resume.")
        self._snap = (None, None)
