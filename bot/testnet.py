"""Binance Spot Testnet: fake funds, real order placement and fills.

TestnetGate decides whether testnet can be used right now. HTTP 451 (the
exchange blocks this location) marks it "blocked by exchange location" and
nothing is sent again until a single re-check 15 minutes later. There is no
retry loop, and the demo accounts never wait on testnet.

TestnetTrader mirrors one strategy's demo orders onto testnet:
  - buys spend the same USDT as the demo order (quoteOrderQty)
  - sells sell the same fraction of the testnet position the demo account sold
  - every order gets a deterministic newClientOrderId that is written to
    SQLite *before* sending, so the same decision can never be sent twice; a
    timeout is reconciled by looking the order up, never by re-sending
  - LOT_SIZE / MARKET_LOT_SIZE / NOTIONAL filters come from /api/v3/exchangeInfo

Manual check (Done item 2), with TESTNET=1 and keys in .env:
  python3 -m bot.testnet --status
  python3 -m bot.testnet --roundtrip        # ~$15 BTC buy, then sell it back
"""
import queue
import sys
import time

from . import env
from .exchange import TESTNET_BASE, BinanceSpot, ExchangeError, LocationBlocked, client_order_id
from .orderbook import mid_price, simulate_market_order

RECHECK_S = 15 * 60
SIM_FEE = 0.001


def _short(e, n=120):
    s = str(e).replace("\n", " ")
    return s[:n]


class TestnetGate:
    def __init__(self, adapter, enabled, clock=time.time):
        self.adapter, self.enabled, self.clock = adapter, enabled, clock
        if not enabled:
            self.status = "off"
        elif not getattr(adapter, "has_keys", False):
            self.status = "no_keys"
        else:
            self.status = "unknown"
        self.detail = ""
        self.next_check = 0.0
        self.checks = 0

    @property
    def message(self):
        when = time.strftime("%H:%M UTC", time.gmtime(self.next_check)) if self.next_check else ""
        return {
            "off": "TESTNET: off (set TESTNET=1 in .env to mirror orders)",
            "no_keys": "TESTNET: on, but TESTNET_API_KEY / TESTNET_API_SECRET are missing from .env",
            "unknown": "TESTNET: connecting",
            "ok": "TESTNET: fake funds, real orders",
            "blocked": f"TESTNET: blocked by exchange location (re-check at {when})",
            "bad_keys": f"TESTNET: keys rejected by testnet, check .env (re-check at {when})",
            "error": f"TESTNET: unreachable, {self.detail} (re-check at {when})",
        }[self.status]

    def usable(self):
        if self.status in ("off", "no_keys"):
            return False
        if self.status == "ok":
            return True
        if self.clock() < self.next_check:
            return False  # waiting for the next scheduled re-check; no retry loop
        return self.check()

    def check(self):
        """One signed request (proves both location and keys)."""
        self.checks += 1
        try:
            self.adapter.account()
        except LocationBlocked:
            self.mark_blocked()
            return False
        except ExchangeError as e:
            if e.code in (-2014, -2015, -1022):
                self._wait("bad_keys", "")
            else:
                self.mark_error(e)
            return False
        except Exception as e:  # network down, DNS, timeout
            self.mark_error(e)
            return False
        self.status, self.detail, self.next_check = "ok", "", 0.0
        return True

    def _wait(self, status, detail):
        self.status, self.detail = status, detail
        self.next_check = self.clock() + RECHECK_S

    def mark_blocked(self):
        self._wait("blocked", "")

    def mark_error(self, e):
        self._wait("error", _short(e, 80))


class TestnetTrader:
    def __init__(self, adapter, gate, store, clock=time.time):
        self.adapter, self.gate, self.store, self.clock = adapter, gate, store, clock

    def positions(self):
        return self.store.get_meta("testnet_positions", {})

    def _set_position(self, account, asset, qty):
        pos = self.positions()
        pos.setdefault(account, {})[asset] = max(0.0, qty)
        self.store.set_meta("testnet_positions", pos)

    def _record(self, account, asset, side, status, reason, **extra):
        self.store.add_fill(account=account, venue="testnet", asset=asset, side=side,
                            status=status, reason=reason, **extra)
        return {"status": status, "reason": reason, **extra}

    def mirror(self, account, asset, side, decision_ms, usd=None, fraction=None, reason="", qty=None):
        """Send the testnet copy of a demo order. Returns a result dict; never raises."""
        side = side.upper()
        symbol = f"{asset}USDT"
        if not self.gate.usable():
            return self._record(account, asset, side, "skipped", self.gate.message)
        cid = client_order_id(account, asset, side, decision_ms)
        if self.store.testnet_order(cid):
            return {"status": "duplicate", "reason": f"{cid} was already sent; not sending twice"}
        try:
            filters = self.adapter.symbol_filters(symbol)
            book = self.adapter.depth(symbol, 100)
            mid = mid_price(book)
            if side == "BUY" and qty:  # same coin quantity as the demo fill
                q, why = filters.market_qty(qty, mid or 0)
                if not q:
                    return self._record(account, asset, side, "skipped", why)
                req = {"quantity": q}
                sim = simulate_market_order(book, side, SIM_FEE, base_qty=q)
            elif side == "BUY":
                ok, why = filters.check_quote(usd or 0)
                if not ok:
                    return self._record(account, asset, side, "skipped", why)
                req = {"quote_qty": round(usd, 2)}
                sim = simulate_market_order(book, side, SIM_FEE, quote_qty=req["quote_qty"])
            else:
                held = self.positions().get(account, {}).get(asset, 0.0)
                qty = held * (fraction if fraction is not None else 1.0)
                qty = min(qty, self.adapter.balances().get(asset, 0.0))
                q, why = filters.market_qty(qty, mid or 0)
                if not q:
                    return self._record(account, asset, side, "skipped", why or "nothing to sell")
                req = {"quantity": q}
                sim = simulate_market_order(book, side, SIM_FEE, base_qty=q)
        except LocationBlocked:
            self.gate.mark_blocked()
            return self._record(account, asset, side, "skipped", self.gate.message)
        except Exception as e:
            self.gate.mark_error(e)
            return self._record(account, asset, side, "skipped", self.gate.message)

        if not self.store.claim_client_order_id(cid, account, asset, side, req):
            return {"status": "duplicate", "reason": f"{cid} was already sent; not sending twice"}
        try:
            o = self.adapter.market_order(symbol, side, cid, **req)
        except LocationBlocked:
            self.store.update_testnet_order(cid, "blocked")
            self.gate.mark_blocked()
            return self._record(account, asset, side, "skipped", self.gate.message, client_order_id=cid)
        except ExchangeError as e:
            self.store.update_testnet_order(cid, "rejected", {"error": _short(e)})
            return self._record(account, asset, side, "rejected", _short(e), client_order_id=cid)
        except Exception as e:  # timeout: the order may or may not exist. Look it up, never re-send.
            self.store.update_testnet_order(cid, "unknown", {"error": _short(e)})
            try:
                o = self.adapter.order_status(symbol, cid)
            except Exception as e2:
                self.gate.mark_error(e2)
                return self._record(account, asset, side, "unknown",
                                    f"sent, no reply ({_short(e, 60)}); will not re-send", client_order_id=cid)

        self.store.update_testnet_order(cid, (o.get("status") or "unknown").lower(), o)
        if not o["qty"]:
            return self._record(account, asset, side, (o.get("status") or "unknown").lower(),
                                "order not filled", client_order_id=cid)
        avg = o["avg_price"]
        comm = o.get("commission", {})
        fee_usd = comm.get("USDT", 0.0) + comm.get(asset, 0.0) * avg
        other_fee = [a for a in comm if a not in ("USDT", asset) and comm[a]]
        real_slip = ((avg - mid) / mid if side == "BUY" else (mid - avg) / mid) * 1e4 if mid else None
        held = self.positions().get(account, {}).get(asset, 0.0)
        if side == "BUY":
            self._set_position(account, asset, held + o["qty"] - comm.get(asset, 0.0))
        else:
            self._set_position(account, asset, held - o["qty"])
        note = reason + (f"; fee paid in {', '.join(other_fee)}" if other_fee else "")
        return self._record(account, asset, side, "filled", note.strip("; "), qty=o["qty"], price=avg,
                            notional=o["quote"], fee=fee_usd, mid=mid, slippage_bps=real_slip,
                            sim_slippage_bps=sim["slippage_bps"], client_order_id=cid)


class TestnetWorker:
    """Sends testnet orders from its own thread, so demo trading never waits on testnet."""

    def __init__(self, trader, gate, on_result=None):
        self.trader, self.gate = trader, gate
        self.on_result = on_result or (lambda job, result: None)
        self._q = queue.Queue()

    def submit(self, **job):
        if self.gate.status not in ("off", "no_keys"):
            self._q.put(job)

    def run_once(self, timeout=5.0):
        try:
            job = self._q.get(timeout=timeout)
        except queue.Empty:
            self.gate.usable()  # only touches the network when a scheduled re-check is due
            return None
        result = self.trader.mirror(**job)
        self.on_result(job, result)
        return result

    def run_forever(self, stop):
        self.gate.usable()  # first check right away so the dashboard shows the real status
        while not stop.is_set():
            try:
                self.run_once()
            except Exception as e:  # never let testnet take anything else down
                self.gate.mark_error(e)


def make_adapter():
    key, secret = env.testnet_keys()
    return BinanceSpot(TESTNET_BASE, key, secret, label="TESTNET")


def main(argv):
    from .db import Store
    env.load()
    adapter = make_adapter()
    gate = TestnetGate(adapter, env.testnet_enabled())
    if gate.status in ("off", "no_keys"):
        print(gate.message)
        return 1
    ok = gate.usable()
    print(gate.message)
    if not ok or "--roundtrip" not in argv:
        return 0 if ok else 2
    usd = float(argv[argv.index("--usd") + 1]) if "--usd" in argv else 15.0
    trader = TestnetTrader(adapter, gate, Store())
    t = int(time.time() * 1000)
    for side, kw in (("BUY", {"usd": usd}), ("SELL", {"fraction": 1.0})):
        r = trader.mirror("TESTNET_CHECK", "BTC", side, t, reason="manual round trip", **kw)
        line = f"{side}: {r['status']}"
        if r["status"] == "filled":
            line += (f" {r['qty']:.6f} BTC at {r['price']:,.2f} (${r['notional']:.2f}), "
                     f"slippage real {r['slippage_bps']:.1f} bp vs simulated {r['sim_slippage_bps']:.1f} bp")
        else:
            line += f" ({r.get('reason', '')})"
        print(line)
        if r["status"] != "filled":
            return 3
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
