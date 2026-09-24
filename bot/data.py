"""Daily price data. Standard library only, no API keys.

Primary source: Binance public market-data mirror (data-api.binance.vision).
Fallback: Coinbase Exchange public candles (works from the US).
A candle is (open_time_ms, open, high, low, close, volume) in UTC days.
"""
import json
import time
import urllib.request

UA = {"User-Agent": "crypto-bot/1.0"}
DAY_MS = 86_400_000


def _get(url, timeout=20):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def binance_daily(asset, quote="USDT", start_ms=None, limit=1000):
    out, t = [], start_ms
    while True:
        url = (f"https://data-api.binance.vision/api/v3/klines?symbol={asset}{quote}"
               f"&interval=1d&limit={limit}" + (f"&startTime={t}" if t is not None else ""))
        rows = _get(url)
        out += [(int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])) for k in rows]
        if len(rows) < limit or t is None:
            break
        t = rows[-1][0] + DAY_MS
    return out


def coinbase_daily(asset, quote="USD", days=400):
    """Coinbase returns max 300 candles per call, newest first."""
    out, end = [], int(time.time())
    remaining = days
    while remaining > 0:
        n = min(300, remaining)
        start = end - n * 86400
        url = (f"https://api.exchange.coinbase.com/products/{asset}-{quote}/candles"
               f"?granularity=86400&start={start}&end={end}")
        rows = _get(url)  # [time, low, high, open, close, volume]
        out += [(int(r[0]) * 1000, float(r[3]), float(r[2]), float(r[1]), float(r[4]), float(r[5])) for r in rows]
        end, remaining = start, remaining - n
    return sorted(set(out))


def daily_candles(asset, days=400, history=False):
    """Recent daily candles; history=True pulls everything since 2017 (for backtests)."""
    errors = []
    try:
        start = 1502928000000 if history else int(time.time() * 1000) - days * DAY_MS
        rows = binance_daily(asset, start_ms=start)
        if rows:
            return rows, "binance"
    except Exception as e:  # network block, 451, etc.
        errors.append(f"binance: {e}")
    try:
        rows = coinbase_daily(asset, days=3000 if history else days)
        if rows:
            return rows, "coinbase"
    except Exception as e:
        errors.append(f"coinbase: {e}")
    raise RuntimeError("No data source reachable. " + " | ".join(errors))


def drop_open_candle(rows, now_ms=None):
    """Remove today's still-forming candle so signals only use closed days."""
    now_ms = now_ms or int(time.time() * 1000)
    return [r for r in rows if r[0] + DAY_MS <= now_ms]
