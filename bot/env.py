"""Tiny .env loader (standard library only).

Reads KEY=VALUE lines from .env into os.environ without overriding variables
that are already set. It never prints or logs values: secrets such as
TESTNET_API_SECRET or TELEGRAM_BOT_TOKEN only live in os.environ.
"""
import os


def load(path=".env"):
    if not os.path.exists(path):
        return False
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if key.startswith("export "):
                key = key[len("export "):].strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    return True


def flag(name, default=False):
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def testnet_enabled():
    return flag("TESTNET")


def testnet_keys():
    """(api_key, api_secret) or (None, None). Callers must never print these."""
    k, s = os.getenv("TESTNET_API_KEY"), os.getenv("TESTNET_API_SECRET")
    return (k, s) if k and s else (None, None)
