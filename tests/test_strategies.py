"""Each strategy's signal on hand-made candles (python3 -m unittest discover tests)."""
import json
import os
import unittest

from bot import candles, strategies, strategy

CFG = json.load(open(os.path.join(os.path.dirname(__file__), "..", "config.json")))
H = candles.HOUR
T0 = 1_750_000_000_000 // (24 * H) * (24 * H)  # a UTC midnight


def bars(closes, step=H, start=T0, highs=None, lows=None):
    """Flat-wick candles unless highs/lows are given."""
    out = []
    for i, c in enumerate(closes):
        hi = highs[i] if highs else c
        lo = lows[i] if lows else c
        out.append((start + i * step, c, hi, lo, c, 1.0))
    return out


def daily(level, n=60):
    return bars([level] * n, step=24 * H, start=T0 - n * 24 * H)


def now_after(rows, step=H):
    return rows[-1][0] + step


class Indicators(unittest.TestCase):
    def test_rsi2(self):
        self.assertEqual(strategies.rsi([1, 2, 3, 4, 5]), 100.0)
        self.assertEqual(strategies.rsi([5, 4, 3, 2, 1]), 0.0)
        # Wilder: seed ag=0.5, al=0 from +1,0; then -2 -> ag=0.25, al=1.0 -> RSI 20
        self.assertAlmostEqual(strategies.rsi([10, 11, 11, 9]), 20.0)
        self.assertIsNone(strategies.rsi([1, 2]))

    def test_resample_1h_to_4h(self):
        rows = bars([1, 2, 3, 4, 5, 6, 7, 8], highs=[1, 9, 3, 4, 5, 6, 7, 8], lows=[1, 2, 0.5, 4, 5, 6, 7, 8])
        four = candles.resample(rows, "4h")
        self.assertEqual(len(four), 2)
        self.assertEqual(four[0], (T0, 1, 9, 0.5, 4, 4.0))
        self.assertEqual(four[1][4], 8)

    def test_closed_candles_only(self):
        rows = bars([1, 2, 3])
        self.assertEqual(len(candles.closed(rows, "1h", T0 + 2 * H + 59_000)), 2)
        self.assertEqual(len(candles.closed(rows, "1h", T0 + 3 * H)), 3)


class TrendD1(unittest.TestCase):
    def test_is_the_unchanged_baseline(self):
        s = strategies.TrendD1(CFG)
        for closes in ([100 * 1.001 ** i for i in range(120)], [100 - i * 0.5 for i in range(120)],
                       [100 * (1.06 if i % 2 else 0.95) * (1.004 ** i) for i in range(120)]):
            exp, info = s.decide("BTC", {"1d": bars(closes, step=24 * H)}, {}, 0)
            base_exp, base_info = strategy.target_exposure(closes, 50, 0.4, 30, 0.1)
            self.assertEqual(exp, base_exp)
            self.assertEqual(info["reason"], base_info["reason"])


class TrendH4(unittest.TestCase):
    s = strategies.TrendH4()

    def test_calm_uptrend_full_size(self):
        exp, info = self.s.decide("BTC", {"4h": bars([100 * 1.0005 ** i for i in range(320)], step=4 * H)}, {}, 0)
        self.assertEqual(exp, 1.0)
        self.assertTrue(info["above_sma"])

    def test_downtrend_is_cash(self):
        exp, info = self.s.decide("BTC", {"4h": bars([100 - i * 0.1 for i in range(320)], step=4 * H)}, {}, 0)
        self.assertEqual(exp, 0.0)
        self.assertIn("below SMA300", info["reason"])

    def test_wild_uptrend_is_cut_by_vol_dial(self):
        closes = [100 * (1.04 if i % 2 else 0.97) * 1.001 ** i for i in range(320)]
        exp, info = self.s.decide("BTC", {"4h": bars(closes, step=4 * H)}, {}, 0)
        self.assertTrue(info["above_sma"])
        self.assertLess(exp, 1.0)
        self.assertGreater(exp, 0.0)

    def test_needs_300_bars(self):
        self.assertEqual(self.s.decide("BTC", {"4h": bars([100 + i for i in range(299)], step=4 * H)}, {}, 0)[0], 0.0)


class BreakoutH1(unittest.TestCase):
    s = strategies.BreakoutH1()

    def run_(self, closes, sma_level=100, pos=None, highs=None, lows=None):
        h = bars(closes, highs=highs, lows=lows)
        pos = {} if pos is None else pos
        exp, info = self.s.decide("BTC", {"1h": h, "1d": daily(sma_level)}, pos, now_after(h))
        return exp, info, pos

    def test_buys_break_of_48h_high_above_sma(self):
        exp, info, _ = self.run_([105.0] * 48 + [106.0])
        self.assertEqual(exp, 1.0)
        self.assertEqual(info["high48"], 105.0)
        self.assertTrue(info["reason"].startswith("buy"))

    def test_no_buy_without_break(self):
        exp, info, _ = self.run_([105.0] * 48 + [104.0])
        self.assertEqual(exp, 0.0)
        self.assertEqual(info["reason"], "waiting: below the 48h high")

    def test_high_older_than_48h_does_not_count(self):
        exp, _, _ = self.run_([200.0] + [105.0] * 48 + [106.0])
        self.assertEqual(exp, 1.0)

    def test_no_buy_below_daily_sma50(self):
        exp, info, _ = self.run_([105.0] * 48 + [106.0], sma_level=150)
        self.assertEqual(exp, 0.0)
        self.assertIn("below the daily SMA50", info["reason"])

    def test_exit_on_24h_low(self):
        pos = {"in": True, "entry": 105.0, "entry_ms": T0, "peak": 105.0}
        exp, info, _ = self.run_([105.0] * 48 + [104.0] * 24 + [103.9], pos=pos)
        self.assertEqual(exp, 0.0)
        self.assertIn("24h low", info["reason"])

    def test_exit_on_3pct_trailing_stop(self):
        pos = {"in": True, "entry": 100.0, "entry_ms": T0, "peak": 110.0}
        exp, info, _ = self.run_([106.0] * 49 + [106.6], pos=pos)  # 110 * 0.97 = 106.7
        self.assertEqual(exp, 0.0)
        self.assertIn("trailing stop", info["reason"])

    def test_holds_and_raises_trailing_peak(self):
        pos = {"in": True, "entry": 100.0, "entry_ms": T0, "peak": 110.0}
        exp, info, pos = self.run_([108.0] * 49 + [112.0], pos=pos)
        self.assertEqual(exp, 1.0)
        self.assertEqual(pos["peak"], 112.0)
        self.assertAlmostEqual(info["stop"], 112.0 * 0.97)

    def test_plain_english_state(self):
        _, info, _ = self.run_([105.0] * 48 + [104.0])
        text = self.s.describe("BTC", info, 103.11, {})
        self.assertIn("BTC is 1.8% below the 48h high", text)


class MeanRevH1(unittest.TestCase):
    s = strategies.MeanRevH1()
    UP = [100 + i * 0.1 for i in range(48)]  # slow drift up, ends at 104.7

    def run_(self, closes, sma_level=90, pos=None, now=None):
        h = bars(closes)
        pos = {} if pos is None else pos
        exp, info = self.s.decide("ETH", {"1h": h, "1d": daily(sma_level)}, pos, now or now_after(h))
        return exp, info, pos

    def test_buys_when_rsi2_below_5_above_sma(self):
        exp, info, _ = self.run_(self.UP + [102.7, 100.7])
        self.assertLess(info["rsi2"], 5)
        self.assertEqual(exp, 1.0)

    def test_no_buy_below_daily_sma50(self):
        exp, info, _ = self.run_(self.UP + [102.7, 100.7], sma_level=120)
        self.assertEqual(exp, 0.0)
        self.assertIn("below the daily SMA50", info["reason"])

    def test_no_buy_when_rsi_not_oversold(self):
        exp, info, _ = self.run_(self.UP + [104.5])
        self.assertGreaterEqual(info["rsi2"], 5)
        self.assertEqual(exp, 0.0)

    def test_sells_when_rsi2_above_70(self):
        pos = {"in": True, "entry": 100.0, "entry_ms": T0 + 46 * H}
        exp, info, _ = self.run_(self.UP[:46] + [100.0, 102.0, 104.5], pos=pos)
        self.assertGreater(info["rsi2"], 70)
        self.assertEqual(exp, 0.0)
        self.assertIn("RSI(2) above 70", info["reason"])

    def test_sells_after_12_hours(self):
        closes = [100.0, 100.5] * 25  # choppy, RSI(2) stays in the middle
        entry_ms = T0 + 38 * H
        pos = {"in": True, "entry": 100.0, "entry_ms": entry_ms}
        exp, info, _ = self.run_(closes, pos=pos, now=entry_ms + 11 * H)
        self.assertEqual(exp, 1.0)
        exp, info, _ = self.run_(closes, pos=pos, now=entry_ms + 12 * H)
        self.assertEqual(exp, 0.0)
        self.assertIn("12 hour", info["reason"])

    def test_3pct_stop(self):
        pos = {"in": True, "entry": 104.0, "entry_ms": T0 + 47 * H}
        exp, info, _ = self.run_(self.UP + [100.8], pos=pos)  # 104 * 0.97 = 100.88
        self.assertEqual(exp, 0.0)
        self.assertIn("3% stop", info["reason"])

    def test_fill_bookkeeping(self):
        pos = {}
        strategies.on_fill(pos, "BUY", 101.0, T0, 1.0)
        self.assertEqual((pos["in"], pos["entry"], pos["entry_ms"], pos["exp"]), (True, 101.0, T0, 1.0))
        strategies.on_fill(pos, "SELL", 103.0, T0 + H, 0.0)
        self.assertEqual(pos, {"in": False, "exp": 0.0})


if __name__ == "__main__":
    unittest.main()
