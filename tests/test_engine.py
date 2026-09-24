"""Engine, market feed, alerts and SSE payload tests with a fake exchange (no network)."""
import copy
import json
import os
import shutil
import tempfile
import unittest

from bot import candles, exchange, strategies, testnet
from bot.alerts import Alerts
from bot.db import Store
from bot.engine import HOLD, Engine, clean, sse
from bot.market import CandlesNotReady, CandleStore, MarketFeed

CFG = json.load(open(os.path.join(os.path.dirname(__file__), "..", "config.json")))
H = candles.HOUR
NOW_S = 1_790_000_000 // 3600 * 3600 + 30  # 30 s after an hourly close (UTC)

FILTERS = {
    "BTCUSDT": {"symbol": "BTCUSDT", "filters": [
        {"filterType": "LOT_SIZE", "minQty": "0.00001", "maxQty": "9000", "stepSize": "0.00001"},
        {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
        {"filterType": "NOTIONAL", "minNotional": "5"}]},
    "ETHUSDT": {"symbol": "ETHUSDT", "filters": [
        {"filterType": "LOT_SIZE", "minQty": "0.0001", "maxQty": "9000", "stepSize": "0.0001"},
        {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
        {"filterType": "NOTIONAL", "minNotional": "5"}]},
}


class Clock:
    def __init__(self, t=NOW_S):
        self.t = float(t)

    def __call__(self):
        return self.t


class FakePublic:
    """Order book one tick wide around a set price, plus hourly/daily klines up to 'now'."""

    def __init__(self, clock, prices=None):
        self.clock = clock
        self.prices = prices or {"BTCUSDT": 84_000.0, "ETHUSDT": 2_650.0}
        self.depth_calls = 0
        self.fail_depth = False
        self.publish_lag = 0

    def depth(self, symbol, limit=100):
        self.depth_calls += 1
        if self.fail_depth:
            raise OSError("network down")
        p = self.prices[symbol]
        tick = 0.01
        return {"bids": [(p - tick * (i + 1), 0.05) for i in range(20)],
                "asks": [(p + tick * i, 0.05) for i in range(20)]}

    def symbol_filters(self, symbol):
        return exchange.SymbolFilters.from_symbol_info(FILTERS[symbol])

    def book_ticker(self, symbols):
        return {s: (self.prices[s] - 0.01, self.prices[s]) for s in symbols}

    def klines(self, symbol, interval, limit=500, start_ms=None, end_ms=None):
        step = candles.INTERVAL_MS[interval]
        now = int(self.clock() * 1000) - self.publish_lag
        last_open = now // step * step  # still-forming candle, like the real API
        p = self.prices[symbol]
        return [(t, p, p, p, p, 1.0, t + step - 1) for t in range(last_open - (limit - 1) * step, last_open + 1, step)]


class Stub(strategies.Strategy):
    name, timeframe, rule = "STUB_H1", "1h", "test strategy"
    needs = {"1h": 3}

    def __init__(self):
        self.want = {"BTC": 1.0, "ETH": 0.0}
        self.calls = 0

    def decide(self, asset, cand, pos, now_ms):
        self.calls += 1
        return self.want[asset], {"reason": f"stub wants {self.want[asset]:.0%}", "close": cand["1h"][-1][4]}

    def describe(self, asset, info, price, pos):
        return f"{asset}: stub"


class Collector:
    def __init__(self):
        self.jobs = []

    def submit(self, **job):
        self.jobs.append(job)


class EngineCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.tmp)  # STOP file and data/ live here
        self.clock = Clock()
        self.public = FakePublic(self.clock)
        self.store = Store(":memory:")
        self.feed = MarketFeed(["BTCUSDT", "ETHUSDT"], self.public, clock=self.clock)
        self.feed.poll_once()
        self.stub = Stub()
        self.cfg = copy.deepcopy(CFG)

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.tmp)

    def engine(self, strategy_list=None, **kw):
        return Engine(self.cfg, self.store, self.feed, CandleStore(self.public), self.public,
                      [self.stub] if strategy_list is None else strategy_list, backtest={}, clock=self.clock, **kw)

    def minute(self, eng, minutes=1):
        for _ in range(minutes):
            self.clock.t += 60
            self.feed.poll_once()
            eng.tick()


class Accounts(EngineCase):
    def test_every_account_starts_with_15_and_benchmark_buys_50_50(self):
        eng = self.engine()
        self.assertEqual(set(eng.accounts), {HOLD, "STUB_H1"})
        for a in eng.accounts.values():
            self.assertEqual(a.s["start_cash"], 15.0)
        eng.tick()
        hold = eng.accounts[HOLD]
        self.assertTrue(hold.s["funded"])
        self.assertGreater(hold.s["holdings"]["BTC"], 0)
        self.assertGreater(hold.s["holdings"]["ETH"], 0)
        bal = hold.equity(eng.prices())
        self.assertLess(bal, 15.0)  # 0.1% fee on the way in
        self.assertGreater(bal, 14.9)
        self.assertAlmostEqual(hold.s["fees"], (15.0 - hold.s["cash"]) * 0.001, places=6)
        fills = [f for f in self.store.recent_fills() if f["account"] == HOLD]
        self.assertEqual({f["venue"] for f in fills}, {"demo"})

    def test_starts_within_a_second_of_the_first_price(self):
        self.feed = MarketFeed(["BTCUSDT", "ETHUSDT"], self.public, clock=self.clock)  # no prices yet
        eng = self.engine()
        eng.tick()
        eng.tick()
        self.assertFalse(eng.accounts[HOLD].s["funded"])
        self.assertEqual(sum(1 for e in self.store.recent_events() if "waiting for live prices" in e["text"]), 1)
        self.feed.poll_once()
        self.clock.t += 1
        eng.tick()
        self.assertTrue(eng.accounts[HOLD].s["funded"])

    def test_state_survives_restart(self):
        eng = self.engine()
        eng.tick()
        started = eng.accounts["STUB_H1"].s["started_ms"]
        cash = eng.accounts["STUB_H1"].s["cash"]
        eng2 = self.engine()
        self.assertEqual(eng2.accounts["STUB_H1"].s["started_ms"], started)
        self.assertEqual(eng2.accounts["STUB_H1"].s["cash"], cash)
        self.assertTrue(eng2.accounts[HOLD].s["funded"])

    def test_marks_to_market_every_minute(self):
        eng = self.engine()
        eng.tick()
        self.minute(eng, 3)
        pts = self.store.equity_history()[HOLD]
        self.assertEqual(len(pts), 4)
        self.assertTrue(all(p[0] % 60_000 == 0 for p in pts))


class Decisions(EngineCase):
    def test_buys_on_closed_candle_and_writes_receipt(self):
        eng = self.engine()
        eng.tick()
        acct = eng.accounts["STUB_H1"]
        self.assertGreater(acct.s["holdings"]["BTC"], 0)
        self.assertEqual(acct.s["holdings"]["ETH"], 0)
        self.assertEqual(acct.s["pos"]["BTC"]["exp"], 1.0)
        # 50% sleeve of $15, rounded down to the BTC stepSize
        self.assertAlmostEqual(acct.value("BTC", eng.prices()), 7.5, delta=0.9)
        rec = [r for r in self.store.receipts() if r["account"] == "STUB_H1"]
        btc = [r for r in rec if r["asset"] == "BTC"][0]
        self.assertIsNotNone(btc["fill_price"])
        self.assertGreater(btc["fee"], 0)
        self.assertIsNotNone(btc["slippage_bps"])
        self.assertEqual(json.loads(btc["signal"])["exposure"], 1.0)
        self.assertTrue(json.loads(btc["risk"])["ok"])
        self.assertIn("usd", json.loads(btc["orders"]))
        eth = [r for r in rec if r["asset"] == "ETH"][0]
        self.assertEqual(json.loads(eth["signal"])["action"], "none")

    def test_decides_only_once_per_closed_candle(self):
        eng = self.engine()
        eng.tick()
        calls = self.stub.calls
        self.minute(eng, 30)
        self.assertEqual(self.stub.calls, calls)
        self.minute(eng, 30)  # next hourly close
        self.assertEqual(self.stub.calls, calls + 2)

    def test_waits_for_candle_to_be_published(self):
        self.public.publish_lag = 2 * H * 1000  # exchange behind by two hours
        eng = self.engine()
        eng.tick()
        self.assertEqual(self.stub.calls, 0)
        self.public.publish_lag = 0
        self.minute(eng)
        self.assertEqual(self.stub.calls, 2)

    def test_retries_when_order_book_unavailable(self):
        eng = self.engine()
        eng.accounts[HOLD].s["funded"] = True
        self.public.fail_depth = True
        eng.tick()
        self.assertEqual(eng.accounts["STUB_H1"].s["holdings"]["BTC"], 0)
        self.public.fail_depth = False
        self.minute(eng)
        self.assertGreater(eng.accounts["STUB_H1"].s["holdings"]["BTC"], 0)

    def test_sell_after_buy_realizes_pnl(self):
        eng = self.engine()
        eng.tick()
        self.public.prices["BTCUSDT"] = 90_000.0
        self.stub.want["BTC"] = 0.0
        self.minute(eng, 60)
        acct = eng.accounts["STUB_H1"]
        self.assertLess(acct.value("BTC", eng.prices()), 1.0)  # at most dust below stepSize is left
        self.assertEqual(acct.s["sells"], 1)
        self.assertEqual(acct.s["wins"], 1)
        self.assertEqual(acct.s["pos"]["BTC"], {"in": False, "exp": 0.0})

    def test_order_below_exchange_minimum_is_skipped(self):
        eng = self.engine()
        self.stub.want["BTC"] = 0.2  # 0.5 * 0.2 * $15 = $1.50 < $5 minimum
        eng.tick()
        self.assertEqual(eng.accounts["STUB_H1"].s["holdings"]["BTC"], 0)
        rec = [r for r in self.store.receipts() if r["account"] == "STUB_H1" and r["asset"] == "BTC"][0]
        self.assertIn("below exchange minimum", rec["note"])


class RiskAndStop(EngineCase):
    def test_stop_blocks_buys_but_sells_go_through(self):
        eng = self.engine()
        eng.accounts[HOLD].s["funded"] = True
        eng.press_stop()
        self.assertTrue(os.path.exists("STOP"))
        eng.tick()
        acct = eng.accounts["STUB_H1"]
        self.assertEqual(acct.s["holdings"]["BTC"], 0)
        self.assertFalse(eng.risk_state["STUB_H1"]["ok"])
        self.assertIn("kill switch", " ".join(eng.risk_state["STUB_H1"]["reasons"]))
        rec = [r for r in self.store.receipts() if r["asset"] == "BTC"][0]
        self.assertTrue(rec["note"].startswith("buy blocked by risk"))
        # resume, buy, stop again, then a sell signal still goes through
        os.remove("STOP")
        self.minute(eng, 60)
        self.assertGreater(acct.s["holdings"]["BTC"], 0)
        eng.press_stop()
        self.stub.want["BTC"] = 0.0
        self.minute(eng, 60)
        self.assertLess(acct.value("BTC", eng.prices()), 1.0)
        self.assertEqual(acct.s["sells"], 1)

    def test_stop_takes_effect_within_a_minute(self):
        eng = self.engine()
        eng.tick()
        self.clock.t += 20
        eng.press_stop()
        self.assertFalse(eng.snapshot()["risk"][0]["ok"])  # dashboard shows it right away
        self.minute(eng)
        self.assertTrue(all(not r["ok"] for r in eng.risk_state.values()))

    def test_stale_prices_block_buys(self):
        eng = self.engine()
        eng.accounts[HOLD].s["funded"] = True
        self.clock.t += 600  # no price update for 10 minutes
        eng.tick()
        self.assertEqual(eng.accounts["STUB_H1"].s["holdings"]["BTC"], 0)
        self.assertIn("min old", " ".join(eng.risk_state["STUB_H1"]["reasons"]))

    def test_daily_loss_limit_blocks_buys(self):
        eng = self.engine([])
        eng.tick()
        self.public.prices = {"BTCUSDT": 84_000.0 * 0.8, "ETHUSDT": 2_650.0 * 0.8}
        self.minute(eng)
        reasons = " ".join(eng.risk_state[HOLD]["reasons"])
        self.assertIn("today (UTC)", reasons)


class Testnet(EngineCase):
    def test_selected_strategy_fills_are_mirrored_to_testnet(self):
        self.cfg["engine"]["testnet_strategy"] = "STUB_H1"
        col = Collector()
        eng = self.engine(testnet=col)
        eng.tick()
        self.assertEqual(len(col.jobs), 1)
        job = col.jobs[0]
        self.assertEqual((job["account"], job["asset"], job["side"]), ("STUB_H1", "BTC", "BUY"))
        self.assertGreater(job["usd"], 5)
        self.stub.want["BTC"] = 0.0
        self.minute(eng, 60)
        self.assertEqual(col.jobs[1]["side"], "SELL")
        self.assertEqual(col.jobs[1]["fraction"], 1.0)

    def test_blocked_testnet_does_not_stop_demo(self):
        class Blocked:
            has_keys = True

            def account(self):
                raise exchange.LocationBlocked("451", status=451)

        gate = testnet.TestnetGate(Blocked(), True, clock=self.clock)
        worker = testnet.TestnetWorker(testnet.TestnetTrader(Blocked(), gate, self.store, clock=self.clock), gate)
        self.cfg["engine"]["testnet_strategy"] = "STUB_H1"
        eng = self.engine(testnet=worker, gate=gate)
        eng.tick()
        self.assertGreater(eng.accounts["STUB_H1"].s["holdings"]["BTC"], 0)  # demo traded
        worker.run_once(timeout=0)
        snap = eng.snapshot()
        self.assertEqual(snap["banner"]["testnet_status"], "blocked")
        self.assertIn("TESTNET: blocked by exchange location", snap["banner"]["testnet"])
        fills = [f for f in snap["fills"] if f["venue"] == "testnet"]
        self.assertEqual(fills[0]["status"], "skipped")


class SSEPayload(EngineCase):
    def test_payload_shape_and_live_balance(self):
        eng = self.engine()
        eng.tick()
        snap = eng.snapshot()
        for key in ("ts", "banner", "market", "leaderboard", "states", "risk", "feed", "fills", "stopped"):
            self.assertIn(key, snap)
        self.assertEqual(snap["banner"]["demo"], "DEMO: no real money")
        self.assertIsNone(snap["banner"]["testnet"])
        self.assertEqual(snap["market"]["BTC"]["price"], 84_000.0 - 0.005)
        self.assertAlmostEqual(snap["market"]["BTC"]["spread"], 0.01)
        rows = snap["leaderboard"]
        self.assertEqual({r["account"] for r in rows}, {HOLD, "STUB_H1"})
        self.assertEqual([r["pnl_usd"] for r in rows], sorted([r["pnl_usd"] for r in rows], reverse=True))
        for r in rows:
            for k in ("balance", "pnl_usd", "pnl_pct", "max_dd", "trades", "win_rate", "fees", "backtest"):
                self.assertIn(k, r)
        self.assertEqual([r["backtest"] for r in rows if r["account"] == "STUB_H1"], ["NOT BACKTESTED"])
        self.assertEqual(snap["states"][1]["lines"], ["BTC: stub", "ETH: stub"])
        # the balance moves with the live price every second, between minute checks
        before = [r for r in rows if r["account"] == HOLD][0]["balance"]
        self.public.prices["BTCUSDT"] = 85_000.0
        self.feed.poll_once()
        self.clock.t += 1
        after = [r for r in eng.snapshot()["leaderboard"] if r["account"] == HOLD][0]["balance"]
        self.assertGreater(after, before)

    def test_sse_message_format(self):
        eng = self.engine()
        eng.tick()
        msg = sse(eng.snapshot())
        self.assertTrue(msg.startswith("data: "))
        self.assertTrue(msg.endswith("\n\n"))
        self.assertEqual(msg.count("\n"), 2)
        body = json.loads(msg[len("data: "):])
        self.assertEqual(body["banner"]["demo"], "DEMO: no real money")

    def test_no_nan_or_infinity_in_json(self):
        payload = clean({"a": float("nan"), "b": [float("inf"), 1.5], "c": {"d": float("-inf")}})
        self.assertEqual(payload, {"a": None, "b": [None, 1.5], "c": {"d": None}})
        self.assertNotIn("NaN", sse({"x": float("nan")}))

    def test_backtest_flag_shows_on_leaderboard(self):
        eng = Engine(self.cfg, self.store, self.feed, CandleStore(self.public), self.public, [self.stub],
                     backtest={"STUB_H1": {"verdict": "FAILED BACKTEST", "why": "lost money out of sample"}},
                     clock=self.clock)
        eng.tick()
        row = [r for r in eng.snapshot()["leaderboard"] if r["account"] == "STUB_H1"][0]
        self.assertEqual(row["backtest"], "FAILED BACKTEST")


class Feed(unittest.TestCase):
    def test_websocket_messages_update_quotes(self):
        clock = Clock()
        f = MarketFeed(["BTCUSDT", "ETHUSDT"], None, clock=clock)
        f.handle_ws_message(json.dumps({"u": 1, "s": "BTCUSDT", "b": "84000.00", "B": "1", "a": "84000.01", "A": "2"}))
        f.handle_ws_message(json.dumps({"e": "kline", "s": "ETHUSDT", "k": {"s": "ETHUSDT", "c": "2650.5", "x": False}}))
        f.handle_ws_message(json.dumps({"result": None, "id": 1}))  # subscribe ack
        q = f.quote("BTCUSDT")
        self.assertAlmostEqual(q["mid"], 84000.005)
        self.assertAlmostEqual(q["spread"], 0.01, places=6)
        self.assertEqual(f.quote("ETHUSDT")["mid"], 2650.5)
        self.assertEqual(f.status, "websocket")
        clock.t += 11
        self.assertEqual(f.status, "down")

    def test_rest_fallback(self):
        clock = Clock()
        f = MarketFeed(["BTCUSDT", "ETHUSDT"], FakePublic(clock), clock=clock)
        self.assertEqual(f.age_s(), float("inf"))
        f.poll_once()
        self.assertEqual(f.status, "rest fallback")
        self.assertEqual(f.quote("BTCUSDT")["source"], "rest")
        self.assertEqual(f.age_s(), 0)

    def test_candle_store_serves_closed_candles_and_caches(self):
        clock = Clock()
        pub = FakePublic(clock)
        calls = []
        orig = pub.klines
        pub.klines = lambda *a, **k: calls.append(a) or orig(*a, **k)
        cs = CandleStore(pub)
        rows = cs.get("BTCUSDT", "1h", 10, int(clock() * 1000))
        self.assertEqual(len(rows), 10)
        self.assertEqual(rows[-1][0] + H, int(clock() * 1000) // H * H)  # last CLOSED hour
        cs.get("BTCUSDT", "1h", 10, int(clock() * 1000) + 60_000)
        self.assertEqual(len(calls), 1)
        pub.publish_lag = 2 * H * 1000
        with self.assertRaises(CandlesNotReady):
            cs.get("BTCUSDT", "1h", 10, int(clock() * 1000) + H * 1000)


class AlertRules(unittest.TestCase):
    def test_risk_alert_only_on_change(self):
        al = Alerts(enabled=False)
        al.risk("A", ["kill switch file STOP exists"])
        al.risk("A", ["kill switch file STOP exists"])
        al.risk("A", [])
        self.assertEqual(len(al.sent), 2)
        self.assertIn("RISK BLOCK A", al.sent[0])
        self.assertIn("OK again", al.sent[1])

    def test_daily_leaderboard_at_0015_once(self):
        al = Alerts(Store(":memory:"), enabled=False)
        rows = [{"label": "TREND_D1", "balance": 15.2, "pnl_pct": 0.0133, "max_dd": 0.01, "trades": 2}]
        day = 1_790_035_200_000 // 86_400_000 * 86_400_000
        self.assertFalse(al.maybe_daily(day + 10 * 60_000, rows))
        self.assertTrue(al.maybe_daily(day + 15 * 60_000, rows))
        self.assertFalse(al.maybe_daily(day + 16 * 60_000, rows))
        self.assertIn("1. TREND_D1: $15.20 (+1.3%)", al.sent[0])


if __name__ == "__main__":
    unittest.main()
