"""Live market data from Binance's public endpoints (no keys).

MarketFeed keeps the latest bid/ask/last for each symbol:
  - websocket wss://data-stream.binance.vision/ws, subscribed to <sym>@kline_1m and
    <sym>@bookTicker, reconnecting with backoff when it drops (Binance also closes
    every connection after 24h)
  - REST polling of /api/v3/ticker/bookTicker every 5 s whenever the websocket has
    been quiet for 10 s (fallback)

CandleStore serves CLOSED klines per (symbol, interval) from REST and only
re-downloads when a new candle of that interval has closed.
"""
import json
import threading
import time

from .candles import INTERVAL_MS, closed, last_close_ms
from .net import ssl_context

WS_URL = "wss://data-stream.binance.vision/ws"
WS_QUIET_S = 10


class MarketFeed:
    def __init__(self, symbols, public, ws_url=WS_URL, poll_s=5, clock=time.time, log=None):
        self.symbols = list(symbols)
        self.public, self.ws_url, self.poll_s, self.clock = public, ws_url, poll_s, clock
        self.log = log or (lambda text: None)
        self.quotes = {s: {"bid": None, "ask": None, "last": None, "ts": 0.0, "source": None} for s in self.symbols}
        self.last_ws_msg = 0.0
        self.last_rest_ok = 0.0
        self.ws_state = "not started"
        self.rest_error = ""
        self._lock = threading.Lock()
        self._stop = threading.Event()

    # ----- updates -----
    def update(self, symbol, bid=None, ask=None, last=None, source="ws"):
        with self._lock:
            q = self.quotes[symbol]
            if bid is not None:
                q["bid"] = bid
            if ask is not None:
                q["ask"] = ask
            if last is not None:
                q["last"] = last
            q["ts"], q["source"] = self.clock(), source

    def handle_ws_message(self, raw):
        msg = json.loads(raw)
        if not isinstance(msg, dict):
            return
        if msg.get("e") == "kline":
            k = msg["k"]
            if k["s"] in self.quotes:
                self.update(k["s"], last=float(k["c"]), source="websocket")
        elif "b" in msg and "a" in msg and msg.get("s") in self.quotes:  # bookTicker
            self.update(msg["s"], bid=float(msg["b"]), ask=float(msg["a"]), source="websocket")
        else:
            return
        self.last_ws_msg = self.clock()

    # ----- reads -----
    def quote(self, symbol):
        with self._lock:
            q = dict(self.quotes[symbol])
        mid = (q["bid"] + q["ask"]) / 2 if q["bid"] and q["ask"] else q["last"]
        q["mid"] = mid
        q["spread"] = q["ask"] - q["bid"] if q["bid"] and q["ask"] else None
        q["spread_bps"] = q["spread"] / mid * 1e4 if q["spread"] is not None and mid else None
        q["age_s"] = self.clock() - q["ts"] if q["ts"] else None
        return q

    def mid(self, symbol):
        return self.quote(symbol)["mid"]

    def age_s(self, symbols=None):
        """Age of the stalest of these symbols (default: all); infinity before the first price."""
        ages = [self.quote(s)["age_s"] for s in (symbols or self.symbols)]
        return float("inf") if any(a is None for a in ages) else max(ages)

    @property
    def status(self):
        now = self.clock()
        if now - self.last_ws_msg < WS_QUIET_S:
            return "websocket"
        if now - self.last_rest_ok < 3 * self.poll_s:
            return "rest fallback"
        return "down"

    # ----- threads -----
    def start(self):
        threading.Thread(target=self._ws_loop, name="ws", daemon=True).start()
        threading.Thread(target=self._poll_loop, name="rest-poll", daemon=True).start()

    def stop(self):
        self._stop.set()

    def poll_once(self):
        try:
            for sym, (bid, ask) in self.public.book_ticker(self.symbols).items():
                if sym in self.quotes:
                    self.update(sym, bid=bid, ask=ask, source="rest")
            self.last_rest_ok = self.clock()
            self.rest_error = ""
        except Exception as e:
            if not self.rest_error:
                self.log(f"REST price poll failed: {str(e)[:100]}")
            self.rest_error = str(e)[:100]

    def _poll_loop(self):
        # Poll right away so prices show before the websocket connects, then only as a fallback.
        self.poll_once()
        while not self._stop.wait(self.poll_s):
            if self.clock() - self.last_ws_msg >= WS_QUIET_S:
                self.poll_once()

    def _ws_loop(self):
        try:
            from websockets.sync.client import connect
        except ImportError:
            self.ws_state = "websockets package missing, using REST polling"
            self.log(self.ws_state)
            return
        streams = [f"{s.lower()}@{k}" for s in self.symbols for k in ("kline_1m", "bookTicker")]
        backoff = 1
        while not self._stop.is_set():
            try:
                with connect(self.ws_url, open_timeout=10, ping_interval=20, ping_timeout=20,
                             max_size=2 ** 20, ssl=ssl_context()) as ws:
                    ws.send(json.dumps({"method": "SUBSCRIBE", "params": streams, "id": 1}))
                    self.ws_state = "connected"
                    self.log("websocket connected")
                    backoff = 1
                    while not self._stop.is_set():
                        self.handle_ws_message(ws.recv(timeout=30))
            except Exception as e:
                self.ws_state = f"reconnecting in {backoff}s ({str(e)[:80]})"
                self.log(f"websocket dropped: {str(e)[:80]}; reconnecting in {backoff}s, REST polling meanwhile")
            if self._stop.wait(backoff):
                return
            backoff = min(backoff * 2, 60)


class CandlesNotReady(Exception):
    """The exchange has not published the candle that just closed yet."""


class CandleStore:
    def __init__(self, public):
        self.public = public
        self._cache = {}
        self._lock = threading.Lock()

    def get(self, symbol, interval, n, now_ms):
        want = last_close_ms(interval, now_ms)
        key = (symbol, interval)
        with self._lock:
            c = self._cache.get(key)
            if c and c["upto"] >= want and len(c["rows"]) >= n:
                return c["rows"][-n:]
        rows = self.public.klines(symbol, interval, limit=min(1000, n + 5))
        rows = closed([r[:6] for r in rows], interval, now_ms)
        step = INTERVAL_MS[interval]
        if not rows or rows[-1][0] + step < want:
            raise CandlesNotReady(f"{symbol} {interval} candle closing {want} not published yet")
        with self._lock:
            self._cache[key] = {"rows": rows, "upto": rows[-1][0] + step}
        return rows[-n:]
