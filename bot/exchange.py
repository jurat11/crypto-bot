"""Exchange adapters (standard library only).

ExchangeAdapter is the generic interface the engine talks to. BinanceSpot is
the one implementation. It is used in two ways:

  - public market data from data-api.binance.vision (no keys, works from the
    user's location)
  - signed MARKET orders on the official Spot Testnet (testnet.binance.vision,
    fake funds). Only /api endpoints exist there.

Real-money trading is not here on purpose: api.binance.com trading endpoints
are never called and bot/broker.py LiveBroker still refuses to start.

HTTP 451 ("restricted location") raises LocationBlocked so the caller can show
it on the dashboard and back off instead of retrying in a loop.
"""
import hashlib
import hmac
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from decimal import ROUND_DOWN, ROUND_UP, Decimal

from .net import ssl_context

PUBLIC_BASE = "https://data-api.binance.vision/api"
TESTNET_BASE = "https://testnet.binance.vision/api"
UA = "crypto-bot/2.0"
CLIENT_ID_RE = re.compile(r"^[a-zA-Z0-9-_]{1,36}$")


class ExchangeError(Exception):
    def __init__(self, msg, status=None, code=None):
        super().__init__(msg)
        self.status, self.code = status, code


class LocationBlocked(ExchangeError):
    """HTTP 451: the exchange refuses requests from this location."""


# ---------- signing and ids ----------

def sign(query, secret):
    """HMAC-SHA256 hex signature of the exact query string Binance receives."""
    return hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()


def signed_query(params, secret, timestamp_ms=None, recv_window=5000):
    p = dict(params)
    if recv_window:
        p["recvWindow"] = recv_window
    p["timestamp"] = int(timestamp_ms if timestamp_ms is not None else time.time() * 1000)
    q = urllib.parse.urlencode(p)
    return q + "&signature=" + sign(q, secret)


def client_order_id(account, asset, side, decision_ms):
    """Deterministic newClientOrderId: the same decision always maps to the same id,
    so a retry can be recognised and never becomes a second order."""
    raw = f"cb-{account}-{asset}-{side[0].upper()}-{int(decision_ms)}"
    if not CLIENT_ID_RE.match(raw):
        raw = "cb-" + hashlib.sha1(raw.encode()).hexdigest()[:32]
    return raw


# ---------- exchange filters ----------

def _d(x):
    return Decimal(str(x))


def fmt(x):
    """Decimal/float -> plain string without exponent or trailing zeros."""
    d = _d(x).normalize()
    s = format(d, "f")
    return s if s not in ("-0", "") else "0"


def floor_step(value, step):
    v, s = _d(value), _d(step)
    if s <= 0:
        return v
    return (v / s).to_integral_value(rounding=ROUND_DOWN) * s


def ceil_step(value, step):
    v, s = _d(value), _d(step)
    if s <= 0:
        return v
    return (v / s).to_integral_value(rounding=ROUND_UP) * s


class SymbolFilters:
    """LOT_SIZE, MARKET_LOT_SIZE, PRICE_FILTER and MIN_NOTIONAL / NOTIONAL for one symbol."""

    NOTIONAL_MARGIN = Decimal("1.01")  # exchange checks the 5-min average price; keep 1% headroom

    def __init__(self, symbol, step_size, min_qty, max_qty, tick_size, min_notional,
                 market_step=0, market_min_qty=0, market_max_qty=0):
        self.symbol = symbol
        self.step_size, self.min_qty, self.max_qty = _d(step_size), _d(min_qty), _d(max_qty)
        self.tick_size, self.min_notional = _d(tick_size), _d(min_notional)
        self.market_step, self.market_min_qty = _d(market_step), _d(market_min_qty)
        self.market_max_qty = _d(market_max_qty)

    @classmethod
    def from_symbol_info(cls, info):
        f = {x["filterType"]: x for x in info.get("filters", [])}
        lot = f.get("LOT_SIZE", {})
        mlot = f.get("MARKET_LOT_SIZE", {})
        price = f.get("PRICE_FILTER", {})
        notional = f.get("NOTIONAL") or f.get("MIN_NOTIONAL") or {}
        return cls(info["symbol"], lot.get("stepSize", "0"), lot.get("minQty", "0"),
                   lot.get("maxQty", "0"), price.get("tickSize", "0"),
                   notional.get("minNotional", "0"), mlot.get("stepSize", "0"),
                   mlot.get("minQty", "0"), mlot.get("maxQty", "0"))

    def round_qty(self, qty):
        """Floor to stepSize (and MARKET_LOT_SIZE stepSize when the exchange sets one).
        Never rounds up: rounding up could spend money the account does not have."""
        q = floor_step(qty, self.step_size)
        if self.market_step > 0:
            q = floor_step(q, self.market_step)
        return q

    def round_price(self, price):
        return floor_step(price, self.tick_size)

    def check_market(self, qty, price):
        """(ok, reason) for a MARKET order of qty at roughly price."""
        q = _d(qty)
        if q <= 0:
            return False, "quantity rounds to zero"
        min_q = max(self.min_qty, self.market_min_qty)
        if q < min_q:
            return False, f"quantity {fmt(q)} below minimum {fmt(min_q)}"
        for mx in (self.max_qty, self.market_max_qty):
            if mx > 0 and q > mx:
                return False, f"quantity {fmt(q)} above maximum {fmt(mx)}"
        notional = q * _d(price)
        if notional < self.min_notional * self.NOTIONAL_MARGIN:
            return False, f"order ${notional:.2f} below exchange minimum ${self.min_notional:.2f}"
        return True, ""

    def check_quote(self, quote_usd):
        if _d(quote_usd) < self.min_notional * self.NOTIONAL_MARGIN:
            return False, f"order ${float(quote_usd):.2f} below exchange minimum ${self.min_notional:.2f}"
        return True, ""

    def market_qty(self, qty, price):
        """Rounded quantity (float) and '' or (0.0, reason) when it cannot be sent."""
        q = self.round_qty(qty)
        ok, reason = self.check_market(q, price)
        return (float(q), "") if ok else (0.0, reason)


# ---------- transport ----------

def urllib_transport(method, url, headers, body=None, timeout=10):
    """Returns (status, bytes). HTTP errors are returned, not raised."""
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl_context()) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


# ---------- adapters ----------

class ExchangeAdapter:
    """What the engine needs from any exchange."""
    name = "base"
    label = ""

    def ping(self):
        raise NotImplementedError

    def symbol_filters(self, symbol):
        raise NotImplementedError

    def depth(self, symbol, limit=100):
        raise NotImplementedError

    def book_ticker(self, symbols):
        raise NotImplementedError

    def klines(self, symbol, interval, limit=500, start_ms=None, end_ms=None):
        raise NotImplementedError

    def balances(self):
        raise NotImplementedError

    def market_order(self, symbol, side, client_order_id, quantity=None, quote_qty=None):
        raise NotImplementedError

    def order_status(self, symbol, client_order_id):
        raise NotImplementedError


def parse_book(raw):
    return {"bids": [(float(p), float(q)) for p, q in raw.get("bids", [])],
            "asks": [(float(p), float(q)) for p, q in raw.get("asks", [])]}


def parse_kline(k):
    """(open_ms, open, high, low, close, volume, close_ms)"""
    return (int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5]), int(k[6]))


class BinanceSpot(ExchangeAdapter):
    name = "binance"

    def __init__(self, base_url=PUBLIC_BASE, api_key=None, api_secret=None,
                 transport=urllib_transport, clock=time.time, label=""):
        self.base = base_url.rstrip("/")
        self._key, self._secret = api_key, api_secret
        self._transport, self._clock = transport, clock
        self.label = label
        self._filters = {}

    def __repr__(self):  # never show keys
        return f"BinanceSpot({self.base!r}, keys={'yes' if self._key else 'no'})"

    @property
    def has_keys(self):
        return bool(self._key and self._secret)

    def _request(self, method, path, params=None, signed=False, timeout=10):
        params = dict(params or {})
        headers = {"User-Agent": UA}
        if signed:
            if not self.has_keys:
                raise ExchangeError("no API keys configured")
            headers["X-MBX-APIKEY"] = self._key
            query = signed_query(params, self._secret, timestamp_ms=self._clock() * 1000)
        else:
            query = urllib.parse.urlencode(params)
        url = f"{self.base}{path}"
        body = None
        if method == "POST":
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            body = query.encode()
        elif query:
            url += "?" + query
        status, raw = self._transport(method, url, headers, body, timeout)
        if status == 451:
            raise LocationBlocked("blocked by exchange location (HTTP 451)", status=451)
        try:
            data = json.loads(raw.decode() or "null")
        except ValueError:
            data = None
        if status >= 400:
            code = data.get("code") if isinstance(data, dict) else None
            msg = data.get("msg") if isinstance(data, dict) else f"HTTP {status}"
            raise ExchangeError(f"{msg} (HTTP {status}{', code ' + str(code) if code else ''})",
                                status=status, code=code)
        return data

    # public
    def ping(self):
        self._request("GET", "/v3/ping")
        return True

    def symbol_filters(self, symbol):
        if symbol not in self._filters:
            info = self._request("GET", "/v3/exchangeInfo", {"symbol": symbol})
            self._filters[symbol] = SymbolFilters.from_symbol_info(info["symbols"][0])
        return self._filters[symbol]

    def depth(self, symbol, limit=100):
        return parse_book(self._request("GET", "/v3/depth", {"symbol": symbol, "limit": limit}))

    def book_ticker(self, symbols):
        data = self._request("GET", "/v3/ticker/bookTicker",
                             {"symbols": json.dumps(list(symbols), separators=(",", ":"))})
        return {d["symbol"]: (float(d["bidPrice"]), float(d["askPrice"])) for d in data}

    def klines(self, symbol, interval, limit=500, start_ms=None, end_ms=None):
        p = {"symbol": symbol, "interval": interval, "limit": limit}
        if start_ms is not None:
            p["startTime"] = int(start_ms)
        if end_ms is not None:
            p["endTime"] = int(end_ms)
        return [parse_kline(k) for k in self._request("GET", "/v3/klines", p, timeout=20)]

    # signed (testnet)
    def account(self):
        return self._request("GET", "/v3/account", signed=True)

    def balances(self):
        return {b["asset"]: float(b["free"]) for b in self.account().get("balances", [])}

    def market_order(self, symbol, side, client_order_id, quantity=None, quote_qty=None):
        if not CLIENT_ID_RE.match(client_order_id):
            raise ValueError("invalid client order id")
        p = {"symbol": symbol, "side": side.upper(), "type": "MARKET",
             "newClientOrderId": client_order_id, "newOrderRespType": "FULL"}
        if quote_qty is not None:
            p["quoteOrderQty"] = fmt(quote_qty)
        elif quantity is not None:
            p["quantity"] = fmt(quantity)
        else:
            raise ValueError("quantity or quote_qty required")
        return normalize_order(self._request("POST", "/v3/order", p, signed=True))

    def order_status(self, symbol, client_order_id):
        return normalize_order(self._request(
            "GET", "/v3/order", {"symbol": symbol, "origClientOrderId": client_order_id}, signed=True))


def normalize_order(o):
    """Average price, filled qty and commission from a Binance order response."""
    qty = float(o.get("executedQty", 0) or 0)
    quote = float(o.get("cummulativeQuoteQty", 0) or 0)
    fills = o.get("fills") or []
    commission = {}
    for f in fills:
        a = f.get("commissionAsset", "")
        commission[a] = commission.get(a, 0.0) + float(f.get("commission", 0) or 0)
    return {"status": o.get("status"), "order_id": o.get("orderId"),
            "client_order_id": o.get("clientOrderId") or o.get("origClientOrderId"),
            "qty": qty, "quote": quote, "avg_price": quote / qty if qty else 0.0,
            "commission": commission, "raw_fills": len(fills)}
