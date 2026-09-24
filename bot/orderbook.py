"""Market-order fill simulation against a real order book snapshot.

A demo MARKET order is filled by walking the live book level by level (asks
for a buy, bids for a sell), exactly like the exchange matching engine would,
then the taker fee is applied. Slippage is measured against the mid price, so
it includes half the spread plus any depth walked.

Fees follow Binance spot: a buyer pays the fee out of the coins received, a
seller pays it out of the USDT received. fee_usd is always reported in USDT.
"""


def mid_price(book):
    if not book["bids"] or not book["asks"]:
        return None
    return (book["bids"][0][0] + book["asks"][0][0]) / 2


def simulate_market_order(book, side, fee_rate, quote_qty=None, base_qty=None):
    """book: {"bids": [(price, qty), ...best first], "asks": [...]}.

    BUY: pass quote_qty (USDT to spend) or base_qty. SELL: pass base_qty.
    Returns a dict with qty (coins matched), notional (USDT matched), avg_price,
    mid, fee_usd, qty_net (coins received after fee, buys), proceeds_net (USDT
    received after fee, sells), slippage_bps (cost vs mid, positive = worse),
    levels walked and complete (False if the book ran out).
    """
    side = side.upper()
    if side not in ("BUY", "SELL"):
        raise ValueError("side must be BUY or SELL")
    if side == "SELL" and base_qty is None:
        raise ValueError("a sell needs base_qty")
    if (quote_qty is None) == (base_qty is None):
        raise ValueError("pass exactly one of quote_qty or base_qty")
    levels = book["asks"] if side == "BUY" else book["bids"]
    mid = mid_price(book)
    qty = notional = 0.0
    used = 0
    rem_quote, rem_base = quote_qty, base_qty
    for price, size in levels:
        if price <= 0 or size <= 0:
            continue
        if rem_quote is not None:
            if rem_quote <= 1e-12:
                break
            take = min(size, rem_quote / price)
            rem_quote -= take * price
        else:
            if rem_base <= 1e-15:
                break
            take = min(size, rem_base)
            rem_base -= take
        qty += take
        notional += take * price
        used += 1
    remaining = rem_quote if rem_quote is not None else rem_base
    complete = remaining is not None and remaining <= (1e-9 if rem_quote is not None else 1e-12)
    avg = notional / qty if qty else 0.0
    fee_usd = notional * fee_rate
    if mid and avg:
        slip = (avg - mid) / mid if side == "BUY" else (mid - avg) / mid
    else:
        slip = 0.0
    return {
        "side": side, "qty": qty, "notional": notional, "avg_price": avg, "mid": mid,
        "fee_usd": fee_usd, "fee_rate": fee_rate,
        "qty_net": qty * (1 - fee_rate) if side == "BUY" else qty,
        "proceeds_net": notional * (1 - fee_rate) if side == "SELL" else 0.0,
        "slippage_bps": slip * 1e4, "levels": used, "complete": bool(complete and qty > 0),
    }
