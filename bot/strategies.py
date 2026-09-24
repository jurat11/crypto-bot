"""The four demo strategies. All spot, long or cash, on a 50% BTC and 50% ETH sleeve.

Every strategy decides per asset on CLOSED candles of its own timeframe and
returns an exposure between 0 (cash) and 1 (the whole sleeve in the coin).
The same decide() code runs in backtest_strategies.py and in the live engine.

  TREND_D1     bot/strategy.py unchanged: daily close > SMA50, 40% vol dial.
  TREND_H4     the same idea on 4h candles: close > SMA300 (50 days x 6 bars),
               vol dial on 180 bars (30 days), decided at each 4h close.
  BREAKOUT_H1  buy when the 1h close breaks the prior 48h high while price is
               above the daily SMA50; exit on a close below the prior 24h low
               or a 3% trailing stop (from the highest close since entry).
  MEANREV_H1   buy when RSI(2) on 1h < 5 while price is above the daily SMA50;
               sell when RSI(2) > 70, after 12 hours, or on a 3% stop.

`pos` is the per-asset position memory kept by the caller:
  {"in": bool, "entry": price, "entry_ms": candle close ms, "peak": price, "exp": applied exposure}
Call on_fill() after an entry or exit actually fills.

No leverage, margin, averaging down, martingale or grid logic, ever.
"""
import math
import time

from . import strategy as trend_d1
from .candles import HOUR

SLEEVES = {"BTC": 0.5, "ETH": 0.5}


def sma(values, n):
    return trend_d1.sma(values, n)


def realized_vol(closes, lookback, periods_per_year):
    if len(closes) < lookback + 1:
        return None
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(len(closes) - lookback, len(closes))]
    mu = sum(rets) / len(rets)
    var = sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var * periods_per_year)


def rsi(closes, n=2):
    """Wilder's RSI over the whole window (n=2 forgets the seed within a few bars)."""
    if len(closes) < n + 1:
        return None
    gains = losses = 0.0
    for i in range(1, n + 1):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    ag, al = gains / n, losses / n
    for i in range(n + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        ag = (ag * (n - 1) + max(d, 0.0)) / n
        al = (al * (n - 1) + max(-d, 0.0)) / n
    if al == 0:
        return 100.0 if ag > 0 else 50.0
    return 100.0 - 100.0 / (1.0 + ag / al)


def on_fill(pos, side, price, decision_ms, exposure):
    """Update position memory after an order for this sleeve filled."""
    pos["exp"] = exposure
    if side == "BUY" and not pos.get("in"):
        pos.update({"in": True, "entry": price, "entry_ms": decision_ms, "peak": price})
    elif side == "SELL" and exposure == 0:
        for k in ("entry", "entry_ms", "peak"):
            pos.pop(k, None)
        pos["in"] = False


def _pct(price, level):
    return price / level - 1 if level else 0.0


def _hhmm(ms):
    return time.strftime("%H:%M UTC", time.gmtime(ms / 1000))


class Strategy:
    name = ""
    timeframe = ""
    needs = {}
    rule = ""

    def decide(self, asset, candles, pos, now_ms):
        raise NotImplementedError

    def describe(self, asset, info, price, pos):
        raise NotImplementedError


class TrendD1(Strategy):
    name, timeframe = "TREND_D1", "1d"
    rule = "Daily close above SMA50 = hold, sized by a 40% volatility dial; below = cash."

    def __init__(self, cfg):
        self.cfg = cfg
        self.needs = {"1d": max(cfg["sma_days"], cfg["vol_lookback_days"] + 1) + 5}

    def decide(self, asset, candles, pos, now_ms):
        closes = [r[4] for r in candles["1d"]]
        c = self.cfg
        exp, info = trend_d1.target_exposure(closes, c["sma_days"], c["vol_target"],
                                             c["vol_lookback_days"], c["rebalance_step"])
        info["exposure"] = exp
        return exp, info

    def describe(self, asset, info, price, pos):
        if "sma" not in info:
            return f"{asset}: {info.get('reason', 'waiting for data')}"
        d = _pct(price, info["sma"])
        if pos.get("exp", 0) > 0:
            return (f"{asset}: holding {pos['exp']:.0%} of its sleeve, price is {d:+.1%} vs the SMA50 "
                    f"({info['sma']:,.0f}); sells if a daily close ends below it")
        return (f"{asset}: in cash, price is {d:+.1%} vs the SMA50 ({info['sma']:,.0f}); "
                f"buys if a daily close ends above it")


class TrendH4(Strategy):
    name, timeframe = "TREND_H4", "4h"
    rule = "4h close above SMA300 (50 days of 4h bars) = hold, 40% volatility dial; below = cash."
    SMA_BARS, VOL_BARS, BARS_PER_YEAR, VOL_TARGET, STEP = 300, 180, 6 * 365, 0.4, 0.1
    needs = {"4h": 305}

    def decide(self, asset, candles, pos, now_ms):
        closes = [r[4] for r in candles["4h"]]
        avg = sma(closes, self.SMA_BARS)
        if avg is None:
            return 0.0, {"reason": "not enough history", "exposure": 0.0}
        last = closes[-1]
        info = {"close": round(last, 2), "sma": round(avg, 2), "above_sma": last > avg}
        if last <= avg:
            info.update(reason="below SMA300: stay in cash", exposure=0.0)
            return 0.0, info
        exp = 1.0
        vol = realized_vol(closes, self.VOL_BARS, self.BARS_PER_YEAR)
        info["vol"] = round(vol, 3) if vol else None
        if vol:
            exp = min(1.0, self.VOL_TARGET / vol)
        exp = round(round(exp / self.STEP) * self.STEP, 4)
        info.update(reason=f"above SMA300: exposure {exp:.0%}", exposure=exp)
        return exp, info

    def describe(self, asset, info, price, pos):
        if "sma" not in info:
            return f"{asset}: {info.get('reason', 'waiting for data')}"
        d = _pct(price, info["sma"])
        if pos.get("exp", 0) > 0:
            return (f"{asset}: holding {pos['exp']:.0%} of its sleeve, price is {d:+.1%} vs the 4h SMA300 "
                    f"({info['sma']:,.0f}); sells if a 4h close ends below it")
        return (f"{asset}: in cash, price is {d:+.1%} vs the 4h SMA300 ({info['sma']:,.0f}); "
                f"buys if a 4h close ends above it")


class BreakoutH1(Strategy):
    name, timeframe = "BREAKOUT_H1", "1h"
    rule = "Buy a 1h close above the prior 48h high while above the daily SMA50; exit below the 24h low or on a 3% trailing stop."
    HIGH_BARS, LOW_BARS, TRAIL = 48, 24, 0.03
    needs = {"1h": 49, "1d": 50}

    def decide(self, asset, candles, pos, now_ms):
        h, d = candles["1h"], candles["1d"]
        if len(h) < self.HIGH_BARS + 1 or len(d) < 50:
            return (1.0 if pos.get("in") else 0.0), {"reason": "not enough history"}
        close = h[-1][4]
        high48 = max(r[2] for r in h[-self.HIGH_BARS - 1:-1])
        low24 = min(r[3] for r in h[-self.LOW_BARS - 1:-1])
        sma50 = sma([r[4] for r in d], 50)
        info = {"close": close, "high48": high48, "low24": low24, "sma50": round(sma50, 2)}
        if pos.get("in"):
            peak = max(pos.get("peak") or pos.get("entry") or close, close)
            pos["peak"] = peak
            stop = peak * (1 - self.TRAIL)
            info.update(peak=peak, stop=round(stop, 2))
            if close < low24:
                info["reason"] = "exit: closed below the prior 24h low"
                return 0.0, info
            if close <= stop:
                info["reason"] = "exit: 3% trailing stop"
                return 0.0, info
            info["reason"] = "holding: no exit signal"
            return 1.0, info
        if close > high48 and close > sma50:
            info["reason"] = "buy: 1h close broke the prior 48h high above the daily SMA50"
            return 1.0, info
        info["reason"] = ("waiting: below the 48h high" if close <= high48
                          else "waiting: broke the 48h high but price is below the daily SMA50")
        return 0.0, info

    def describe(self, asset, info, price, pos):
        if "high48" not in info:
            return f"{asset}: {info.get('reason', 'waiting for data')}"
        if pos.get("in"):
            peak = max(pos.get("peak") or price, price)
            stop = peak * (1 - self.TRAIL)
            exit_at = max(stop, info["low24"])
            return (f"{asset}: holding since {_hhmm(pos.get('entry_ms', 0))}, exits on an hourly close below "
                    f"{exit_at:,.0f} ({_pct(price, exit_at):+.1%} away; trailing stop {stop:,.0f}, "
                    f"24h low {info['low24']:,.0f})")
        below_sma = price <= info["sma50"]
        if price <= info["high48"]:
            s = f"{asset}: waiting, {asset} is {-_pct(price, info['high48']):.1%} below the 48h high ({info['high48']:,.0f})"
        else:
            s = f"{asset}: waiting for the hourly close, price is above the 48h high ({info['high48']:,.0f})"
        if below_sma:
            s += f"; also below the daily SMA50 ({info['sma50']:,.0f}), so no buys"
        return s


class MeanRevH1(Strategy):
    name, timeframe = "MEANREV_H1", "1h"
    rule = "Buy when RSI(2) on 1h < 5 while above the daily SMA50; sell at RSI(2) > 70, after 12 hours, or on a 3% stop."
    BUY_BELOW, SELL_ABOVE, MAX_HOLD_MS, STOP = 5.0, 70.0, 12 * HOUR, 0.03
    needs = {"1h": 50, "1d": 50}

    def decide(self, asset, candles, pos, now_ms):
        h, d = candles["1h"], candles["1d"]
        if len(h) < 10 or len(d) < 50:
            return (1.0 if pos.get("in") else 0.0), {"reason": "not enough history"}
        closes = [r[4] for r in h[-self.needs["1h"]:]]
        close, r2 = closes[-1], rsi(closes, 2)
        sma50 = sma([r[4] for r in d], 50)
        info = {"close": close, "rsi2": round(r2, 2), "sma50": round(sma50, 2)}
        if pos.get("in"):
            stop = pos["entry"] * (1 - self.STOP)
            info.update(stop=round(stop, 2), held_h=round((now_ms - pos["entry_ms"]) / HOUR, 2))
            if r2 > self.SELL_ABOVE:
                info["reason"] = "sell: RSI(2) above 70"
                return 0.0, info
            if now_ms - pos["entry_ms"] >= self.MAX_HOLD_MS:
                info["reason"] = "sell: 12 hour time exit"
                return 0.0, info
            if close <= stop:
                info["reason"] = "sell: 3% stop"
                return 0.0, info
            info["reason"] = "holding: no exit signal"
            return 1.0, info
        if r2 < self.BUY_BELOW and close > sma50:
            info["reason"] = "buy: RSI(2) below 5 above the daily SMA50"
            return 1.0, info
        info["reason"] = ("waiting: RSI(2) not below 5" if r2 >= self.BUY_BELOW
                          else "waiting: RSI(2) below 5 but price is below the daily SMA50")
        return 0.0, info

    def describe(self, asset, info, price, pos):
        if "rsi2" not in info:
            return f"{asset}: {info.get('reason', 'waiting for data')}"
        if pos.get("in"):
            stop = pos["entry"] * (1 - self.STOP)
            return (f"{asset}: holding since {_hhmm(pos['entry_ms'])}; sells when RSI(2) > 70 (last {info['rsi2']:.0f}), "
                    f"at {_hhmm(pos['entry_ms'] + self.MAX_HOLD_MS)}, or on a close below {stop:,.0f} "
                    f"({_pct(price, stop):+.1%} away)")
        s = f"{asset}: waiting, RSI(2) on the last hourly close is {info['rsi2']:.0f} (buys below 5)"
        if price <= info["sma50"]:
            s += f"; price is below the daily SMA50 ({info['sma50']:,.0f}), so no buys"
        return s


def build(cfg):
    return [TrendD1(cfg), TrendH4(), BreakoutH1(), MeanRevH1()]
