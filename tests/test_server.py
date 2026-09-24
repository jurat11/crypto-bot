"""Dashboard HTTP endpoints (skipped when FastAPI is not installed)."""
import json
import os
import shutil
import tempfile
import unittest

from test_engine import CFG, Clock, FakePublic, Stub

from bot.db import Store
from bot.engine import Engine
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

    def test_stop_needs_confirmation_and_creates_file(self):
        self.assertEqual(self.client.post("/api/stop", json={}).status_code, 400)
        self.assertFalse(os.path.exists("STOP"))
        r = self.client.post("/api/stop", json={"confirm": True})
        self.assertEqual(r.json(), {"stopped": True})
        self.assertTrue(os.path.exists("STOP"))
        self.engine.clock.t += 1
        self.assertTrue(self.client.get("/api/snapshot").json()["stopped"])


if __name__ == "__main__":
    unittest.main()
