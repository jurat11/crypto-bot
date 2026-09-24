"""SQLite store (data/bot.db): accounts, equity curve, receipts, fills, events,
and the testnet order ledger that makes client order ids single-use."""
import json
import os
import sqlite3
import threading
import time

DB_PATH = "data/bot.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS accounts (name TEXT PRIMARY KEY, state TEXT NOT NULL, updated_ms INTEGER);
CREATE TABLE IF NOT EXISTS equity (account TEXT, ts INTEGER, equity REAL, PRIMARY KEY (account, ts));
CREATE TABLE IF NOT EXISTS receipts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, account TEXT, asset TEXT, candle_ms INTEGER,
  inputs TEXT, signal TEXT, risk TEXT, orders TEXT,
  fill_price REAL, fee REAL, slippage_bps REAL, note TEXT);
CREATE TABLE IF NOT EXISTS fills (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, account TEXT, venue TEXT, asset TEXT, side TEXT,
  qty REAL, price REAL, notional REAL, fee REAL, mid REAL, slippage_bps REAL, sim_slippage_bps REAL,
  client_order_id TEXT, status TEXT, reason TEXT);
CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, account TEXT, kind TEXT, text TEXT);
CREATE TABLE IF NOT EXISTS testnet_orders (
  client_order_id TEXT PRIMARY KEY, ts INTEGER, account TEXT, asset TEXT, side TEXT,
  request TEXT, status TEXT, response TEXT);
CREATE INDEX IF NOT EXISTS equity_ts ON equity (ts);
CREATE INDEX IF NOT EXISTS fills_ts ON fills (ts);
"""


def now_ms():
    return int(time.time() * 1000)


class Store:
    def __init__(self, path=DB_PATH):
        if path != ":memory:":
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.path = path
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        if path != ":memory:":
            self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)
        self._db.commit()

    def _exec(self, sql, args=()):
        with self._lock:
            cur = self._db.execute(sql, args)
            self._db.commit()
            return cur

    def _all(self, sql, args=()):
        with self._lock:
            return [dict(r) for r in self._db.execute(sql, args).fetchall()]

    def close(self):
        with self._lock:
            self._db.close()

    # meta
    def get_meta(self, key, default=None):
        rows = self._all("SELECT value FROM meta WHERE key=?", (key,))
        return json.loads(rows[0]["value"]) if rows else default

    def set_meta(self, key, value):
        self._exec("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, json.dumps(value)))

    # accounts
    def load_accounts(self):
        return {r["name"]: json.loads(r["state"]) for r in self._all("SELECT name, state FROM accounts")}

    def save_account(self, name, state):
        self._exec("INSERT OR REPLACE INTO accounts (name, state, updated_ms) VALUES (?, ?, ?)",
                   (name, json.dumps(state), now_ms()))

    # equity curve (one point per account per minute)
    def add_equity(self, ts, points):
        with self._lock:
            self._db.executemany("INSERT OR REPLACE INTO equity (account, ts, equity) VALUES (?, ?, ?)",
                                 [(a, int(ts), float(e)) for a, e in points.items()])
            self._db.commit()

    def equity_history(self, since_ms=0, max_points=1500):
        rows = self._all("SELECT account, ts, equity FROM equity WHERE ts >= ? ORDER BY ts", (since_ms,))
        out = {}
        for r in rows:
            out.setdefault(r["account"], []).append((r["ts"], r["equity"]))
        for a, pts in out.items():
            if len(pts) > max_points:  # keep shape, thin evenly, always keep the last point
                step = len(pts) / max_points
                thinned = [pts[int(i * step)] for i in range(max_points - 1)] + [pts[-1]]
                out[a] = thinned
        return out

    # receipts
    def add_receipt(self, account, asset, candle_ms, inputs, signal, risk, orders,
                    fill_price=None, fee=None, slippage_bps=None, note="", ts=None):
        self._exec(
            "INSERT INTO receipts (ts, account, asset, candle_ms, inputs, signal, risk, orders, fill_price, fee, "
            "slippage_bps, note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (ts or now_ms(), account, asset, candle_ms, json.dumps(inputs), json.dumps(signal), json.dumps(risk),
             json.dumps(orders), fill_price, fee, slippage_bps, note))

    def receipts(self, limit=100, account=None):
        if account:
            return self._all("SELECT * FROM receipts WHERE account=? ORDER BY id DESC LIMIT ?", (account, limit))
        return self._all("SELECT * FROM receipts ORDER BY id DESC LIMIT ?", (limit,))

    # fills (demo and testnet)
    def add_fill(self, **f):
        cols = ["ts", "account", "venue", "asset", "side", "qty", "price", "notional", "fee", "mid",
                "slippage_bps", "sim_slippage_bps", "client_order_id", "status", "reason"]
        f.setdefault("ts", now_ms())
        self._exec(f"INSERT INTO fills ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                   tuple(f.get(c) for c in cols))

    def recent_fills(self, limit=50):
        return self._all("SELECT * FROM fills ORDER BY id DESC LIMIT ?", (limit,))

    # activity feed
    def add_event(self, account, kind, text, ts=None):
        cur = self._exec("INSERT INTO events (ts, account, kind, text) VALUES (?, ?, ?, ?)",
                         (ts or now_ms(), account, kind, text))
        return cur.lastrowid

    def recent_events(self, limit=100):
        return self._all("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))

    # testnet orders: a client order id can be claimed exactly once
    def claim_client_order_id(self, cid, account, asset, side, request):
        cur = self._exec(
            "INSERT OR IGNORE INTO testnet_orders (client_order_id, ts, account, asset, side, request, status) "
            "VALUES (?, ?, ?, ?, ?, ?, 'sending')", (cid, now_ms(), account, asset, side, json.dumps(request)))
        return cur.rowcount == 1

    def update_testnet_order(self, cid, status, response=None):
        self._exec("UPDATE testnet_orders SET status=?, response=? WHERE client_order_id=?",
                   (status, json.dumps(response) if response is not None else None, cid))

    def testnet_order(self, cid):
        rows = self._all("SELECT * FROM testnet_orders WHERE client_order_id=?", (cid,))
        return rows[0] if rows else None
