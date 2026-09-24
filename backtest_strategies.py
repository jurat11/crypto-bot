"""Backtest the four demo strategies before they trade in demo (same honest method as backtest.py).

  python3 backtest_strategies.py             refresh hourly history, print the table,
                                             write data/backtest_results.json
  python3 backtest_strategies.py --offline   use cached candles in data/cache/ only

The rules were fixed in SPEC.md before this test ran, and nothing is tuned here.
  in-sample (IS)       2018-01-01 .. 2022-12-31   the period rules are "chosen" on
  out-of-sample (OOS)  2023-01-01 .. last closed hour, never used for choosing
Fees are 0.1% and 0.4% per trade on the traded fraction of the sleeve, as in backtest.py.
Each strategy runs a 50% BTC and 50% ETH sleeve. It decides on closed candles of its own
timeframe (the same decide() code the live engine uses), trades at that candle's close, and is
marked to market every hour.

Verdict: a strategy that loses money out of sample at either fee is marked FAILED BACKTEST.
It still runs in demo, and the dashboard shows the flag.
"""
import csv
import json
import os
import sys
import time
from datetime import datetime, timezone

from bot import candles as cd
from bot import strategies
from bot.exchange import BinanceSpot

H, D = cd.HOUR, cd.INTERVAL_MS["1d"]
FEES = (0.001, 0.004)
BINANCE_START = 1502928000000  # 2017-08-17, first Binance candle; used as warm-up only
IS = (datetime(2018, 1, 1, tzinfo=timezone.utc), datetime(2023, 1, 1, tzinfo=timezone.utc))
OOS = (datetime(2023, 1, 1, tzinfo=timezone.utc), datetime(2100, 1, 1, tzinfo=timezone.utc))
CACHE = "data/cache"
RESULTS = "data/backtest_results.json"


def ms(dt):
    return int(dt.timestamp() * 1000)


def day(t):
    return datetime.fromtimestamp(t / 1000, timezone.utc).strftime("%Y-%m-%d")


# ---------- data ----------

def load_cache(symbol):
    path = os.path.join(CACHE, f"{symbol}_1h.csv")
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]))
                for r in csv.reader(f)]


def save_cache(symbol, rows):
    os.makedirs(CACHE, exist_ok=True)
    with open(os.path.join(CACHE, f"{symbol}_1h.csv"), "w", newline="") as f:
        csv.writer(f).writerows(rows)


def hourly(symbol, offline=False):
    rows = load_cache(symbol)
    if not offline:
        api = BinanceSpot()
        t = rows[-1][0] + H if rows else BINANCE_START
        n = 0
        while True:
            batch = api.klines(symbol, "1h", limit=1000, start_ms=t)
            if not batch:
                break
            rows += [k[:6] for k in batch]
            t = batch[-1][0] + H
            n += 1
            if n % 10 == 0:
                print(f"  {symbol}: {len(rows):,} hourly candles, up to {day(t)}", file=sys.stderr)
            if len(batch) < 1000:
                break
            time.sleep(0.05)
        dedup = {r[0]: r for r in rows}
        # only closed candles go in the cache; the still-forming hour would otherwise stay frozen in it
        rows = cd.closed([dedup[k] for k in sorted(dedup)], "1h", int(time.time() * 1000))
        save_cache(symbol, rows)
    return cd.closed(rows, "1h", int(time.time() * 1000))


# ---------- simulation ----------

def exposure_path(strategy, asset, h1):
    """[(close_ms, close, exposure after the decision at that close)] for one asset, hourly."""
    h4, d1 = cd.resample(h1, "4h"), cd.resample(h1, "1d")
    need = strategy.needs
    pos, path = {}, []
    j4 = jd = 0
    seen = {"1h": -1, "4h": 0, "1d": 0}
    for i, bar in enumerate(h1):
        t = bar[0] + H
        while j4 < len(h4) and h4[j4][0] + 4 * H <= t:
            j4 += 1
        while jd < len(d1) and d1[jd][0] + D <= t:
            jd += 1
        counts = {"1h": i + 1, "4h": j4, "1d": jd}
        tf = strategy.timeframe
        if counts[tf] > seen[tf]:  # a new candle of the strategy's own timeframe just closed
            seen[tf] = counts[tf]
            window = {}
            for iv, n in need.items():
                src, end = {"1h": (h1, i + 1), "4h": (h4, j4), "1d": (d1, jd)}[iv]
                window[iv] = src[max(0, end - n):end]
            exp, _ = strategy.decide(asset, window, pos, t)
            prev = pos.get("exp", 0.0)
            if exp != prev:
                strategies.on_fill(pos, "BUY" if exp > prev else "SELL", bar[4], t, exp)
        path.append((t, bar[4], pos.get("exp", 0.0)))
    return path


def sleeve(path, fee):
    """Hourly equity curve plus trades and round trips (entry time, exit time, won?).

    Holds a fixed coin quantity between trades, like the demo account: when the exposure
    changes it trades the difference to exposure x sleeve equity and pays `fee` on the traded
    notional (buyer pays in coins, seller in USDT)."""
    cash, qty, prev_e = 1.0, 0.0, 0.0
    curve, trades, trips = {}, [], []
    entry = None
    for t, c, e in path:
        if e != prev_e:
            eq = cash + qty * c
            if prev_e == 0:
                entry = (t, eq)
            diff = e * eq - qty * c
            if diff > 0:
                cash -= diff
                qty += diff * (1 - fee) / c
            else:
                sold = qty if e == 0 else -diff / c
                qty -= sold
                cash += sold * c * (1 - fee)
            trades.append(t)
            if e == 0 and entry:
                trips.append((entry[0], t, cash > entry[1]))
                entry = None
        prev_e = e
        curve[t] = cash + qty * c
    return curve, trades, trips


def combine(curves, weights, t0, t1):
    keys = sorted(set.intersection(*[set(c) for c in curves.values()]))
    keys = [k for k in keys if t0 <= k < t1]
    if not keys:
        return []
    base = {a: curves[a][keys[0]] for a in curves}
    return [(k, sum(weights[a] * curves[a][k] / base[a] for a in curves)) for k in keys]


def stats(series):
    peak, mdd = 0.0, 0.0
    for _, e in series:
        peak = max(peak, e)
        mdd = max(mdd, 1 - e / peak)
    years = (series[-1][0] - series[0][0]) / (365 * D)
    tot = series[-1][1] / series[0][1]
    return {"return": tot - 1, "cagr": tot ** (1 / years) - 1 if years > 0 else 0.0, "max_dd": mdd,
            "start": day(series[0][0]), "end": day(series[-1][0])}


def evaluate(paths, weights, fee, periods):
    """paths: {asset: exposure path}. Returns {period_name: stats + trades + win rate}."""
    sl = {a: sleeve(p, fee) for a, p in paths.items()}
    curves = {a: s[0] for a, s in sl.items()}
    out = {}
    for name, (t0, t1) in periods.items():
        series = combine(curves, weights, t0, t1)
        st = stats(series)
        trips = [w for a in sl for (_, x, w) in sl[a][2] if t0 <= x < t1]
        st["trades"] = sum(1 for a in sl for t in sl[a][1] if t0 <= t < t1)
        st["round_trips"] = len(trips)
        st["win_rate"] = sum(trips) / len(trips) if trips else None
        exp_sum = sum(e for a, p in paths.items() for (t, _, e) in p if t0 <= t < t1)
        n = sum(1 for a, p in paths.items() for (t, _, _) in p if t0 <= t < t1)
        st["time_invested"] = exp_sum / n if n else 0.0
        out[name] = st
    return out


def verdict(results):
    losses = [f"{float(fee):.1%}" for fee, r in results.items() if r["oos"]["return"] <= 0]
    if losses:
        return "FAILED BACKTEST", f"lost money out of sample at {' and '.join(losses)} fee"
    return "PASSED", "made money out of sample at 0.1% and 0.4% fees"


def run(hist, cfg, periods=None):
    periods = periods or {"is": (ms(IS[0]), ms(IS[1])), "oos": (ms(OOS[0]), ms(OOS[1]))}
    weights = strategies.SLEEVES
    report = {"strategies": {}}
    for s in strategies.build(cfg):
        paths = {a: exposure_path(s, a, hist[a]) for a in weights}
        res = {str(fee): evaluate(paths, weights, fee, periods) for fee in FEES}
        v, why = verdict(res)
        report["strategies"][s.name] = {"verdict": v, "why": why, "rule": s.rule, "results": res}
    hold = {a: [(r[0] + H, r[4], 1.0) for r in hist[a]] for a in weights}
    report["hold"] = evaluate(hold, weights, 0.0, periods)
    return report


def pct(x, digits=1):
    return "n/a" if x is None else f"{x * 100:.{digits}f}%"


def table(report):
    oos_end = report["hold"]["oos"]["end"]
    lines = [
        f"| Strategy | Fee | IS CAGR | IS max DD | OOS CAGR | OOS return | OOS max DD | OOS trades | OOS win rate | Verdict |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name, s in report["strategies"].items():
        for fee, r in s["results"].items():
            i, o = r["is"], r["oos"]
            lines.append(f"| {name} | {float(fee):.1%} | {pct(i['cagr'])} | {pct(i['max_dd'], 0)} | {pct(o['cagr'])} | "
                         f"{pct(o['return'])} | {pct(o['max_dd'], 0)} | {o['trades']} | {pct(o['win_rate'], 0)} | "
                         f"{s['verdict'] if fee == str(FEES[0]) else ''} |")
    i, o = report["hold"]["is"], report["hold"]["oos"]
    lines.append(f"| Hold 50/50 BTC/ETH | none | {pct(i['cagr'])} | {pct(i['max_dd'], 0)} | {pct(o['cagr'])} | "
                 f"{pct(o['return'])} | {pct(o['max_dd'], 0)} | 0 | n/a | benchmark |")
    head = (f"In-sample {report['hold']['is']['start']} to {report['hold']['is']['end']}, "
            f"out-of-sample {report['hold']['oos']['start']} to {oos_end}. 50% BTC / 50% ETH sleeves, "
            f"hourly marks. Trades count every buy or sell (both coins).")
    why = [f"- {n}: {s['verdict']} ({s['why']})" for n, s in report["strategies"].items()]
    return "\n".join([head, ""] + lines + [""] + why)


def main():
    cfg = json.load(open("config.json"))
    offline = "--offline" in sys.argv
    hist = {}
    for a in strategies.SLEEVES:
        print(f"Loading {a}USDT hourly candles{' (cache only)' if offline else ''}...", file=sys.stderr)
        try:
            hist[a] = hourly(f"{a}USDT", offline)
        except OSError as e:
            sys.exit(f"Could not download candles from data-api.binance.vision ({e}). "
                     "Run this on a network that can reach it, or use --offline with a filled data/cache/.")
        if not hist[a]:
            sys.exit(f"No cached {a}USDT candles in {CACHE}/. Run once without --offline.")
        print(f"  {a}: {len(hist[a]):,} candles, {day(hist[a][0][0])} to {day(hist[a][-1][0] + H)}", file=sys.stderr)
    report = run(hist, cfg)
    report["generated_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    report["data_source"] = "Binance spot 1h klines (data-api.binance.vision), 4h and 1d built from them"
    os.makedirs(os.path.dirname(RESULTS), exist_ok=True)
    with open(RESULTS, "w") as f:
        json.dump(report, f, indent=1)
    print(table(report))
    print(f"\nSaved {RESULTS}. The dashboard reads the verdicts from it. Past results do not predict future ones.")


if __name__ == "__main__":
    main()
