"""Optional Telegram alerts: every trade, every risk block, and a daily 00:15 UTC leaderboard.

Uses bot/notify.py (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID). Messages go out from a
background thread so a slow Telegram never delays trading. Off when unset.
"""
import os
import queue
import threading
import time

from . import notify

DAILY_AT_MIN = 15  # 00:15 UTC
DAILY_WINDOW_MIN = 60  # if the engine was down at 00:15, skip that day rather than send late


class Alerts:
    def __init__(self, store=None, send=notify.send, enabled=None):
        if enabled is None:
            enabled = bool(os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"))
        self.enabled, self.store, self._send = enabled, store, send
        self._q = queue.Queue(maxsize=200)
        self._blocked = {}
        self.sent = []  # last messages, for tests and debugging
        if enabled:
            threading.Thread(target=self._loop, name="telegram", daemon=True).start()

    def _loop(self):
        while True:
            text = self._q.get()
            try:
                self._send(text)
            except Exception:
                pass

    def push(self, text):
        self.sent = (self.sent + [text])[-50:]
        if self.enabled:
            try:
                self._q.put_nowait(text)
            except queue.Full:
                pass

    def trade(self, account, venue, side, asset, qty, price, notional, fee, reason=""):
        self.push(f"{venue} {account}: {side} {qty:.6f} {asset} at {price:,.2f} "
                  f"(${notional:.2f}, fee ${fee:.4f}). {reason}".strip())

    def risk(self, account, reasons):
        """Alert when an account becomes blocked or its reasons change, and once when it clears."""
        key = tuple(reasons)
        prev = self._blocked.get(account, ())
        if key == prev:
            return
        self._blocked[account] = key
        if reasons:
            self.push(f"RISK BLOCK {account}: " + "; ".join(reasons) + ". Buys stopped, sells still allowed.")
        elif prev:
            self.push(f"Risk OK again for {account}.")

    def maybe_daily(self, now_ms, rows):
        t = time.gmtime(now_ms / 1000)
        minute = t.tm_hour * 60 + t.tm_min
        if not (DAILY_AT_MIN <= minute < DAILY_AT_MIN + DAILY_WINDOW_MIN):
            return False
        day = time.strftime("%Y-%m-%d", t)
        last = self.store.get_meta("alerts_last_daily") if self.store else getattr(self, "_last_daily", None)
        if last == day:
            return False
        lines = [f"Daily leaderboard {day} (demo, no real money)"]
        for i, r in enumerate(rows, 1):
            lines.append(f"{i}. {r['label']}: ${r['balance']:.2f} ({r['pnl_pct']:+.1%}), "
                         f"max DD {r['max_dd']:.1%}, {r['trades']} trades")
        self.push("\n".join(lines))
        if self.store:
            self.store.set_meta("alerts_last_daily", day)
        self._last_daily = day
        return True
