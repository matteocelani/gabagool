import os
import asyncio
import logging
import requests
from typing import Dict, Any

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

async def send_arbitrage_executed(
    market_id: str, 
    yes_price: float, 
    no_price: float, 
    profit_margin: float, 
    trade_size: float, 
    curr_exposure: float, 
    max_exposure: float, 
    stats: Dict[str, Any]
):
    tot_volume = stats.get("total_volume", 0.0)
    tot_profit = stats.get("total_profit", 0.0)
    win_rate = stats.get("win_rate", 0.0)
    completed = stats.get("completed_trades", 0)

    msg = (
        f"🚨 <b>Arbitrage Executed!</b>\n\n"
        f"🎯 <b>Market:</b> <code>{market_id[:16]}...</code>\n"
        f"📈 <b>Cost YES:</b> ${yes_price:.4f}\n"
        f"📉 <b>Cost NO:</b> ${no_price:.4f}\n"
        f"💵 <b>Combined Cost:</b> ${(yes_price + no_price):.4f}\n"
        f"💰 <b>Est. Profit Margin:</b> {(profit_margin * 100):.2f}%\n"
        f"📊 <b>Position Size:</b> ${trade_size * 2:.2f} total\n\n"
        f"🏦 <b>Account Status:</b>\n"
        f"• Exposure: ${curr_exposure:.2f} / ${max_exposure:.2f}\n"
        f"• Total Vol Traded: ${tot_volume:.2f}\n"
        f"• Total Net Profit: ${tot_profit:.2f}\n"
        f"• Trades Completed: {completed}\n"
        f"• Win Rate: {(win_rate * 100):.1f}%"
    )
    await send_telegram_async(msg)

async def send_status_update(curr_exposure: float, max_exposure: float, stats: Dict[str, Any], consecutive_failures: int = 0):
    tot_volume = stats.get("total_volume", 0.0)
    tot_profit = stats.get("total_profit", 0.0)
    win_rate = stats.get("win_rate", 0.0)
    completed = stats.get("completed_trades", 0)
    pending = stats.get("pending_trades", 0)
    failed = stats.get("failed_trades", 0)
    
    status_emoji = "🟢" if consecutive_failures == 0 else "🟠"

    msg = (
        f"⏱️ <b>Bot Status Update (8h)</b>\n\n"
        f"{status_emoji} The bot is actively scanning markets.\n"
        f"Consecutive Failures: {consecutive_failures}\n\n"
        f"🏦 <b>Account Summary:</b>\n"
        f"• Active Exposure: ${curr_exposure:.2f} / ${max_exposure:.2f}\n"
        f"• Pending Trades: {pending}\n"
        f"• Trades Completed: {completed}\n"
        f"• Trades Failed: {failed}\n"
        f"• Total Vol Traded: ${tot_volume:.2f}\n"
        f"• Total Net Profit: ${tot_profit:.2f}\n"
        f"• Win Rate: {(win_rate * 100):.1f}%"
    )
    await send_telegram_async(msg)

async def send_error_alert(error_message: str, context: str = ""):
    msg = (
        f"⚠️ <b>BOT ERROR ALERT</b>\n\n"
        f"<b>Context:</b> {context}\n"
        f"<b>Error:</b> <code>{error_message}</code>\n\n"
        f"Please check the server logs."
    )
    await send_telegram_async(msg)
