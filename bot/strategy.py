"""Trend filter with a volatility dial.

Rule per asset, decided once a day on the last CLOSED daily candle:
  - close > SMA(sma_days)  -> hold the asset
  - otherwise              -> hold cash
  - size = min(1, vol_target / realized_vol) so calmer markets get full size
    and wild markets get less, rounded to rebalance_step to avoid churn.

Why this rule and not something fancier: it was the only candidate that held up
out of sample (2023-2026) on BTC and ETH after being chosen on 2018-2022 data,
and it held across SMA lengths 40-150 and across execution hours. See RESEARCH.md.
"""
import math


def sma(values, n):
    if len(values) < n:
        return None
    return sum(values[-n:]) / n


def realized_vol(closes, lookback):
    if len(closes) < lookback + 1:
        return None
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(len(closes) - lookback, len(closes))]
    mu = sum(rets) / len(rets)
    var = sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var * 365)


def target_exposure(closes, sma_days=50, vol_target=0.4, vol_lookback=30, step=0.1):
    """Return (exposure 0..1, info dict) for one asset."""
    avg = sma(closes, sma_days)
    if avg is None:
        return 0.0, {"reason": "not enough history"}
    last = closes[-1]
    info = {"close": round(last, 2), "sma": round(avg, 2), "above_sma": last > avg}
    if last <= avg:
        info["reason"] = "below SMA: stay in cash"
        return 0.0, info
    exp = 1.0
    if vol_target:
        vol = realized_vol(closes, vol_lookback)
        info["vol"] = round(vol, 3) if vol else None
        if vol:
            exp = min(1.0, vol_target / vol)
    exp = round(round(exp / step) * step, 4) if step else exp
    info["reason"] = f"above SMA: exposure {exp:.0%}"
    return exp, info


def portfolio_targets(closes_by_asset, weights, cfg):
    """Weights of total equity per asset, e.g. {'BTC': 0.5 * exposure}."""
    targets, notes = {}, {}
    for asset, w in weights.items():
        exp, info = target_exposure(
            closes_by_asset[asset], cfg["sma_days"], cfg["vol_target"],
            cfg["vol_lookback_days"], cfg["rebalance_step"])
        targets[asset] = round(w * exp, 4)
        notes[asset] = info
    return targets, notes
