"""Article #20's "find the hour that pays" hack, tested properly.

Pull hourly BTC candles, rank the 24 UTC hours by average return on 2018-2022,
then check whether the same hours still win on 2023-today. If the ranking does
not carry over (low correlation), the "best hour" was noise.

  python3 hour_test.py BTC
"""
import json
import math
import sys
import urllib.request
from datetime import datetime, timezone


def hourly(asset):
    out, t = [], 1514764800000  # 2018-01-01
    now = int(datetime.now(timezone.utc).timestamp() * 1000)
    while t < now:
        url = f"https://data-api.binance.vision/api/v3/klines?symbol={asset}USDT&interval=1h&limit=1000&startTime={t}"
        rows = json.load(urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "x"}), timeout=20))
        if not rows:
            break
        out += [(r[0], float(r[1]), float(r[4])) for r in rows]
        t = rows[-1][0] + 3_600_000
    return out


def table(rows, t0, t1):
    s, n = [0.0] * 24, [0] * 24
    for t, o, c in rows:
        if t0 <= t < t1:
            h = datetime.fromtimestamp(t / 1000, timezone.utc).hour
            s[h] += math.log(c / o)
            n[h] += 1
    return [s[h] / n[h] * 1e4 if n[h] else 0 for h in range(24)]  # basis points


def corr(a, b):
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    return num / math.sqrt(sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b))


if __name__ == "__main__":
    asset = sys.argv[1] if len(sys.argv) > 1 else "BTC"
    rows = hourly(asset)
    split = int(datetime(2023, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    a, b = table(rows, 0, split), table(rows, split, 10 ** 15)
    print(f"{asset}: average return per UTC hour, basis points (1 bp = 0.01%)")
    print("hour  2018-22  2023+")
    for h in sorted(range(24), key=lambda h: -a[h]):
        print(f"{h:4d}  {a[h]:7.2f}  {b[h]:6.2f}")
    print(f"\nCorrelation of hour ranking between periods: {corr(a, b):.2f} (near 0 = the pattern did not persist)")
    print("A round trip costs about 20-80 bp in fees, far more than any single hour's average edge.")
