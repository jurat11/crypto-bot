"""Number formatting shared by the engine and the strategy descriptions."""


def px(x):
    """Price for people: 84,253.61 / 2.6841 / 0.00001234 (memecoins have tiny prices)."""
    if x is None:
        return "-"
    if x >= 100:
        return f"{x:,.2f}"
    if x >= 1:
        return f"{x:,.4f}"
    if x < 1e-9:
        return f"{x:.3e}"
    return f"{float(f'{x:.6g}'):.12f}".rstrip("0").rstrip(".")  # 6 significant digits, no exponent


def level(x):
    """A reference level (SMA, 48h high...) with just enough digits."""
    if x is None:
        return "-"
    return f"{x:,.0f}" if x >= 1000 else px(x)


def qty_text(q):
    if q >= 1000:
        return f"{q:,.0f}"
    if q >= 1:
        return f"{q:.8g}"
    return f"{q:.10f}".rstrip("0").rstrip(".") or "0"
