"""Re-run the research yourself (article #20 method, done honestly).

  python3 backtest.py                full report with the config in config.json
  python3 backtest.py --sma 30       try another SMA length

It pulls full daily history, then reports:
  1. in-sample 2018-2022 vs out-of-sample 2023-today (the rule was picked on in-sample)
  2. fee sensitivity (0.1%, 0.4%, 0.6% per trade)
  3. SMA length sensitivity (a real edge should not depend on one magic number)
  4. year by year, including the bad years
  5. buy-and-hold for comparison
"""
import json
import sys
from datetime import datetime, timezone

from bot import data, strategy

IS = (datetime(2018, 7, 20, tzinfo=timezone.utc), datetime(2023, 1, 1, tzinfo=timezone.utc))
OOS = (datetime(2023, 1, 1, tzinfo=timezone.utc), datetime(2100, 1, 1, tzinfo=timezone.utc))


def sleeve(rows, cfg, fee):
    """Daily equity curve for one asset following the strategy. Decision at close i, held to close i+1."""
    closes = [r[4] for r in rows]
    eq, prev, curve = 1.0, 0.0, {}
    need = max(cfg["sma_days"], cfg["vol_lookback_days"] + 1)
    for i in range(len(rows) - 1):
        window = closes[max(0, i + 1 - need): i + 1]
        exp, _ = strategy.target_exposure(window, cfg["sma_days"], cfg["vol_target"],
                                          cfg["vol_lookback_days"], cfg["rebalance_step"])
        if exp != prev:
            eq *= 1 - fee * abs(exp - prev)
            prev = exp
        eq *= 1 + exp * (closes[i + 1] / closes[i] - 1)
        curve[rows[i + 1][0]] = eq
    return curve


def hold_curve(rows):
    return {r[0]: r[4] / rows[0][4] for r in rows}


def combine(curves, weights, t0, t1):
    keys = sorted(set.intersection(*[set(c) for c in curves.values()]))
    keys = [k for k in keys if t0 <= k < t1]
    base = {a: curves[a][keys[0]] for a in curves}
    return [(k, sum(weights[a] * curves[a][k] / base[a] for a in curves)) for k in keys]


def stats(series):
    peak, mdd = 0, 0
    for _, e in series:
        peak = max(peak, e)
        mdd = max(mdd, 1 - e / peak)
    years = (series[-1][0] - series[0][0]) / (365 * 86_400_000)
    tot = series[-1][1] / series[0][1]
    return tot, tot ** (1 / years) - 1, mdd


def by_year(series):
    out, start = {}, {}
    prev = series[0][1]
    for t, e in series:
        y = datetime.fromtimestamp(t / 1000, timezone.utc).year
        start.setdefault(y, prev)
        out[y] = e / start[y] - 1
        prev = e
    return out


def ms(dt):
    return int(dt.timestamp() * 1000)


def main():
    cfg = json.load(open("config.json"))
    if "--sma" in sys.argv:
        cfg["sma_days"] = int(sys.argv[sys.argv.index("--sma") + 1])
    hist = {a: data.drop_open_candle(data.daily_candles(a, history=True)[0]) for a in cfg["assets"]}
    w = cfg["assets"]

    def report(label, fee, c=cfg):
        curves = {a: sleeve(hist[a], c, fee) for a in w}
        for name, (t0, t1) in (("in-sample 2018-22", IS), ("out-of-sample 2023+", OOS)):
            tot, cagr, mdd = stats(combine(curves, w, ms(t0), ms(t1)))
            print(f"  {label:<22} {name:<20} x{tot:6.2f}  CAGR {cagr:6.1%}  max drawdown {mdd:5.1%}")
        return curves

    print(f"\nStrategy: SMA{cfg['sma_days']} trend filter, vol target {cfg['vol_target']}, weights {w}\n")
    print("1-2. Fees (per trade) and in-sample vs out-of-sample")
    curves = None
    for fee in (0.001, 0.004, 0.006):
        c = report(f"fee {fee:.1%}", fee)
        curves = c if fee == cfg["fee_rate"] else curves
    holds = {a: hold_curve(hist[a]) for a in w}
    for name, (t0, t1) in (("in-sample 2018-22", IS), ("out-of-sample 2023+", OOS)):
        tot, cagr, mdd = stats(combine(holds, w, ms(t0), ms(t1)))
        print(f"  {'buy and hold':<22} {name:<20} x{tot:6.2f}  CAGR {cagr:6.1%}  max drawdown {mdd:5.1%}")

    print("\n3. SMA length sensitivity (fee from config)")
    for n in (30, 40, 50, 80, 100, 150, 200):
        c2 = dict(cfg, sma_days=n)
        cv = {a: sleeve(hist[a], c2, cfg["fee_rate"]) for a in w}
        _, ci, mi = stats(combine(cv, w, ms(IS[0]), ms(IS[1])))
        _, co, mo = stats(combine(cv, w, ms(OOS[0]), ms(OOS[1])))
        print(f"  SMA{n:<4} in-sample CAGR {ci:6.1%} (dd {mi:4.0%})   out-of-sample CAGR {co:6.1%} (dd {mo:4.0%})")

    print("\n4. Year by year (strategy vs buy and hold)")
    s_all = combine(curves, w, ms(IS[0]), ms(OOS[1]))
    h_all = combine(holds, w, ms(IS[0]), ms(OOS[1]))
    ys, yh = by_year(s_all), by_year(h_all)
    for y in ys:
        print(f"  {y}: strategy {ys[y]:+7.1%}   hold {yh.get(y, 0):+7.1%}")
    tot, cagr, mdd = stats(s_all)
    print(f"\n5. Whole period: x{tot:.2f}, CAGR {cagr:.1%}, max drawdown {mdd:.1%}")
    print(f"   $20 at this CAGR for one year is about ${20 * (1 + cagr):.2f}. Past results do not predict future ones.\n")


if __name__ == "__main__":
    main()
