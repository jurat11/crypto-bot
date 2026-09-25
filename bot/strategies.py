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
  SCALP        fast long/short trades on 1-minute candles (demo only, simulated
               shorts): go with a 0.1%+ move of the last 15 minutes, close at
               +0.3%, -0.3% or after 30 minutes, then look for the next one.

`pos` is the per-asset position memory kept by the caller:
  {"in": bool, "entry": price, "entry_ms": candle close ms, "peak": price, "exp": applied exposure}
Call on_fill() after an entry or exit actually fills.

No leverage, margin, averaging down, martingale or grid logic, ever.
"""
import math
import time

from . import strategy as trend_d1
from .candles import HOUR
from .fmt import level

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
    """Update position memory after an order for this sleeve filled. A new position (long, or a
    simulated short) records its entry; resizing keeps it; going to cash clears it."""
    prev = pos.get("exp", 0.0)
    pos["exp"] = exposure
    if exposure == 0:
        for k in ("entry", "entry_ms", "peak"):
            pos.pop(k, None)
        pos["in"] = False
    elif not pos.get("in") or (prev > 0) != (exposure > 0):
        pos.update({"in": True, "entry": price, "entry_ms": decision_ms, "peak": price})


def _pct(price, level):
    return price / level - 1 if level else 0.0


def _hhmm(ms):
    return time.strftime("%H:%M UTC", time.gmtime(ms / 1000))


class Strategy:
    name = ""
    timeframe = ""
    needs = {}
    rule = ""
    sleeves = SLEEVES  # coin -> share of the account; the rest stays in cash
    group = "BTC & ETH"
    benchmark = False
    allows_short = False  # True: exposure can be negative (simulated short, demo only)

    def decide(self, asset, candles, pos, now_ms):
        raise NotImplementedError

    def describe(self, asset, info, price, pos):
        raise NotImplementedError


class TrendD1(Strategy):
    name, timeframe = "TREND_D1", "1d"

    def __init__(self, cfg, name="TREND_D1", overrides=None):
        self.cfg = dict(cfg, **(overrides or {}))
        self.name = name
        c = self.cfg
        self.needs = {"1d": max(c["sma_days"], c["vol_lookback_days"] + 1) + 5}
        dial = f"sized by a {c['vol_target']:.0%} volatility dial" if c["vol_target"] else "always the full sleeve (no volatility dial)"
        self.rule = f"Daily close above SMA{c['sma_days']} = hold, {dial}; below = cash."

    def decide(self, asset, candles, pos, now_ms):
        closes = [r[4] for r in candles["1d"]]
        c = self.cfg
        exp, info = trend_d1.target_exposure(closes, c["sma_days"], c["vol_target"],
                                             c["vol_lookback_days"], c["rebalance_step"])
        if "sma" in info:  # bot/strategy.py reports cents, which would show a memecoin's SMA as 0.0
            info["sma"], info["close"] = sma(closes, c["sma_days"]), closes[-1]
        info["exposure"] = exp
        return exp, info

    def describe(self, asset, info, price, pos):
        if "sma" not in info:
            return f"{asset}: {info.get('reason', 'waiting for data')}"
        d = _pct(price, info["sma"])
        n = self.cfg["sma_days"]
        if pos.get("exp", 0) > 0:
            return (f"{asset}: holding {pos['exp']:.0%} of its sleeve, price is {d:+.1%} vs the SMA{n} "
                    f"({level(info['sma'])}); sells if a daily close ends below it")
        return (f"{asset}: in cash, price is {d:+.1%} vs the SMA{n} ({level(info['sma'])}); "
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
        info = {"close": last, "sma": avg, "above_sma": last > avg}
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
                    f"({level(info['sma'])}); sells if a 4h close ends below it")
        return (f"{asset}: in cash, price is {d:+.1%} vs the 4h SMA300 ({level(info['sma'])}); "
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
        info = {"close": close, "high48": high48, "low24": low24, "sma50": sma50}
        if pos.get("in"):
            peak = max(pos.get("peak") or pos.get("entry") or close, close)
            pos["peak"] = peak
            stop = peak * (1 - self.TRAIL)
            info.update(peak=peak, stop=stop)
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
                    f"{level(exit_at)} ({_pct(price, exit_at):+.1%} away; trailing stop {level(stop)}, "
                    f"24h low {level(info['low24'])})")
        below_sma = price <= info["sma50"]
        if price <= info["high48"]:
            s = f"{asset}: waiting, {asset} is {-_pct(price, info['high48']):.1%} below the 48h high ({level(info['high48'])})"
        else:
            s = f"{asset}: waiting for the hourly close, price is above the 48h high ({level(info['high48'])})"
        if below_sma:
            s += f"; also below the daily SMA50 ({level(info['sma50'])}), so no buys"
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
        info = {"close": close, "rsi2": round(r2, 2), "sma50": sma50}
        if pos.get("in"):
            stop = pos["entry"] * (1 - self.STOP)
            info.update(stop=stop, held_h=round((now_ms - pos["entry_ms"]) / HOUR, 2))
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
                    f"at {_hhmm(pos['entry_ms'] + self.MAX_HOLD_MS)}, or on a close below {level(stop)} "
                    f"({_pct(price, stop):+.1%} away)")
        s = f"{asset}: waiting, RSI(2) on the last hourly close is {info['rsi2']:.0f} (buys below 5)"
        if price <= info["sma50"]:
            s += f"; price is below the daily SMA50 ({level(info['sma50'])}), so no buys"
        return s


class Hold(Strategy):
    """Benchmark: buys its sleeves at the first daily decision and never sells."""
    name, timeframe, benchmark = "HOLD", "1d", True
    rule = "Benchmark: buys once at the start and holds."
    needs = {"1d": 2}

    def decide(self, asset, candles, pos, now_ms):
        return 1.0, {"reason": "benchmark: buy once and hold", "close": candles["1d"][-1][4]}

    def describe(self, asset, info, price, pos):
        if pos.get("exp"):
            entry = pos.get("entry")
            since = f", {_pct(price, entry):+.1%} since buying at {level(entry)}" if entry else ""
            return f"{asset}: holding since the start (benchmark){since}"
        return f"{asset}: buys at the next daily decision (benchmark)"


class LongShortD1(Strategy):
    """Picks the direction itself on every daily close: above SMA50 = long the sleeve,
    below = short it. Demo only: shorting needs margin or futures, which a normal spot
    account cannot do, so the engine simulates it at 1x (no leverage) with a borrow cost."""
    name, timeframe, allows_short = "LS_TREND", "1d", True
    rule = "Daily close above SMA50 = long the sleeve, below = short it (simulated short, 1x, no leverage)."
    SMA = 50
    needs = {"1d": 55}

    def decide(self, asset, candles, pos, now_ms):
        closes = [r[4] for r in candles["1d"]]
        avg = sma(closes, self.SMA)
        if avg is None:
            return 0.0, {"reason": "not enough history", "exposure": 0.0}
        last = closes[-1]
        exp = 1.0 if last > avg else -1.0
        side = "long" if exp > 0 else "short"
        info = {"close": last, "sma": avg, "direction": side, "exposure": exp,
                "reason": f"{'above' if exp > 0 else 'below'} SMA50: go {side}"}
        return exp, info

    def describe(self, asset, info, price, pos):
        if "sma" not in info:
            return f"{asset}: {info.get('reason', 'waiting for data')}"
        d = _pct(price, info["sma"])
        e = pos.get("exp", 0.0)
        if e > 0:
            return (f"{asset}: LONG, price is {d:+.1%} vs the SMA50 ({level(info['sma'])}); "
                    f"flips to short if a daily close ends below it")
        if e < 0:
            return (f"{asset}: SHORT (simulated), price is {d:+.1%} vs the SMA50 ({level(info['sma'])}); "
                    f"profits if the price falls, flips to long if a daily close ends above it")
        return f"{asset}: flat, price is {d:+.1%} vs the SMA50 ({level(info['sma'])}); opens at the next daily close"


class Scalp(Strategy):
    """Fast trades: long or short for minutes at a time, over and over (demo only).

    The rules were fixed before the backtest ran and are not tuned:
      entry, when flat: the last 15 one-minute closes moved at least 0.1% and the close is on the
        same side of its 60-minute average -> go with the move (up = long, down = simulated short)
      exit: +0.3% take profit, -0.3% stop, or 30 minutes, checked on every 1-minute close.
    Every round trip pays the fee twice, so the average win has to beat the fees."""
    name, timeframe, allows_short = "SCALP", "1m", True
    LOOKBACK, AVG, MOVE, TAKE, STOP, MAX_HOLD_MS = 15, 60, 0.001, 0.003, 0.003, 30 * 60_000
    EPS = 1e-9  # a price exactly on a level counts as reaching it (float rounding)
    needs = {"1m": 61}
    rule = ("Every minute: go with a 0.1%+ move of the last 15 minutes (long if up, short if down, on the same "
            "side of the 1-hour average); close at +0.3%, -0.3% or after 30 minutes. Simulated shorts, 1x.")

    def decide(self, asset, candles, pos, now_ms):
        m = candles["1m"]
        e = pos.get("exp", 0.0)
        if len(m) < self.AVG + 1:
            return e, {"reason": "not enough 1-minute history"}
        closes = [r[4] for r in m]
        close = closes[-1]
        avg = sum(closes[-self.AVG:]) / self.AVG
        move = close / closes[-1 - self.LOOKBACK] - 1
        info = {"close": close, "avg60": avg, "move15": move}
        if e:
            entry = pos.get("entry") or close
            gain = (close / entry - 1) * (1 if e > 0 else -1)
            entry_ms = pos.get("entry_ms")
            held = now_ms - (now_ms if entry_ms is None else entry_ms)
            side = "long" if e > 0 else "short"
            info.update(entry=entry, gain=gain, held_min=round(held / 60_000))
            if gain >= self.TAKE - self.EPS:
                info["reason"] = f"close {side}: +{self.TAKE:.1%} take profit"
                return 0.0, info
            if gain <= -self.STOP + self.EPS:
                info["reason"] = f"close {side}: -{self.STOP:.1%} stop"
                return 0.0, info
            if held >= self.MAX_HOLD_MS:
                info["reason"] = f"close {side}: 30 minute time exit"
                return 0.0, info
            info["reason"] = f"holding {side}: {gain:+.2%} since entry"
            return e, info
        if move >= self.MOVE - self.EPS and close > avg:
            info["reason"] = f"open long: up {move:.2%} in 15 minutes, above the 1-hour average"
            return 1.0, info
        if move <= -self.MOVE + self.EPS and close < avg:
            info["reason"] = f"open short: down {-move:.2%} in 15 minutes, below the 1-hour average"
            return -1.0, info
        info["reason"] = f"waiting: {move:+.2%} in 15 minutes (needs 0.1% on the same side as the 1-hour average)"
        return 0.0, info

    def describe(self, asset, info, price, pos):
        if "avg60" not in info:
            return f"{asset}: {info.get('reason', 'waiting for data')}"
        e = pos.get("exp", 0.0)
        if e:
            entry = pos.get("entry") or price
            sign = 1 if e > 0 else -1
            gain = (price / entry - 1) * sign
            take, stop = entry * (1 + sign * self.TAKE), entry * (1 - sign * self.STOP)
            side = "LONG" if e > 0 else "SHORT (simulated)"
            return (f"{asset}: {side} since {_hhmm(pos.get('entry_ms', 0))} at {level(entry)}, {gain:+.2%} now; "
                    f"takes profit at {level(take)}, stops at {level(stop)}, "
                    f"or closes at {_hhmm((pos.get('entry_ms') or 0) + self.MAX_HOLD_MS)}")
        return (f"{asset}: flat, {info['move15']:+.2%} in the last 15 minutes; opens long above +0.1% "
                f"(and above the 1-hour average {level(info['avg60'])}), short below -0.1%")


BASES = {"TREND_D1": TrendD1, "TREND_H4": TrendH4, "BREAKOUT_H1": BreakoutH1, "MEANREV_H1": MeanRevH1, "HOLD": Hold,
         "LS_TREND": LongShortD1, "SCALP": Scalp}


def build(cfg):
    """The four strategies plus the variants in config.json engine.variants.

    A variant is a copy of a base strategy with its own name, starting cash and fee.
    TREND_D1 variants may also override its settings (sma_days, vol_target, ...)."""
    e = cfg.get("engine", {})
    out = [TrendD1(cfg), TrendH4(), BreakoutH1(), MeanRevH1()]
    for s in out:
        s.start_cash, s.fee_rate, s.note, s.base = e.get("start_cash", 15.0), e.get("fee_rate", 0.001), "", s.name
    for v in e.get("variants", []):
        if sum(v.get("sleeves", SLEEVES).values()) > 1.0 + 1e-9:
            raise ValueError(f"{v['name']}: sleeves add up to more than 100% (no leverage)")
        if v["base"] == "TREND_D1":
            s = TrendD1(cfg, v["name"], v.get("overrides"))
        else:
            if v.get("overrides"):
                raise ValueError(f"{v['name']}: only TREND_D1 variants can override settings")
            s = BASES[v["base"]]()
            s.name = v["name"]
        s.start_cash = v.get("start_cash", e.get("start_cash", 15.0))
        s.fee_rate = v.get("fee_rate", e.get("fee_rate", 0.001))
        s.note, s.base = v.get("note", ""), v["base"]
        s.sleeves = dict(v.get("sleeves", SLEEVES))
        s.group = v.get("group", "BTC & ETH")
        if s.fee_rate != e.get("fee_rate", 0.001):
            s.rule += f" Pays {s.fee_rate:.1%} per trade."
        out.append(s)
    return out
