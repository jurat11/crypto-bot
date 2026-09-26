"""Dashboard HTTP endpoints (skipped when FastAPI is not installed)."""
import json
import os
import shutil
import ssl
import tempfile
import unittest
from unittest import mock

from test_engine import CFG, Clock, FakePublic, Stub

from bot import net
from bot.db import Store
from bot.engine import BACKTEST_FILE, Engine
from bot.market import CandleStore, MarketFeed

try:
    from fastapi.testclient import TestClient

    from bot.server import create_app
except ImportError:  # pragma: no cover
    TestClient = None


@unittest.skipIf(TestClient is None, "fastapi not installed (pip install -r requirements.txt)")
class Endpoints(unittest.TestCase):
    def setUp(self):
        self.tmp, self.cwd = tempfile.mkdtemp(), os.getcwd()
        os.chdir(self.tmp)
        clock = Clock()
        pub = FakePublic(clock)
        feed = MarketFeed(["BTCUSDT", "ETHUSDT"], pub, clock=clock)
        feed.poll_once()
        self.engine = Engine(CFG, Store(":memory:"), feed, CandleStore(pub), pub, [Stub()], backtest={}, clock=clock)
        self.engine.tick()
        self.client = TestClient(create_app(self.engine))

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.tmp)

    def test_page(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("DEMO: no real money", r.text)
        self.assertIn("EventSource(\"/api/stream\")", r.text)
        self.assertIn("cdn.jsdelivr.net/npm/chart.js", r.text)

    def test_snapshot_and_history(self):
        snap = self.client.get("/api/snapshot").json()
        self.assertEqual(snap["banner"]["demo"], "DEMO: no real money")
        self.assertEqual(len(snap["leaderboard"]), 2)
        hist = self.client.get("/api/history").json()
        self.assertIn("HOLD_50_50", hist["equity"])
        self.assertEqual(hist["accounts"]["HOLD_50_50"], "Hold 50/50 BTC/ETH")

    def test_backtest_endpoint(self):
        self.assertEqual(self.client.get("/api/backtest").json(), {})
        os.makedirs("data", exist_ok=True)
        rep = {"generated_utc": "2026-09-24T17:00:00+00:00", "hold": {"is": {}, "oos": {}},
               "strategies": {"STUB_H1": {"verdict": "FAILED BACKTEST", "why": "lost money", "results": {}}}}
        with open(BACKTEST_FILE, "w") as f:
            json.dump(rep, f)
        got = self.client.get("/api/backtest").json()
        self.assertEqual(got["strategies"]["STUB_H1"]["verdict"], "FAILED BACKTEST")
        self.assertIn("loadBacktest", self.client.get("/").text)

    def test_stop_needs_confirmation_and_creates_file(self):
        self.assertEqual(self.client.post("/api/stop", json={}).status_code, 400)
        self.assertFalse(os.path.exists("STOP"))
        r = self.client.post("/api/stop", json={"confirm": True})
        self.assertEqual(r.json(), {"stopped": True})
        self.assertTrue(os.path.exists("STOP"))
        self.engine.clock.t += 1
        self.assertTrue(self.client.get("/api/snapshot").json()["stopped"])


    def test_password_protects_every_endpoint(self):
        import base64
        client = TestClient(create_app(self.engine, password="s3cret"))
        for path in ("/", "/api/snapshot", "/api/history", "/api/backtest"):
            self.assertEqual(client.get(path).status_code, 401, path)
        self.assertEqual(client.post("/api/stop", json={"confirm": True}).status_code, 401)
        self.assertFalse(os.path.exists("STOP"))
        wrong = {"Authorization": "Basic " + base64.b64encode(b"me:nope").decode()}
        right = {"Authorization": "Basic " + base64.b64encode(b"me:s3cret").decode()}
        self.assertEqual(client.get("/", headers=wrong).status_code, 401)
        self.assertEqual(client.get("/", headers=right).status_code, 200)
        self.assertEqual(client.get("/api/snapshot", headers=right).status_code, 200)


@unittest.skipIf(TestClient is None, "fastapi not installed (pip install -r requirements.txt)")
class BacktestGate(unittest.TestCase):
    def setUp(self):
        self.tmp, self.cwd = tempfile.mkdtemp(), os.getcwd()
        os.chdir(self.tmp)

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.tmp)

    def test_only_backtested_strategies_trade_in_demo(self):
        from bot.server import wired_strategies
        wired, held = wired_strategies(CFG, {"TREND_D1": {"verdict": "PASSED", "why": ""}})
        self.assertEqual([s.name for s in wired], ["TREND_D1"])
        self.assertEqual(held, [n for n in CFG["engine"]["strategies"] if n != "TREND_D1"])

    def test_price_feed_covers_every_coin(self):
        from bot import strategies
        from bot.server import symbols_for
        syms = symbols_for(strategies.build(CFG))
        self.assertEqual(syms[:2], ["BTCUSDT", "ETHUSDT"])
        for coin in ("SOL", "XRP", "DOGE", "PEPE", "PAXG"):
            self.assertIn(coin + "USDT", syms)
        self.assertEqual(len(syms), len(set(syms)))

    def test_first_launch_runs_the_backtest(self):
        from bot import server
        full = {n: {"verdict": "PASSED", "why": ""} for n in CFG["engine"]["strategies"]}
        with mock.patch("bot.server.load_backtest", side_effect=[{}, full]), \
                mock.patch("backtest_strategies.run_and_save") as run:
            self.assertEqual(server.ensure_backtest(CFG), full)
        run.assert_called_once()
        with mock.patch("bot.server.load_backtest", return_value=full), \
                mock.patch("backtest_strategies.run_and_save") as run:
            server.ensure_backtest(CFG)
        run.assert_not_called()

    def test_backtest_failure_does_not_crash_the_server(self):
        from bot import server
        with mock.patch("bot.server.load_backtest", return_value={}), \
                mock.patch("backtest_strategies.run_and_save", side_effect=OSError("no network")):
            self.assertEqual(server.ensure_backtest(CFG), {})


class TLS(unittest.TestCase):
    def test_certificates_are_always_verified(self):
        ctx = net.ssl_context()
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(ctx.check_hostname)


if __name__ == "__main__":
    unittest.main()
