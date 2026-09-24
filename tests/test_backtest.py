"""Backtest engine checks on synthetic hourly data (no network)."""
import json
import math
import os
import random
import shutil
import tempfile
import time
import unittest
from unittest import mock

import backtest_strategies as bt
from bot import candles, strategies

CFG = json.load(open(os.path.join(os.path.dirname(__file__), "..", "config.json")))
H, D = candles.HOUR, 24 * candles.HOUR
T0 = 1_600_000_000_000 // D * D


def synthetic(days, drift, vol, seed):
    rnd = random.Random(seed)
    rows, p = [], 100.0
    for i in range(days * 24):
        o = p
        p *= math.exp(drift / (24 * 365) + vol * rnd.gauss(0, 1) / math.sqrt(24 * 365))
        rows.append((T0 + i * H, o, max(o, p) * 1.001, min(o, p) * 0.999, p, 1.0))
    return rows


class Sleeve(unittest.TestCase):
    def test_fee_and_mark_to_market_math(self):
        path = [(1, 100.0, 1.0), (2, 110.0, 0.0), (3, 50.0, 0.0)]
        curve, trades, trips = bt.sleeve(path, 0.001)
        self.assertAlmostEqual(curve[2], 0.999 * 1.1 * 0.999)
        self.assertAlmostEqual(curve[3], curve[2])  # in cash, the crash does not matter
        self.assertEqual(trades, [1, 2])
        self.assertEqual(trips, [(1, 2, True)])

    def test_partial_resize_costs_fee_on_the_traded_fraction(self):
        curve, trades, _ = bt.sleeve([(1, 100.0, 0.9), (2, 100.0, 1.0)], 0.004)
        self.assertAlmostEqual(curve[1], 1 - 0.004 * 0.9)
        self.assertAlmostEqual(curve[2], 1 - 0.004 * 1.0)
        self.assertEqual(len(trades), 2)

    def test_short_profits_when_price_falls(self):
        curve, trades, trips = bt.sleeve([(1, 100.0, -1.0), (2, 90.0, -1.0), (3, 90.0, 0.0)], 0.0)
        self.assertAlmostEqual(curve[2], 1.1)  # sold at 100, worth 90 to buy back
        self.assertAlmostEqual(curve[3], 1.1)
        self.assertEqual(trips, [(1, 3, True)])

    def test_short_loses_when_price_rises_and_pays_fees_both_ways(self):
        curve, _, trips = bt.sleeve([(1, 100.0, -1.0), (2, 120.0, 0.0)], 0.001)
        self.assertAlmostEqual(curve[2], 1 + 0.999 - 0.01 * 120 * 1.001)
        self.assertFalse(trips[0][2])

    def test_flip_closes_then_opens(self):
        curve, trades, trips = bt.sleeve([(1, 100.0, 1.0), (2, 110.0, -1.0), (3, 100.0, -1.0)], 0.0)
        self.assertEqual(trades, [1, 2, 2])  # buy, then at t=2 sell the long and open the short
        self.assertAlmostEqual(curve[2], 1.1)
        self.assertAlmostEqual(curve[3], 1.1 * (1 + (110 - 100) / 110))  # the short gained on the fall

    def test_borrow_cost_on_shorts(self):
        path = [(k, 100.0, -1.0) for k in range(24 * 365 + 1)]
        curve, _, _ = bt.sleeve(path, 0.0, borrow=0.10)
        self.assertAlmostEqual(curve[24 * 365], 1 - 0.10, places=3)  # a year of 10% interest on the short

    def test_quantity_is_held_between_trades(self):
        # 50% in the coin, price doubles: equity is 0.5 cash + 0.5 * 2, no hidden rebalancing
        curve, _, _ = bt.sleeve([(1, 100.0, 0.5), (2, 150.0, 0.5), (3, 200.0, 0.5)], 0.0)
        self.assertAlmostEqual(curve[3], 1.5)


class Backtest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.up = {"BTC": synthetic(420, 0.9, 0.35, 1), "ETH": synthetic(420, 0.9, 0.45, 2)}
        cls.down = {"BTC": synthetic(420, -1.2, 0.35, 3), "ETH": synthetic(420, -1.2, 0.45, 4)}
        cls.periods = {"is": (T0 + 100 * D, T0 + 260 * D), "oos": (T0 + 260 * D, T0 + 420 * D)}

    def test_daily_strategy_only_trades_at_daily_closes(self):
        path = bt.exposure_path(strategies.TrendD1(CFG), "BTC", self.up["BTC"])
        changes = [t for (t, _, e), (_, _, p) in zip(path[1:], path) if e != p]
        self.assertTrue(changes)
        self.assertTrue(all(t % D == 0 for t in changes))

    def test_4h_strategy_only_trades_at_4h_closes(self):
        path = bt.exposure_path(strategies.TrendH4(), "BTC", self.up["BTC"])
        changes = [t for (t, _, e), (_, _, p) in zip(path[1:], path) if e != p]
        self.assertTrue(changes)
        self.assertTrue(all(t % (4 * H) == 0 for t in changes))

    def test_report_rising_market(self):
        rep = bt.run(self.up, CFG, self.periods)
        on_btc_eth = {s.name for s in strategies.build(CFG) if set(s.sleeves) <= {"BTC", "ETH"}}
        self.assertEqual(set(rep["strategies"]), on_btc_eth)  # the others have no price data in this test
        self.assertIn("LS_BTC_ETH", on_btc_eth)
        self.assertGreater(rep["hold"]["oos"]["return"], 0)
        d1 = rep["strategies"]["TREND_D1"]
        self.assertGreater(d1["results"]["0.001"]["oos"]["return"], 0)
        for s in rep["strategies"].values():  # higher fees never help
            self.assertLessEqual(s["results"]["0.004"]["oos"]["return"], s["results"]["0.001"]["oos"]["return"])
        text = bt.table(rep)
        self.assertIn("| TREND_D1 | 0.1% |", text)
        self.assertIn("Hold 50/50 BTC/ETH", text)

    def test_day_week_year_ranges(self):
        rep = bt.run(self.up, CFG, self.periods)
        h = rep["strategies"]["TREND_D1"]["results"]["0.001"]["oos"]["horizons"]
        self.assertEqual(set(h), {"day", "week"})  # the 160-day test period has no full year
        for x in h.values():
            self.assertLessEqual(x["p10"], x["median"])
            self.assertLessEqual(x["median"], x["p90"])
            self.assertTrue(0 <= x["loss_share"] <= 1)
        self.assertEqual(h["day"]["windows"], 159)
        self.assertIn("horizons", rep["hold"]["oos"])
        self.assertEqual(rep["strategies"]["D1_HIGH_FEE"]["demo_fee"], "0.004")

    def test_horizon_math(self):
        series = [(k * D, 1.0 + 0.01 * k) for k in range(10)]  # +1% of the start every day
        h = bt.horizons(series)
        self.assertEqual(h["day"]["loss_share"], 0.0)
        self.assertEqual(h["week"]["windows"], 3)  # days 0->7, 1->8, 2->9
        self.assertAlmostEqual(h["week"]["p10"], 1.09 / 1.02 - 1)  # the weakest week
        self.assertAlmostEqual(h["week"]["median"], 1.08 / 1.01 - 1)
        self.assertAlmostEqual(h["week"]["p90"], 1.07 / 1.00 - 1)  # the best week

    def test_losing_strategy_is_flagged(self):
        rep = bt.run(self.down, CFG, self.periods)
        self.assertLess(rep["hold"]["oos"]["return"], 0)
        for s in rep["strategies"].values():
            lost = any(r["oos"]["return"] <= 0 for r in s["results"].values())
            self.assertEqual(s["verdict"], "FAILED BACKTEST" if lost else "PASSED")

    def test_verdict_rule(self):
        mk = lambda a, b: {"0.001": {"oos": {"return": a}}, "0.004": {"oos": {"return": b}}}
        self.assertEqual(bt.verdict(mk(0.2, 0.1))[0], "PASSED")
        self.assertEqual(bt.verdict(mk(0.2, -0.1)), ("FAILED BACKTEST", "lost money out of sample at 0.4% fee"))
        self.assertEqual(bt.verdict(mk(-0.2, -0.3))[0], "FAILED BACKTEST")


class OtherCoins(unittest.TestCase):
    def test_partial_sleeve_keeps_the_rest_in_cash(self):
        curve = {k * D: 1.0 + k for k in range(3)}  # the coin triples
        series = bt.combine({"PAXG": curve}, {"PAXG": 0.5}, 0, 10 * D)
        self.assertAlmostEqual(series[-1][1], 0.5 + 0.5 * 3.0)

    def test_coin_listed_after_the_in_sample_period(self):
        periods = {"is": (T0 + 100 * D, T0 + 260 * D), "oos": (T0 + 260 * D, T0 + 420 * D)}
        full = synthetic(420, 0.9, 0.35, 1)
        new_coin = [r for r in synthetic(420, 0.9, 0.9, 5) if r[0] >= T0 + 200 * D]  # listed on day 200
        cfg = dict(CFG, engine=dict(CFG["engine"], variants=[
            {"name": "NEW_TREND", "base": "TREND_D1", "sleeves": {"NEW": 0.5}, "overrides": {"vol_target": 0}},
            {"name": "NEW_HOLD", "base": "HOLD", "sleeves": {"NEW": 0.5}}]))
        rep = bt.run({"BTC": full, "ETH": full, "NEW": new_coin}, cfg, periods)
        res = rep["strategies"]["NEW_TREND"]["results"]["0.001"]
        self.assertIsNotNone(res["oos"]["return"])
        self.assertEqual(rep["strategies"]["NEW_TREND"]["coins"], ["NEW"])
        self.assertIn(rep["strategies"]["NEW_TREND"]["verdict"], ("PASSED", "FAILED BACKTEST"))
        self.assertEqual(rep["strategies"]["NEW_HOLD"]["verdict"], "BENCHMARK")
        self.assertIn("| NEW_TREND (NEW) | 0.1% |", bt.table(rep))

    def test_no_in_sample_data_at_all(self):
        periods = {"is": (T0, T0 + 100 * D), "oos": (T0 + 260 * D, T0 + 420 * D)}
        late = [r for r in synthetic(420, 0.9, 0.9, 6) if r[0] >= T0 + 250 * D]
        full = synthetic(420, 0.9, 0.35, 1)
        cfg = dict(CFG, engine=dict(CFG["engine"], variants=[
            {"name": "LATE", "base": "TREND_D1", "sleeves": {"LATE": 0.5}, "overrides": {"vol_target": 0}}]))
        rep = bt.run({"BTC": full, "ETH": full, "LATE": late}, cfg, periods)
        r = rep["strategies"]["LATE"]["results"]["0.001"]
        self.assertIsNone(r["is"]["cagr"])
        self.assertIn("n/a", bt.table(rep))

    def test_strategies_without_price_history_are_left_out(self):
        full = synthetic(420, 0.9, 0.35, 1)
        periods = {"is": (T0 + 100 * D, T0 + 260 * D), "oos": (T0 + 260 * D, T0 + 420 * D)}
        rep = bt.run({"BTC": full, "ETH": full}, CFG, periods)
        self.assertNotIn("MEME_TREND", rep["strategies"])
        self.assertIn("TREND_D1", rep["strategies"])


class Cache(unittest.TestCase):
    def test_forming_candle_is_never_cached(self):
        now = int(time.time() * 1000)
        last_open = now // H * H  # this hour is still forming
        rows = [(t, 1.0, 1.0, 1.0, 1.0, 1.0, t + H - 1) for t in range(last_open - 5 * H, last_open + H, H)]
        tmp, cwd = tempfile.mkdtemp(), os.getcwd()
        os.chdir(tmp)
        try:
            with mock.patch("backtest_strategies.BinanceSpot") as api:
                api.return_value.klines.return_value = rows
                got = bt.hourly("BTCUSDT")
            self.assertEqual(got[-1][0], last_open - H)
            self.assertEqual(bt.load_cache("BTCUSDT")[-1][0], last_open - H)
        finally:
            os.chdir(cwd)
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
