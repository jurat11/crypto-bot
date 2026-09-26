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


class Variants(unittest.TestCase):
    def setUp(self):
        self.all = {s.name: s for s in strategies.build(CFG)}

    def test_variants_from_config(self):
        self.assertTrue({"TREND_D1", "TREND_H4", "BREAKOUT_H1", "MEANREV_H1", "D1_ALL_IN", "D1_FAST",
                         "D1_HIGH_FEE", "ALTS_TREND", "MEME_TREND", "GOLD_TREND", "GOLD_HOLD"} <= set(self.all))
        self.assertEqual((self.all["TREND_D1"].start_cash, self.all["TREND_D1"].fee_rate), (15.0, 0.001))
        self.assertEqual((self.all["D1_HIGH_FEE"].start_cash, self.all["D1_HIGH_FEE"].fee_rate), (20.0, 0.004))
        self.assertEqual(self.all["D1_FAST"].base, "TREND_D1")
        self.assertEqual(set(CFG["engine"]["strategies"]), set(self.all))

    def test_all_in_skips_the_vol_dial(self):
        closes = [100 * (1.06 if i % 2 else 0.95) * (1.004 ** i) for i in range(120)]  # wild uptrend
        c = {"1d": bars(closes, step=24 * H)}
        self.assertLess(self.all["TREND_D1"].decide("BTC", c, {}, 0)[0], 1.0)
        self.assertEqual(self.all["D1_ALL_IN"].decide("BTC", c, {}, 0)[0], 1.0)

    def test_fast_uses_sma20(self):
        # 100 days flat at 100, then 25 days at 104: above SMA20 and SMA50, then back to 99 for 10 days
        closes = [100.0] * 100 + [104.0] * 25 + [99.0] * 10
        c = {"1d": bars(closes, step=24 * H)}
        exp50, info50 = self.all["TREND_D1"].decide("BTC", c, {}, 0)
        exp20, info20 = self.all["D1_FAST"].decide("BTC", c, {}, 0)
        self.assertAlmostEqual(info20["sma"], (104.0 * 10 + 99.0 * 10) / 20)
        self.assertEqual(exp20, 0.0)  # 99 is below the SMA20 (101.5)
        self.assertAlmostEqual(info50["sma"], (100.0 * 15 + 104.0 * 25 + 99.0 * 10) / 50)
        self.assertIn("SMA20", self.all["D1_FAST"].describe("BTC", info20, 99.0, {}))

    def test_other_coins_and_benchmarks(self):
        self.assertEqual(self.all["MEME_TREND"].sleeves, {"DOGE": 0.5, "PEPE": 0.5})
        self.assertEqual(self.all["MEME_TREND"].group, "More coins")
        self.assertEqual(self.all["TREND_D1"].sleeves, {"BTC": 0.5, "ETH": 0.5})
        self.assertTrue(self.all["GOLD_HOLD"].benchmark)
        self.assertFalse(self.all["GOLD_TREND"].benchmark)
        self.assertEqual(self.all["GOLD_TREND"].sleeves, {"PAXG": 0.5})  # the rest stays in cash
        for s in self.all.values():
            self.assertLessEqual(sum(s.sleeves.values()), 1.0)  # never more than the account (no leverage)

    def test_hold_benchmark_always_holds(self):
        h = strategies.Hold()
        self.assertEqual(h.decide("DOGE", {"1d": bars([0.1, 0.05])}, {}, 0)[0], 1.0)
        self.assertIn("holding since the start", h.describe("DOGE", {}, 0.12, {"exp": 1.0, "entry": 0.1}))

    def test_sleeves_over_100_percent_are_refused(self):
        cfg = dict(CFG, engine=dict(CFG["engine"], variants=[
            {"name": "X", "base": "TREND_D1", "sleeves": {"SOL": 0.7, "XRP": 0.7}}]))
        with self.assertRaises(ValueError):
            strategies.build(cfg)

    def test_memecoin_prices_are_readable(self):
        closes = [0.00001 * 1.002 ** i for i in range(120)]  # PEPE-sized prices, rising
        exp, info = self.all["MEME_TREND"].decide("PEPE", {"1d": bars(closes, step=24 * H)}, {}, 0)
        self.assertEqual(exp, 1.0)
        self.assertGreater(info["sma"], 0)  # not rounded away to 0.00
        text = self.all["MEME_TREND"].describe("PEPE", info, closes[-1], {"exp": 1.0})
        self.assertIn("0.0000", text)
        self.assertNotIn("(0)", text)

    def test_only_trend_d1_variants_can_override_settings(self):
        cfg = dict(CFG, engine=dict(CFG["engine"], variants=[{"name": "X", "base": "TREND_H4", "overrides": {"sma_days": 5}}]))
        with self.assertRaises(ValueError):
            strategies.build(cfg)


class LongShort(unittest.TestCase):
    s = strategies.LongShortD1()

    def test_decides_the_direction_itself(self):
        up = bars([100 * 1.001 ** i for i in range(80)], step=24 * H)
        down = bars([100 * 0.999 ** i for i in range(80)], step=24 * H)
        self.assertEqual(self.s.decide("BTC", {"1d": up}, {}, 0)[0], 1.0)
        exp, info = self.s.decide("BTC", {"1d": down}, {}, 0)
        self.assertEqual(exp, -1.0)
        self.assertEqual(info["direction"], "short")
        self.assertIn("SHORT (simulated)", self.s.describe("BTC", info, 90.0, {"exp": -1.0}))
        self.assertEqual(self.s.decide("BTC", {"1d": up[:10]}, {}, 0)[0], 0.0)  # not enough history: flat

    def test_config_accounts(self):
        all_ = {s.name: s for s in strategies.build(CFG)}
        ls = [s for s in all_.values() if s.allows_short]
        self.assertEqual({s.name for s in ls}, {"LS_BTC_ETH", "LS_ALTS", "LS_MEME", "LS_GOLD", "SCALP_BTC_ETH", "SCALP_MEME",
                                               "SCALP_1USD", "SCALP_1USD_MEME"})
        simulated = ("Long/short (simulated)", "Scalping (simulated)")
        self.assertFalse(any(s.allows_short for s in all_.values() if s.group not in simulated))
        self.assertTrue(all(sum(s.sleeves.values()) <= 1.0 for s in ls))  # 1x, no leverage


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


class Scalp(unittest.TestCase):
    s = strategies.Scalp()
    M = 60_000

    def minutes(self, last15):
        """60 flat one-minute closes at 100, then 15 more ending at `last15` (a straight line)."""
        ramp = [100 + (last15 - 100) * (i + 1) / 15 for i in range(15)]
        return {"1m": bars([100.0] * 46 + ramp, step=self.M)}

    def test_goes_with_a_move_of_the_last_15_minutes(self):
        exp, info = self.s.decide("BTC", self.minutes(100.2), {}, 0)
        self.assertEqual(exp, 1.0)  # up 0.2% and above the 1-hour average: long
        self.assertIn("open long", info["reason"])
        exp, info = self.s.decide("BTC", self.minutes(99.8), {}, 0)
        self.assertEqual(exp, -1.0)  # down 0.2%: simulated short
        self.assertIn("open short", info["reason"])
        self.assertEqual(self.s.decide("BTC", self.minutes(100.05), {}, 0)[0], 0.0)  # too small a move: wait

    def test_take_profit_and_stop_for_a_long(self):
        pos = {"exp": 1.0, "entry": 100.0, "entry_ms": 0}
        exp, info = self.s.decide("BTC", self.minutes(100.3), pos, 10 * self.M)
        self.assertEqual((exp, info["reason"]), (0.0, "close long: +0.3% take profit"))
        exp, info = self.s.decide("BTC", self.minutes(99.7), pos, 10 * self.M)
        self.assertEqual((exp, info["reason"]), (0.0, "close long: -0.3% stop"))
        self.assertEqual(self.s.decide("BTC", self.minutes(100.1), pos, 10 * self.M)[0], 1.0)  # keeps holding

    def test_a_short_wins_when_the_price_falls(self):
        pos = {"exp": -1.0, "entry": 100.0, "entry_ms": 0}
        exp, info = self.s.decide("BTC", self.minutes(99.7), pos, 10 * self.M)
        self.assertEqual((exp, info["reason"]), (0.0, "close short: +0.3% take profit"))
        exp, info = self.s.decide("BTC", self.minutes(100.3), pos, 10 * self.M)
        self.assertEqual((exp, info["reason"]), (0.0, "close short: -0.3% stop"))

    def test_closes_after_30_minutes(self):
        pos = {"exp": 1.0, "entry": 100.0, "entry_ms": 0}
        self.assertEqual(self.s.decide("BTC", self.minutes(100.1), pos, 29 * self.M)[0], 1.0)
        exp, info = self.s.decide("BTC", self.minutes(100.1), pos, 30 * self.M)
        self.assertEqual((exp, info["reason"]), (0.0, "close long: 30 minute time exit"))

    def test_describe(self):
        _, info = self.s.decide("BTC", self.minutes(99.9), {"exp": -1.0, "entry": 100.0, "entry_ms": 0}, self.M)
        text = self.s.describe("BTC", info, 99.9, {"exp": -1.0, "entry": 100.0, "entry_ms": 0})
        self.assertIn("SHORT (simulated)", text)
        self.assertIn("takes profit at 99.7000", text)
        self.assertIn("stops at 100.30", text)
        _, info = self.s.decide("BTC", self.minutes(100.05), {}, 0)
        self.assertIn("flat", self.s.describe("BTC", info, 100.05, {}))

    def test_on_fill_records_the_entry_of_a_short(self):
        pos = {}
        strategies.on_fill(pos, "SELL", 100.0, 5, -1.0)
        self.assertEqual((pos["entry"], pos["entry_ms"], pos["exp"]), (100.0, 5, -1.0))
        strategies.on_fill(pos, "BUY", 99.0, 9, 0.0)
        self.assertNotIn("entry", pos)
        self.assertEqual(pos["exp"], 0.0)

    def test_config_accounts(self):
        sc = {s.name: s for s in strategies.build(CFG) if s.base == "SCALP"}
        self.assertEqual(set(sc), {"SCALP_BTC_ETH", "SCALP_MEME", "SCALP_1USD", "SCALP_1USD_MEME"})
        self.assertTrue(all(s.timeframe == "1m" and s.allows_short and sum(s.sleeves.values()) <= 1.0 for s in sc.values()))
        one = sc["SCALP_1USD"]  # $20 account, $10 per coin: +$1 is a 10% move
        self.assertAlmostEqual(one.take, 0.10)
        self.assertAlmostEqual(one.stop, 0.10)
        self.assertIsNone(one.max_hold_ms)
        self.assertIn("close at +$1 (+10%) or -$1 (-10%) on the $10 trade", one.rule)
        self.assertEqual((sc["SCALP_BTC_ETH"].take, sc["SCALP_BTC_ETH"].max_hold_ms), (0.003, 30 * 60_000))


class ScalpOneDollar(unittest.TestCase):
    """The +$1 / -$1 variant: same entries as SCALP, exits at +-10% of a $10 trade, no time limit."""
    s = strategies.Scalp("SCALP_1USD", {"take_usd": 1.0, "stop_usd": 1.0, "max_hold_min": None}, trade_usd=10.0)
    M = 60_000

    def minutes(self, last):
        return {"1m": bars([100.0] * 60 + [last], step=self.M)}

    def test_holds_small_moves_and_closes_at_one_dollar(self):
        pos = {"exp": 1.0, "entry": 100.0, "entry_ms": 0}
        self.assertEqual(self.s.decide("BTC", self.minutes(100.3), pos, 10 * self.M)[0], 1.0)  # +0.3% is only +$0.03
        exp, info = self.s.decide("BTC", self.minutes(110.0), pos, 10 * self.M)
        self.assertEqual((exp, info["reason"]), (0.0, "close long: +$1 (+10%) take profit"))
        exp, info = self.s.decide("BTC", self.minutes(90.0), pos, 10 * self.M)
        self.assertEqual((exp, info["reason"]), (0.0, "close long: -$1 (-10%) stop"))

    def test_no_time_limit(self):
        pos = {"exp": -1.0, "entry": 100.0, "entry_ms": 0}
        self.assertEqual(self.s.decide("BTC", self.minutes(99.0), pos, 30 * 24 * 60 * self.M)[0], -1.0)  # a month later
        text = self.s.describe("BTC", self.s.decide("BTC", self.minutes(99.0), pos, self.M)[1], 99.0, pos)
        self.assertIn("takes profit at 90.0000, stops at 110.00 (no time limit)", text)

    def test_rejects_unknown_settings(self):
        with self.assertRaises(ValueError):
            strategies.Scalp("BAD", {"take_pct": 0.5}, trade_usd=10.0)
        with self.assertRaises(ValueError):
            strategies.Scalp("BAD", {"take_usd": 1.0})  # dollar exits need the trade size


if __name__ == "__main__":
    unittest.main()
