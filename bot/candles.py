"""Candle helpers shared by the engine and the backtest.

A candle is (open_ms, open, high, low, close, volume). Strategies only ever
see CLOSED candles: a candle is closed once open_ms + interval <= now.
"""
HOUR = 3_600_000
INTERVAL_MS = {"1m": 60_000, "1h": HOUR, "4h": 4 * HOUR, "1d": 24 * HOUR}


def closed(rows, interval, now_ms):
    step = INTERVAL_MS[interval]
    return [r for r in rows if r[0] + step <= now_ms]


def last_close_ms(interval, now_ms):
    """Close time (ms) of the most recent closed candle of this interval."""
    step = INTERVAL_MS[interval]
    return (now_ms // step) * step


def resample(rows, interval):
    """Aggregate smaller candles into `interval` buckets aligned to UTC (4h: 00,04,..; 1d: 00:00)."""
    step = INTERVAL_MS[interval]
    out = []
    for r in rows:
        b = (r[0] // step) * step
        if out and out[-1][0] == b:
            o = out[-1]
            out[-1] = (b, o[1], max(o[2], r[2]), min(o[3], r[3]), r[4], o[5] + r[5])
        else:
            out.append((b, r[1], r[2], r[3], r[4], r[5]))
    return out
