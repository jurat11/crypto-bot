"""Backtest engine checks on synthetic hourly data (no network)."""
import json
import math
import os
import random
import unittest

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
        self.assertEqual(set(rep["strategies"]), {"TREND_D1", "TREND_H4", "BREAKOUT_H1", "MEANREV_H1"})
        self.assertGreater(rep["hold"]["oos"]["return"], 0)
        d1 = rep["strategies"]["TREND_D1"]
        self.assertGreater(d1["results"]["0.001"]["oos"]["return"], 0)
        for s in rep["strategies"].values():  # higher fees never help
            self.assertLessEqual(s["results"]["0.004"]["oos"]["return"], s["results"]["0.001"]["oos"]["return"])
        text = bt.table(rep)
        self.assertIn("| TREND_D1 | 0.1% |", text)
        self.assertIn("Hold 50/50 BTC/ETH", text)

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


if __name__ == "__main__":
    unittest.main()
