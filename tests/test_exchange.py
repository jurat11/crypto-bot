"""Offline tests for the exchange layer: signing, filters, fill simulation, testnet safety."""
import json
import unittest
import urllib.parse

from bot import exchange, orderbook, testnet
from bot.db import Store

BTC_INFO = {"symbol": "BTCUSDT", "filters": [
    {"filterType": "PRICE_FILTER", "minPrice": "0.01000000", "maxPrice": "1000000.00000000", "tickSize": "0.01000000"},
    {"filterType": "LOT_SIZE", "minQty": "0.00001000", "maxQty": "9000.00000000", "stepSize": "0.00001000"},
    {"filterType": "MARKET_LOT_SIZE", "minQty": "0.00000000", "maxQty": "83.14000000", "stepSize": "0.00000000"},
    {"filterType": "NOTIONAL", "minNotional": "5.00000000", "applyMinToMarket": True,
     "maxNotional": "9000000.00000000", "applyMaxToMarket": False, "avgPriceMins": 5},
]}
BOOK = {"bids": [(99.0, 0.1), (98.0, 0.2), (97.0, 5.0)],
        "asks": [(100.0, 0.1), (101.0, 0.1), (102.0, 5.0)]}


class Signing(unittest.TestCase):
    def test_signature_matches_binance_docs_example(self):
        secret = "NhqPtmdSJYdKjVHjA7PZj4Mge3R5YNiP1e3UZjInClVN65XAbvqqM6A7H5fATj0j"
        query = ("symbol=LTCBTC&side=BUY&type=LIMIT&timeInForce=GTC&quantity=1&price=0.1"
                 "&recvWindow=5000&timestamp=1499827319559")
        self.assertEqual(exchange.sign(query, secret),
                         "c8db56825ae71d6d79447849e617115f4a920fa2acdcab2b053c4b2838bd6b71")

    def test_signed_query_with_fixed_timestamp(self):
        secret = "NhqPtmdSJYdKjVHjA7PZj4Mge3R5YNiP1e3UZjInClVN65XAbvqqM6A7H5fATj0j"
        params = {"symbol": "LTCBTC", "side": "BUY", "type": "LIMIT", "timeInForce": "GTC",
                  "quantity": 1, "price": 0.1}
        q = exchange.signed_query(params, secret, timestamp_ms=1499827319559)
        self.assertTrue(q.startswith("symbol=LTCBTC&side=BUY&type=LIMIT&timeInForce=GTC&quantity=1&price=0.1"
                                     "&recvWindow=5000&timestamp=1499827319559&signature="))
        self.assertTrue(q.endswith("c8db56825ae71d6d79447849e617115f4a920fa2acdcab2b053c4b2838bd6b71"))

    def test_order_request_is_signed_and_secret_never_sent(self):
        sent = []

        def transport(method, url, headers, body, timeout):
            sent.append((method, url, headers, body))
            return 200, json.dumps({"status": "FILLED", "orderId": 1, "clientOrderId": "cb-x",
                                    "executedQty": "0.00018", "cummulativeQuoteQty": "14.99",
                                    "fills": [{"commission": "0.00000018", "commissionAsset": "BTC"}]}).encode()

        a = exchange.BinanceSpot(exchange.TESTNET_BASE, "KEY123", "SECRET456", transport=transport,
                                 clock=lambda: 1_700_000_000.0)
        o = a.market_order("BTCUSDT", "buy", "cb-x", quote_qty=15)
        method, url, headers, body = sent[0]
        self.assertEqual(method, "POST")
        self.assertEqual(url, "https://testnet.binance.vision/api/v3/order")
        self.assertEqual(headers["X-MBX-APIKEY"], "KEY123")
        form = urllib.parse.parse_qs(body.decode())
        self.assertEqual(form["type"], ["MARKET"])
        self.assertEqual(form["quoteOrderQty"], ["15"])
        self.assertEqual(form["newClientOrderId"], ["cb-x"])
        self.assertEqual(form["timestamp"], ["1700000000000"])
        unsigned = body.decode().rsplit("&signature=", 1)[0]
        self.assertEqual(form["signature"], [exchange.sign(unsigned, "SECRET456")])
        self.assertNotIn(b"SECRET456", body)
        self.assertNotIn("SECRET456", url + repr(a))
        self.assertAlmostEqual(o["avg_price"], 14.99 / 0.00018)
        self.assertEqual(o["commission"], {"BTC": 0.00000018})

    def test_client_order_id_is_deterministic_and_valid(self):
        a = exchange.client_order_id("BREAKOUT_H1", "BTC", "BUY", 1727222400000)
        self.assertEqual(a, exchange.client_order_id("BREAKOUT_H1", "BTC", "BUY", 1727222400000))
        self.assertNotEqual(a, exchange.client_order_id("BREAKOUT_H1", "BTC", "SELL", 1727222400000))
        self.assertRegex(a, r"^[a-zA-Z0-9-_]{1,36}$")
        long_id = exchange.client_order_id("A_VERY_LONG_ACCOUNT_NAME_FOR_TESTS", "BTC", "BUY", 1727222400000)
        self.assertRegex(long_id, r"^[a-zA-Z0-9-_]{1,36}$")


class Filters(unittest.TestCase):
    def setUp(self):
        self.f = exchange.SymbolFilters.from_symbol_info(BTC_INFO)

    def test_qty_floors_to_step_never_up(self):
        self.assertEqual(exchange.fmt(self.f.round_qty(0.000179999)), "0.00017")
        self.assertEqual(exchange.fmt(self.f.round_qty(0.00018)), "0.00018")
        self.assertEqual(exchange.fmt(self.f.round_qty(0.3)), "0.3")  # no float artefacts
        self.assertEqual(exchange.fmt(self.f.round_qty(0.000009)), "0")

    def test_price_rounds_to_tick(self):
        self.assertEqual(exchange.fmt(self.f.round_price(84123.456789)), "84123.45")

    def test_min_qty_and_min_notional(self):
        self.assertEqual(self.f.market_qty(0.000009, 84_000)[0], 0.0)
        qty, why = self.f.market_qty(0.00005, 84_000)  # $4.20
        self.assertEqual(qty, 0.0)
        self.assertIn("below exchange minimum", why)
        qty, why = self.f.market_qty(0.000179999, 84_000)  # $14.28 after rounding
        self.assertEqual((qty, why), (0.00017, ""))
        self.assertFalse(self.f.check_quote(4.99)[0])
        self.assertFalse(self.f.check_quote(5.00)[0])  # needs 1% headroom over the 5-min average price
        self.assertTrue(self.f.check_quote(7.5)[0])

    def test_market_max_qty(self):
        ok, why = self.f.check_market(100, 84_000)
        self.assertFalse(ok)
        self.assertIn("above maximum", why)

    def test_old_min_notional_filter_name(self):
        info = {"symbol": "X", "filters": [{"filterType": "MIN_NOTIONAL", "minNotional": "10"}]}
        self.assertEqual(float(exchange.SymbolFilters.from_symbol_info(info).min_notional), 10.0)


class OrderBookFill(unittest.TestCase):
    def test_buy_walks_asks_and_pays_fee_in_coins(self):
        r = orderbook.simulate_market_order(BOOK, "BUY", 0.001, quote_qty=25.0)
        qty = 0.1 + 0.1 + (25.0 - 10.0 - 10.1) / 102.0
        self.assertAlmostEqual(r["qty"], qty)
        self.assertAlmostEqual(r["notional"], 25.0)
        self.assertAlmostEqual(r["avg_price"], 25.0 / qty)
        self.assertAlmostEqual(r["qty_net"], qty * 0.999)
        self.assertAlmostEqual(r["fee_usd"], 0.025)
        self.assertEqual(r["levels"], 3)
        self.assertTrue(r["complete"])
        self.assertAlmostEqual(r["mid"], 99.5)
        self.assertAlmostEqual(r["slippage_bps"], (25.0 / qty - 99.5) / 99.5 * 1e4)
        self.assertGreater(r["slippage_bps"], 50)  # half spread alone is ~50 bp here

    def test_sell_walks_bids_and_pays_fee_in_usdt(self):
        r = orderbook.simulate_market_order(BOOK, "SELL", 0.001, base_qty=0.25)
        notional = 0.1 * 99 + 0.15 * 98
        self.assertAlmostEqual(r["notional"], notional)
        self.assertAlmostEqual(r["proceeds_net"], notional * 0.999)
        self.assertAlmostEqual(r["avg_price"], notional / 0.25)
        self.assertEqual(r["levels"], 2)
        self.assertAlmostEqual(r["slippage_bps"], (99.5 - notional / 0.25) / 99.5 * 1e4)

    def test_small_order_fills_at_best_price(self):
        r = orderbook.simulate_market_order(BOOK, "BUY", 0.001, quote_qty=5.0)
        self.assertAlmostEqual(r["avg_price"], 100.0)
        self.assertEqual(r["levels"], 1)

    def test_thin_book_is_incomplete(self):
        r = orderbook.simulate_market_order(BOOK, "SELL", 0.001, base_qty=100)
        self.assertFalse(r["complete"])
        self.assertAlmostEqual(r["qty"], 5.3)

    def test_bad_arguments(self):
        with self.assertRaises(ValueError):
            orderbook.simulate_market_order(BOOK, "SELL", 0.001, quote_qty=10)
        with self.assertRaises(ValueError):
            orderbook.simulate_market_order(BOOK, "HOLD", 0.001, base_qty=1)


class FakeTestnet:
    """Stands in for BinanceSpot(TESTNET_BASE). Counts every order sent."""
    has_keys = True

    def __init__(self, blocked=False, timeout_on_order=False):
        self.blocked, self.timeout_on_order = blocked, timeout_on_order
        self.orders, self.account_calls, self.status_calls = [], 0, 0

    def account(self):
        self.account_calls += 1
        if self.blocked:
            raise exchange.LocationBlocked("blocked", status=451)
        return {"balances": []}

    def balances(self):
        return {"BTC": 1.0, "USDT": 10_000.0}

    def symbol_filters(self, symbol):
        return exchange.SymbolFilters.from_symbol_info(BTC_INFO)

    def depth(self, symbol, limit=100):
        if self.blocked:
            raise exchange.LocationBlocked("blocked", status=451)
        return {"bids": [(84_000.0, 1.0)], "asks": [(84_001.0, 1.0)]}

    def market_order(self, symbol, side, cid, quantity=None, quote_qty=None):
        self.orders.append((side, cid, quantity, quote_qty))
        if self.timeout_on_order:
            raise TimeoutError("timed out")
        qty = quantity if quantity else round(quote_qty / 84_001.0, 5)
        return {"status": "FILLED", "order_id": len(self.orders), "client_order_id": cid, "qty": qty,
                "quote": qty * 84_001.0, "avg_price": 84_001.0 if side == "BUY" else 84_000.0,
                "commission": {"BTC": qty * 0.001} if side == "BUY" else {"USDT": qty * 84.0}}

    def order_status(self, symbol, cid):
        self.status_calls += 1
        return {"status": "FILLED", "order_id": 9, "client_order_id": cid, "qty": 0.00017,
                "quote": 14.28, "avg_price": 84_001.0, "commission": {}}


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class TestnetSafety(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")

    def test_never_sends_the_same_decision_twice(self):
        fake = FakeTestnet()
        tr = testnet.TestnetTrader(fake, testnet.TestnetGate(fake, True), self.store)
        r1 = tr.mirror("BREAKOUT_H1", "BTC", "BUY", 1727222400000, usd=15.0)
        r2 = tr.mirror("BREAKOUT_H1", "BTC", "BUY", 1727222400000, usd=15.0)
        self.assertEqual(r1["status"], "filled")
        self.assertEqual(r2["status"], "duplicate")
        self.assertEqual(len(fake.orders), 1)
        self.assertAlmostEqual(r1["sim_slippage_bps"], (84_001 - 84_000.5) / 84_000.5 * 1e4)
        # the sell sells what the buy received (after the fee in BTC), rounded to stepSize
        r3 = tr.mirror("BREAKOUT_H1", "BTC", "SELL", 1727226000000, fraction=1.0)
        self.assertEqual(r3["status"], "filled")
        self.assertEqual(fake.orders[1][2], 0.00017)

    def test_buy_mirrors_the_demo_quantity(self):
        fake = FakeTestnet()
        tr = testnet.TestnetTrader(fake, testnet.TestnetGate(fake, True), self.store)
        r = tr.mirror("TREND_D1", "BTC", "BUY", 5, usd=6.75, qty=0.00008)
        self.assertEqual(r["status"], "filled")
        self.assertEqual(fake.orders[0][2:], (0.00008, None))  # quantity, not quoteOrderQty

    def test_timeout_is_reconciled_not_resent(self):
        fake = FakeTestnet(timeout_on_order=True)
        tr = testnet.TestnetTrader(fake, testnet.TestnetGate(fake, True), self.store)
        r = tr.mirror("TREND_D1", "BTC", "BUY", 1, usd=15.0)
        self.assertEqual(r["status"], "filled")
        self.assertEqual((len(fake.orders), fake.status_calls), (1, 1))
        self.assertEqual(tr.mirror("TREND_D1", "BTC", "BUY", 1, usd=15.0)["status"], "duplicate")
        self.assertEqual(len(fake.orders), 1)

    def test_451_blocks_and_rechecks_only_after_15_minutes(self):
        fake, clock = FakeTestnet(blocked=True), Clock()
        gate = testnet.TestnetGate(fake, True, clock=clock)
        tr = testnet.TestnetTrader(fake, gate, self.store, clock=clock)
        r = tr.mirror("TREND_D1", "BTC", "BUY", 1, usd=15.0)
        self.assertEqual(r["status"], "skipped")
        self.assertEqual(gate.status, "blocked")
        self.assertIn("TESTNET: blocked by exchange location", gate.message)
        self.assertEqual(fake.account_calls, 1)
        for minutes in (1, 5, 14):  # the engine keeps asking; nothing goes out
            clock.t = 1_800_000_000.0 + minutes * 60
            tr.mirror("TREND_D1", "BTC", "BUY", 1000 + minutes, usd=15.0)
        self.assertEqual(fake.account_calls, 1)
        self.assertEqual(fake.orders, [])
        clock.t = 1_800_000_000.0 + RECHECK + 1
        fake.blocked = False  # the exchange starts working again: no code change needed
        self.assertEqual(tr.mirror("TREND_D1", "BTC", "BUY", 2, usd=15.0)["status"], "filled")
        self.assertEqual(fake.account_calls, 2)
        self.assertEqual(gate.message, "TESTNET: fake funds, real orders")

    def test_off_and_missing_keys(self):
        fake = FakeTestnet()
        self.assertFalse(testnet.TestnetGate(fake, False).usable())
        fake.has_keys = False
        g = testnet.TestnetGate(fake, True)
        self.assertFalse(g.usable())
        self.assertIn("missing", g.message)
        self.assertEqual(fake.account_calls, 0)

    def test_order_below_min_notional_is_skipped(self):
        fake = FakeTestnet()
        tr = testnet.TestnetTrader(fake, testnet.TestnetGate(fake, True), self.store)
        r = tr.mirror("TREND_D1", "BTC", "BUY", 1, usd=0.75)
        self.assertEqual(r["status"], "skipped")
        self.assertIn("below exchange minimum", r["reason"])
        self.assertEqual(fake.orders, [])

    def test_real_451_response_raises_location_blocked(self):
        a = exchange.BinanceSpot(exchange.TESTNET_BASE, "k", "s",
                                 transport=lambda *a: (451, b'{"code":0,"msg":"restricted location"}'))
        with self.assertRaises(exchange.LocationBlocked):
            a.account()


RECHECK = testnet.RECHECK_S

if __name__ == "__main__":
    unittest.main()
