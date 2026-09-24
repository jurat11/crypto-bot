"""Made-up market for developing the dashboard offline (python3 -m bot.server --fake-market).

NOT real data. Prices are a smooth deterministic wave plus noise around BTC 84,000
and ETH 2,650, so candles, order books and the live feed agree with each other.
The dashboard labels everything "FAKE PRICES" in this mode, and it uses data/dev.db.
"""
import math
import threading
import time

from .candles import INTERVAL_MS
from .exchange import ExchangeAdapter, SymbolFilters
from .market import MarketFeed

BASE = {"BTCUSDT": 84_000.0, "ETHUSDT": 2_650.0, "SOLUSDT": 180.0, "XRPUSDT": 0.62,
        "DOGEUSDT": 0.15, "PEPEUSDT": 0.0000105, "PAXGUSDT": 3_750.0}
STEP = {"BTCUSDT": ("0.00001", "0.01"), "ETHUSDT": ("0.0001", "0.01"), "SOLUSDT": ("0.001", "0.01"),
        "XRPUSDT": ("0.1", "0.0001"), "DOGEUSDT": ("1", "0.00001"), "PEPEUSDT": ("1", "0.00000001"),
        "PAXGUSDT": ("0.0001", "0.01")}
WILD = {"BTCUSDT": 1.0, "ETHUSDT": 1.25, "SOLUSDT": 1.6, "XRPUSDT": 1.5, "DOGEUSDT": 2.0,
        "PEPEUSDT": 2.6, "PAXGUSDT": 0.25}
DAY = 86_400.0


def _noise(t, seed):
    """Smooth value noise in [-1, 1] from integer hashing, interpolated per minute."""
    def h(i):
        x = (i * 374761393 + seed * 668265263) & 0xFFFFFFFF
        x = ((x ^ (x >> 13)) * 1274126177) & 0xFFFFFFFF
        return ((x ^ (x >> 16)) & 0xFFFF) / 32767.5 - 1
    i, f = divmod(t / 60.0, 1.0)
    i = int(i)
    f = f * f * (3 - 2 * f)
    return h(i) * (1 - f) + h(i + 1) * f


def price_at(symbol, t):
    seed = sorted(BASE).index(symbol) + 1
    k = WILD[symbol]
    x = (0.18 * math.sin(2 * math.pi * t / (120 * DAY) + seed)
         + 0.06 * math.sin(2 * math.pi * t / (9 * DAY) + 2 * seed)
         + 0.012 * math.sin(2 * math.pi * t / (0.5 * DAY) + seed)
         + 0.004 * math.sin(2 * math.pi * t / 1800 + seed)
         + 0.0015 * _noise(t, seed))
    return BASE[symbol] * math.exp(k * x)


class FakePublic(ExchangeAdapter):
    name, label = "fake", "FAKE"

    def __init__(self, clock=time.time):
        self.clock = clock

    def ping(self):
        return True

    def symbol_filters(self, symbol):
        step, tick = STEP[symbol]
        return SymbolFilters(symbol, step, step, "90000000000", tick, "5")

    def depth(self, symbol, limit=100):
        p = price_at(symbol, self.clock())
        tick = float(STEP[symbol][1])
        usd = 1_700.0  # about $1,700 more depth per level
        bids = [(p - tick * (1 + i * i), (0.02 + 0.03 * i) * usd * 50 / p) for i in range(min(limit, 30))]
        asks = [(p + tick * (i * i), (0.02 + 0.03 * i) * usd * 50 / p) for i in range(min(limit, 30))]
        return {"bids": bids, "asks": asks}

    def book_ticker(self, symbols):
        out = {}
        for s in symbols:
            b = self.depth(s, 1)
            out[s] = (b["bids"][0][0], b["asks"][0][0])
        return out

    def klines(self, symbol, interval, limit=500, start_ms=None, end_ms=None):
        step = INTERVAL_MS[interval] / 1000
        now = self.clock()
        last_open = now // step * step
        rows = []
        for n in range(limit - 1, -1, -1):
            t0 = last_open - n * step
            t1 = min(t0 + step, now)
            pts = [price_at(symbol, t0 + (t1 - t0) * j / 6) for j in range(7)]
            rows.append((int(t0 * 1000), pts[0], max(pts), min(pts), pts[-1], 1.0, int((t0 + step) * 1000) - 1))
        return rows


class FakeFeed(MarketFeed):
    def start(self):
        threading.Thread(target=self._loop, name="fake-feed", daemon=True).start()

    def _loop(self):
        while True:
            for s, (bid, ask) in self.public.book_ticker(self.symbols).items():
                self.update(s, bid=bid, ask=ask, source="fake")
            self.last_ws_msg = self.clock()
            if self._stop.wait(1.0):
                return
