"""Live dashboard: python3 -m bot.server  ->  http://localhost:8000

On the first launch it runs backtest_strategies.py (about a minute), prints the
table, and only then puts the strategies into demo: a strategy with no backtest
result never trades. Then it starts the market feed, the demo engine, the
optional testnet worker and a FastAPI app that streams the engine snapshot over
Server-Sent Events every second, and opens the dashboard in your browser.

  --fake-market   development only: made-up prices, so the page can be worked on
                  without network. Uses data/dev.db and says FAKE PRICES everywhere.
  --no-browser    do not open the browser
  --host / --port default 127.0.0.1:8000 (the STOP button has no login, so the
                  dashboard only listens on this computer unless you change --host)

Secrets (.env) are loaded into the environment and never printed or logged.
"""
import argparse
import asyncio
import json
import os
import sys
import threading
import time
import webbrowser

from . import env, strategies
from .alerts import Alerts
from .db import Store
from .engine import BACKTEST_FILE, Engine, load_backtest, sse, stream_delta
from .exchange import BinanceSpot
from .market import CandleStore, MarketFeed

STATIC = os.path.join(os.path.dirname(__file__), "static")


def create_app(engine, password=None):
    """password: when set (DASHBOARD_PASSWORD in .env), every page and API call needs it
    (HTTP Basic auth, any user name). Use it whenever the dashboard is reachable from
    another computer, because the STOP button has no other protection."""
    import base64
    import secrets

    from fastapi import Depends, FastAPI, HTTPException, Request
    from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

    def check_password(request: Request):
        if not password:
            return
        header = request.headers.get("authorization", "")
        given = ""
        if header.startswith("Basic "):
            try:
                given = base64.b64decode(header[6:]).decode().partition(":")[2]
            except (ValueError, UnicodeDecodeError):
                given = ""
        if not secrets.compare_digest(given.encode(), password.encode()):
            raise HTTPException(401, "password required", {"WWW-Authenticate": 'Basic realm="crypto-bot"'})

    app = FastAPI(title="crypto-bot dashboard", docs_url=None, redoc_url=None, openapi_url=None,
                  dependencies=[Depends(check_password)])

    @app.get("/", response_class=HTMLResponse)
    def index():
        with open(os.path.join(STATIC, "index.html")) as f:
            return f.read()

    @app.get("/api/snapshot")
    def snapshot():
        return JSONResponse(json.loads(sse(engine.snapshot())[len("data: "):]))

    @app.get("/api/history")
    def history():
        return engine.history()

    @app.get("/api/backtest")
    def backtest():
        try:
            with open(BACKTEST_FILE) as f:
                rep = json.load(f)
        except (OSError, ValueError):
            return {}
        return {k: rep.get(k) for k in ("generated_utc", "strategies", "hold")}

    @app.get("/api/stream")
    async def stream(request: Request):
        async def gen():
            seen = {}
            while not await request.is_disconnected():
                snap = await asyncio.to_thread(engine.snapshot)
                yield sse(stream_delta(snap, seen))
                await asyncio.sleep(1)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/api/stop")
    async def stop(request: Request):
        try:
            body = await request.json()
        except ValueError:
            body = {}
        if body.get("confirm") is not True:
            return JSONResponse({"stopped": False, "error": "confirmation required"}, status_code=400)
        await asyncio.to_thread(engine.press_stop, "dashboard")
        return {"stopped": True}

    return app


def wired_strategies(cfg, backtest):
    """Strategies listed in engine.strategies that also have a backtest result.
    SPEC: no strategy trades in demo before it has been through the backtest."""
    wanted = [s for s in strategies.build(cfg) if s.name in cfg["engine"]["strategies"]]
    return [s for s in wanted if s.name in backtest], [s.name for s in wanted if s.name not in backtest]


def ensure_backtest(cfg):
    """First launch: run the backtest before any strategy trades in demo."""
    backtest = load_backtest()
    if all(n in backtest for n in cfg["engine"]["strategies"]):
        return backtest
    print("First launch: running the backtest before any strategy trades in demo (about a minute)...")
    try:
        import backtest_strategies
        backtest_strategies.run_and_save()
    except Exception as e:
        print(f"Backtest could not run ({str(e)[:120]}). Strategies without a result stay out of demo; "
              "run python3 backtest_strategies.py when the data API is reachable, then restart.")
    return load_backtest()


def build(args):
    cfg = json.load(open("config.json"))
    ecfg = cfg["engine"]
    backtest = ensure_backtest(cfg) if not args.fake_market else load_backtest()
    wired, held = wired_strategies(cfg, backtest)
    for name in held:
        print(f"{name}: not trading in demo yet, it has no backtest result")
    alerts = Alerts(enabled=None)
    if args.fake_market:
        from .devmarket import FakeFeed, FakePublic
        public = FakePublic()
        feed = FakeFeed(["BTCUSDT", "ETHUSDT"], public)
        store = Store("data/dev.db")
        wired = strategies.build(cfg)  # all four, on made-up prices, to exercise the page
        label = "FAKE PRICES (dev mode, not real market data)"
        alerts = Alerts(enabled=False)
    else:
        public = BinanceSpot()
        store = Store()
        feed = None
        label = ""
    alerts.store = store
    events = lambda text: store.add_event("market", "check", text)
    if feed is None:
        feed = MarketFeed(["BTCUSDT", "ETHUSDT"], public, poll_s=ecfg.get("rest_poll_seconds", 5), log=events)

    worker = gate = None
    if not args.fake_market:
        from .testnet import TestnetGate, TestnetTrader, TestnetWorker, make_adapter
        adapter = make_adapter()
        gate = TestnetGate(adapter, env.testnet_enabled())
        if gate.status != "off":
            def on_result(job, r):
                what = f"TESTNET {job['side']} {job['asset']} for {job['account']}: {r['status']}"
                if r["status"] == "filled":
                    what += (f" {r['qty']:.6f} at {r['price']:,.2f} (TESTNET price), slippage real "
                             f"{r['slippage_bps']:.1f} bp vs simulated {r['sim_slippage_bps']:.1f} bp")
                else:
                    what += f" ({r.get('reason', '')})"
                store.add_event(job["account"], "testnet", what)
            worker = TestnetWorker(TestnetTrader(adapter, gate, store), gate, on_result)

    engine = Engine(cfg, store, feed, CandleStore(public), public, wired, backtest=backtest,
                    testnet=worker, gate=gate, alerts=alerts, market_label=label)
    return engine, feed, worker


def main(argv=None):
    p = argparse.ArgumentParser(description="crypto-bot demo engine and live dashboard")
    p.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"))
    p.add_argument("--port", type=int, default=int(os.getenv("PORT", "8000")))
    p.add_argument("--fake-market", action="store_true", help="made-up prices for UI development")
    p.add_argument("--no-browser", action="store_true", help="do not open the dashboard in a browser")
    args = p.parse_args(argv)
    env.load()
    try:
        import uvicorn
    except ImportError:
        sys.exit("Missing packages. Run: python3 -m pip install -r requirements.txt")

    engine, feed, worker = build(args)
    stop = threading.Event()
    feed.start()
    threading.Thread(target=engine.run_forever, args=(stop,), name="engine", daemon=True).start()
    if worker:
        threading.Thread(target=worker.run_forever, args=(stop,), name="testnet", daemon=True).start()
    wired = ", ".join(engine.strategies) or "no strategies yet (they need a backtest result first)"
    print(f"crypto-bot demo engine: accounts HOLD_50_50 + {wired}")
    if engine.gate:
        print(engine.gate.message)
    url = f"http://{'localhost' if args.host in ('127.0.0.1', '0.0.0.0') else args.host}:{args.port}"
    print(f"Dashboard: {url}   (Ctrl+C to stop)")
    if not args.no_browser:
        threading.Thread(target=lambda: (time.sleep(1.5), webbrowser.open(url)), daemon=True).start()
    try:
        uvicorn.run(create_app(engine, os.getenv("DASHBOARD_PASSWORD") or None),
                    host=args.host, port=args.port, log_level="warning")
    finally:
        stop.set()
        feed.stop()


if __name__ == "__main__":
    main()
