"""Optional Telegram message. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID
(create a bot with @BotFather, then message it once and read your chat id from
https://api.telegram.org/bot<TOKEN>/getUpdates). Silently skipped if unset."""
import json
import os
import urllib.request


def send(text):
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return False
    body = json.dumps({"chat_id": chat, "text": text[:4000]}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", body,
                                 {"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=15)
        return True
    except Exception as e:
        print(f"telegram failed: {e}")
        return False
