"""Offline tests: python3 -m unittest discover tests"""
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from bot import broker, risk, strategy

CFG = json.load(open(os.path.join(os.path.dirname(__file__), "..", "config.json")))


class Strategy(unittest.TestCase):
    def test_below_sma_is_cash(self):
        closes = [100 - i * 0.5 for i in range(120)]  # falling
        exp, info = strategy.target_exposure(closes, 50, 0.4, 30, 0.1)
        self.assertEqual(exp, 0.0)
        self.assertFalse(info["above_sma"])

    def test_above_sma_calm_market_full_size(self):
        closes = [100 * 1.001 ** i for i in range(120)]  # smooth uptrend, tiny vol
        exp, _ = strategy.target_exposure(closes, 50, 0.4, 30, 0.1)
        self.assertEqual(exp, 1.0)

    def test_vol_dial_cuts_size_in_wild_market(self):
        closes = [100 * (1.06 if i % 2 else 0.95) ** 1 * (1.004 ** i) for i in range(120)]
        exp, info = strategy.target_exposure(closes, 50, 0.4, 30, 0.1)
        self.assertTrue(info["above_sma"])
        self.assertLess(exp, 1.0)

    def test_not_enough_history(self):
        self.assertEqual(strategy.target_exposure([1, 2, 3], 50)[0], 0.0)


class PaperTrading(unittest.TestCase):
    def test_round_trip_costs_fees(self):
        st = {"cash": 20.0, "holdings": {}}
        pb = broker.PaperBroker(0.004, 0.001)
        pb.execute(st, {"asset": "BTC", "side": "buy", "usd": 10}, 50_000)
        eq_mid = broker.equity(st, {"BTC": 50_000})
        pb.execute(st, {"asset": "BTC", "side": "sell", "usd": 1e9}, 50_000)
        self.assertLess(eq_mid, 20.0)
        self.assertLess(st["cash"], eq_mid)
        self.assertAlmostEqual(st["holdings"]["BTC"], 0.0, places=12)

    def test_plan_sells_first_and_skips_dust(self):
        st = {"cash": 0.0, "holdings": {"BTC": 0.0002, "ETH": 0.0}}
        orders = broker.plan_orders(st, {"BTC": 50_000, "ETH": 2_000}, {"BTC": 0.0, "ETH": 0.5}, 1.0)
        self.assertEqual(orders[0]["side"], "sell")
        self.assertEqual(broker.plan_orders(st, {"BTC": 50_000, "ETH": 2_000}, {"BTC": 0.95, "ETH": 0.05}, 1.0), [])


class Risk(unittest.TestCase):
    def test_blocks_on_stale_data_drawdown_and_daily_loss(self):
        state = {"peak_equity": 100, "last_equity": 100}
        problems, dd = risk.check(CFG, state, 60, 48, [])
        text = " ".join(problems)
        self.assertIn("old", text)
        self.assertIn("drawdown", text)
        self.assertIn("since last run", text)

    def test_clean_run_passes(self):
        problems, _ = risk.check(CFG, {"peak_equity": 20, "last_equity": 20}, 20, 2, [])
        self.assertEqual(problems, [])

    def test_weight_cap(self):
        self.assertEqual(risk.clamp_targets({"BTC": 0.9}, CFG)["BTC"], CFG["risk"]["max_weight_per_asset"])


class EndToEnd(unittest.TestCase):
    def test_run_twice_with_fake_data(self):
        import time
        import run
        now = int(time.time() * 1000)
        day = 86_400_000

        def fake(asset, days=400, history=False):
            base = 60_000 if asset == "BTC" else 3_000
            start = (now // day - 150) * day
            rows = [(start + i * day, 0, 0, 0, base * 1.002 ** i, 0) for i in range(151)]
            return rows, "fake"

        tmp = tempfile.mkdtemp()
        shutil.copy(os.path.join(os.path.dirname(__file__), "..", "config.json"), tmp)
        cwd = os.getcwd()
        os.chdir(tmp)
        try:
            with mock.patch("bot.data.daily_candles", side_effect=fake):
                run.main()
                run.main()
            self.assertTrue(os.path.exists("MEMORY.md"))
            self.assertTrue(os.path.exists("logs/ledger.csv"))
            st = json.load(open("data/state.json"))
            self.assertEqual(st["runs"], 2)
            self.assertGreater(st["holdings"]["BTC"], 0)
        finally:
            os.chdir(cwd)
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
