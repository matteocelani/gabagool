import os
import asyncio
import logging
import requests

def send_telegram_sync(message: str):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message, "parse_mode": "HTML"}
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        logging.getLogger("telegram").error("Failed to send telegram msg: %s", e)

async def send_telegram_async(message: str):
    await asyncio.to_thread(send_telegram_sync, message)
