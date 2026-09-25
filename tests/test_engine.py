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
from bot.engine import HOLD, Engine, clean, sse, stream_delta
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
        self.prices = prices or {"BTCUSDT": 84_000.0, "ETHUSDT": 2_650.0, "SOLUSDT": 180.0,
                                 "XRPUSDT": 0.6, "PEPEUSDT": 0.00001}
        self.depth_calls = 0
        self.fail_depth = False
        self.publish_lag = 0
        self.silent = set()  # symbols the REST ticker stops returning (no fresh price)

    def depth(self, symbol, limit=100):
        self.depth_calls += 1
        if self.fail_depth:
            raise OSError("network down")
        p = self.prices[symbol]
        tick = 0.01 if p >= 1 else p * 1e-4
        size = 0.05 if symbol in FILTERS else 4_200 / p  # about $4,200 per level
        return {"bids": [(p - tick * (i + 1), size) for i in range(20)],
                "asks": [(p + tick * i, size) for i in range(20)]}

    def symbol_filters(self, symbol):
        if symbol in FILTERS:
            return exchange.SymbolFilters.from_symbol_info(FILTERS[symbol])
        step = {"SOLUSDT": "0.001", "XRPUSDT": "0.1", "PEPEUSDT": "1"}.get(symbol, "0.001")
        return exchange.SymbolFilters(symbol, step, step, "90000000000", "0.00000001", "5")

    def book_ticker(self, symbols):
        out = {}
        for s in symbols:
            if s in self.silent:
                continue
            p = self.prices[s]
            out[s] = (p - (0.01 if p >= 1 else p * 1e-4), p)
        return out

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


class CoinStub(Stub):
    """A strategy on other coins: half the account per coin, the rest in cash."""

    def __init__(self, name, sleeves, want, start_cash=20.0):
        super().__init__()
        self.name, self.sleeves, self.want, self.start_cash = name, sleeves, want, start_cash


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


class VariantAccounts(EngineCase):
    def test_variant_starts_with_its_own_cash_and_pays_its_own_fee(self):
        cheap, dear = Stub(), Stub()
        dear.name, dear.start_cash, dear.fee_rate = "STUB_HIGH_FEE", 20.0, 0.004
        eng = self.engine([cheap, dear])
        self.assertEqual(eng.accounts["STUB_HIGH_FEE"].s["start_cash"], 20.0)
        self.assertEqual(eng.accounts["STUB_H1"].s["start_cash"], 15.0)
        eng.tick()
        fills = {f["account"]: f for f in self.store.recent_fills() if f["asset"] == "BTC" and f["account"] != HOLD}
        for name, rate in (("STUB_H1", 0.001), ("STUB_HIGH_FEE", 0.004)):
            self.assertAlmostEqual(fills[name]["fee"], fills[name]["notional"] * rate)
        rows = eng.snapshot()["leaderboard"]
        self.assertEqual({r["account"]: r["start_cash"] for r in rows}["STUB_HIGH_FEE"], 20.0)
        self.assertEqual([r["pnl_pct"] for r in rows], sorted([r["pnl_pct"] for r in rows], reverse=True))


class OtherCoins(EngineCase):
    def feed_for(self, *symbols):
        self.feed = MarketFeed(list(symbols), self.public, clock=self.clock)
        self.feed.poll_once()

    def test_account_trades_only_its_own_coins(self):
        self.feed_for("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
        alts = CoinStub("ALTS", {"SOL": 0.5, "XRP": 0.5}, {"SOL": 1.0, "XRP": 0.0})
        eng = self.engine([alts])
        eng.tick()
        acct = eng.accounts["ALTS"]
        self.assertEqual(acct.s["start_cash"], 20.0)
        self.assertEqual(set(acct.s["holdings"]), {"SOL", "XRP"})
        self.assertAlmostEqual(acct.value("SOL", eng.prices()), 10.0, delta=0.2)
        self.assertEqual(acct.s["holdings"]["XRP"], 0.0)
        row = [r for r in eng.snapshot()["leaderboard"] if r["account"] == "ALTS"][0]
        self.assertEqual(row["coins"], ["SOL", "XRP"])
        self.assertIn("SOL", eng.snapshot()["market"]["coins"])

    def test_memecoin_order_and_price_text(self):
        self.feed_for("BTCUSDT", "ETHUSDT", "PEPEUSDT")
        meme = CoinStub("MEME", {"PEPE": 0.5}, {"PEPE": 1.0})
        eng = self.engine([meme])
        eng.tick()
        fill = [f for f in self.store.recent_fills() if f["account"] == "MEME"][0]
        self.assertEqual(fill["qty"], int(fill["qty"]))  # PEPE trades in whole units
        self.assertAlmostEqual(fill["notional"], 10.0, delta=0.01)
        text = [e["text"] for e in self.store.recent_events() if e["kind"] == "fill" and "PEPE" in e["text"]][0]
        self.assertIn("1,000,000 PEPE at 0.00001", text)

    def test_missing_price_pauses_only_accounts_that_use_it(self):
        self.public.silent = {"PEPEUSDT"}
        self.feed_for("BTCUSDT", "ETHUSDT", "PEPEUSDT")
        meme = CoinStub("MEME", {"PEPE": 0.5}, {"PEPE": 1.0})
        eng = self.engine([self.stub, meme])
        eng.tick()
        self.assertFalse(eng.accounts[HOLD].s["funded"])  # first 2 minutes: wait for every coin
        self.minute(eng, 3)
        self.assertTrue(eng.accounts[HOLD].s["funded"])
        self.assertGreater(eng.accounts["STUB_H1"].s["holdings"]["BTC"], 0)
        self.assertEqual(eng.accounts["MEME"].s["holdings"]["PEPE"], 0.0)
        self.assertNotIn("MEME", {r["account"] for r in eng.snapshot()["leaderboard"]})
        errors = [e["text"] for e in self.store.recent_events() if e["kind"] == "error"]
        self.assertEqual(errors, ["no live price for PEPE: accounts that use it are paused"])

    def test_stale_coin_blocks_buys_only_where_it_is_used(self):
        self.feed_for("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
        alts = CoinStub("ALTS", {"SOL": 0.5, "XRP": 0.5}, {"SOL": 0.0, "XRP": 0.0})
        eng = self.engine([self.stub, alts])
        eng.tick()
        self.public.silent = {"SOLUSDT"}  # SOL stops updating
        self.minute(eng, 10)
        self.assertIn("min old", " ".join(eng.risk_state["ALTS"]["reasons"]))
        self.assertTrue(eng.risk_state["STUB_H1"]["ok"])
        self.assertEqual(self.feed.age_s(["BTCUSDT"]), 0)


class LongShortAccounts(EngineCase):
    def ls(self, want):
        st = CoinStub("LS", {"BTC": 0.5, "ETH": 0.5}, want)
        st.allows_short = True
        return st

    def test_opens_a_short_and_profits_when_price_falls(self):
        st = self.ls({"BTC": -1.0, "ETH": 0.0})
        eng = self.engine([st])
        eng.tick()
        acct = eng.accounts["LS"]
        self.assertLess(acct.s["holdings"]["BTC"], 0)  # short
        # $20 + the sale proceeds. The $10 target is 0.000119 BTC; the step is 0.00001 BTC and
        # 0.00012 ($10.08) would break the 50% cap, so it sells 0.00011 BTC ($9.24)
        self.assertAlmostEqual(-acct.s["holdings"]["BTC"], 0.00011)
        self.assertAlmostEqual(acct.s["cash"], 20 + 0.00011 * 84_000 * 0.999, delta=0.01)
        start_eq = acct.equity(eng.prices())
        self.assertAlmostEqual(start_eq, 20.0, delta=0.05)  # only the fee (and spread) is lost
        self.public.prices["BTCUSDT"] = 84_000.0 * 0.9
        self.feed.poll_once()
        self.assertAlmostEqual(acct.equity(eng.prices()) - start_eq, 0.00011 * 8_400, delta=0.01)  # 10% fall
        fill = [f for f in self.store.recent_fills() if f["account"] == "LS"][0]
        self.assertEqual((fill["side"], fill["reason"].split(":")[0]), ("SELL", "open short"))

    def test_flip_from_short_to_long(self):
        st = self.ls({"BTC": -1.0, "ETH": 0.0})
        eng = self.engine([st])
        eng.tick()
        self.public.prices["BTCUSDT"] = 80_000.0
        st.want["BTC"] = 1.0
        self.minute(eng, 60)
        acct = eng.accounts["LS"]
        self.assertGreater(acct.s["holdings"]["BTC"], 0)  # now long
        self.assertEqual((acct.s["sells"], acct.s["wins"]), (1, 1))  # the short was closed at a profit
        legs = [f["reason"].split(":")[0] for f in self.store.recent_fills() if f["account"] == "LS"]
        self.assertEqual(legs[:3], ["open long", "close short", "open short"])  # newest first

    def test_stop_blocks_opening_but_not_closing(self):
        st = self.ls({"BTC": -1.0, "ETH": 0.0})
        eng = self.engine([st])
        eng.tick()
        eng.press_stop()
        st.want["BTC"] = 1.0
        self.minute(eng, 60)
        acct = eng.accounts["LS"]
        self.assertEqual(acct.s["holdings"]["BTC"], 0.0)  # the short was closed, the long not opened
        self.assertEqual(acct.s["pos"]["BTC"]["exp"], 0.0)
        self.assertTrue(any("open long blocked by risk" in e["text"] for e in self.store.recent_events()))

    def test_borrow_cost_while_short(self):
        st = self.ls({"BTC": -1.0, "ETH": 0.0})
        eng = self.engine([st])
        eng.tick()
        acct = eng.accounts["LS"]
        short_value = -acct.s["holdings"]["BTC"] * eng.prices()["BTC"]
        self.minute(eng, 60)
        self.assertAlmostEqual(acct.s["borrow_fees"], short_value * 0.10 / (365 * 24), places=7)
        self.assertEqual(eng.accounts[HOLD].s.get("borrow_fees", 0.0), 0.0)  # long-only pays nothing

    def test_long_only_accounts_never_go_short(self):
        self.stub.want["BTC"] = -1.0  # a long-only strategy asking for a short is treated as cash
        eng = self.engine()
        eng.tick()
        self.assertGreaterEqual(eng.accounts["STUB_H1"].s["holdings"]["BTC"], 0.0)
        self.assertEqual(eng.accounts["STUB_H1"].s["pos"]["BTC"].get("exp", 0.0), 0.0)


class RampPublic(FakePublic):
    """1-minute BTC candles rising 0.02% a minute into the current price (a scalper's long signal)."""

    def klines(self, symbol, interval, limit=500, start_ms=None, end_ms=None):
        rows = super().klines(symbol, interval, limit, start_ms, end_ms)
        if interval != "1m" or symbol != "BTCUSDT":
            return rows
        n = len(rows)
        return [(r[0],) + (r[4] * (1 - 0.0002 * (n - 1 - i)),) * 4 + tuple(r[5:]) for i, r in enumerate(rows)]


class TradeLog(Alerts):
    def __init__(self):
        super().__init__(None, enabled=False)
        self.trades = []

    def trade(self, *a, **k):
        self.trades.append(a)


class Scalping(EngineCase):
    def test_scalper_opens_takes_profit_and_stays_quiet(self):
        self.public = RampPublic(self.clock)
        self.feed = MarketFeed(["BTCUSDT", "ETHUSDT"], self.public, clock=self.clock)
        self.feed.poll_once()
        st = strategies.Scalp()
        log = TradeLog()
        eng = self.engine([st], alerts=log)
        eng.tick()
        acct = eng.accounts["SCALP"]
        self.assertGreater(acct.s["holdings"]["BTC"], 0)  # BTC rose 0.3% in 15 minutes: long
        self.assertEqual(acct.s["holdings"]["ETH"], 0.0)  # ETH is flat: waits
        entry = acct.s["pos"]["BTC"]["entry"]
        self.minute(eng)  # nothing moved: keeps holding, and the feed gets no per-minute signal lines
        signals = [e for e in self.store.recent_events() if e["kind"] == "signal"]
        self.assertEqual(len(signals), 1)  # only the one that opened the trade
        self.assertEqual(len(self.store.receipts(account="SCALP")), 4)  # but every decision has a receipt
        self.public.prices["BTCUSDT"] *= 1.004
        self.minute(eng)
        self.assertEqual(acct.s["pos"]["BTC"]["exp"], 0.0)  # +0.3% reached: closed
        self.assertLess(acct.s["holdings"]["BTC"], 0.00001)  # at most one exchange step of dust (the buy fee is paid in BTC)
        self.assertEqual((acct.s["sells"], acct.s["wins"]), (1, 1))
        legs = [f["reason"].split(":")[0] for f in self.store.recent_fills() if f["account"] == "SCALP"]
        self.assertEqual(legs, ["close long", "open long"])
        self.assertGreater(self.public.prices["BTCUSDT"] * 0.9998, entry * 1.003)
        self.assertEqual([t for t in log.trades if t[0] == "SCALP"], [])  # scalper fills never go to Telegram
        self.assertTrue(any(t[0].startswith("Hold") for t in log.trades))  # other accounts' fills still do
        self.assertIn("SCALP", {r["account"] for r in eng.snapshot()["leaderboard"]})


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

    def test_buy_takes_the_nearest_quantity_step(self):
        # 90% of a $7.50 sleeve is $6.75 = 0.0000799 BTC at 84,401. Rounding down would buy
        # 0.00007 ($5.91); the nearest step 0.00008 costs $6.752, still under the $7.50 cap.
        self.public.prices["BTCUSDT"] = 84_401.21
        self.stub.want["BTC"] = 0.9
        eng = self.engine()
        eng.tick()
        fill = [f for f in self.store.recent_fills() if f["account"] == "STUB_H1" and f["asset"] == "BTC"][0]
        self.assertAlmostEqual(fill["qty"], 0.00008)

    def test_nearest_step_never_breaks_the_per_asset_cap(self):
        # 100% of the sleeve is $7.50 = 0.0000889 BTC; 0.00009 would cost $7.60 > the $7.50 cap
        self.public.prices["BTCUSDT"] = 84_401.21
        eng = self.engine()
        eng.tick()
        fill = [f for f in self.store.recent_fills() if f["account"] == "STUB_H1" and f["asset"] == "BTC"][0]
        self.assertAlmostEqual(fill["qty"], 0.00008)
        self.assertLessEqual(fill["notional"], 7.5)

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
        self.assertEqual([r["pnl_pct"] for r in rows], sorted([r["pnl_pct"] for r in rows], reverse=True))
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

    def test_stream_sends_only_new_events_and_changed_fills(self):
        eng = self.engine()
        eng.tick()
        seen = {}
        first = stream_delta(eng.snapshot(), seen)
        self.assertEqual(len(first["feed"]), len(eng.snapshot()["feed"]))  # full history once
        self.assertIn("fills", first)
        self.clock.t += 1
        second = stream_delta(eng.snapshot(), seen)
        self.assertEqual(second["feed"], [])
        self.assertNotIn("fills", second)
        self.assertIn("leaderboard", second)  # balances still come every second
        eng.event("engine", "check", "something new")
        self.clock.t += 1
        third = stream_delta(eng.snapshot(), seen)
        self.assertEqual([e["text"] for e in third["feed"]], ["something new"])
        self.stub.want["BTC"] = 0.0
        self.minute(eng, 60)
        self.assertIn("fills", stream_delta(eng.snapshot(), seen))  # a new fill arrived
        # the saving is the whole activity feed (and fills table), which is not sent again
        saved = len(sse(first)) - len(sse(second))
        self.assertGreater(saved, len(json.dumps(first["feed"])))

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


class Formatting(unittest.TestCase):
    def test_prices_for_every_size_of_coin(self):
        from bot.fmt import level, px, qty_text
        self.assertEqual(px(84_253.614), "84,253.61")
        self.assertEqual(px(2.684123), "2.6841")
        self.assertEqual(px(0.00001234), "0.00001234")
        self.assertEqual(px(0.468289689), "0.46829")
        self.assertEqual(px(0.0000102940123), "0.000010294")
        self.assertEqual(level(78_296.4), "78,296")
        self.assertEqual(level(0.0000105), "0.0000105")
        self.assertEqual(qty_text(1_234_567.0), "1,234,567")
        self.assertEqual(qty_text(0.00008), "0.00008")
        self.assertEqual(qty_text(2.5), "2.5")


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
