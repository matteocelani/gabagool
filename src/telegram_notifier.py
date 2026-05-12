import os
import asyncio
import logging
import requests
from typing import Dict, Any
from datetime import datetime


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


# ─── LIFECYCLE EVENTS ────────────────────────────────────────────────────────

async def send_bot_started(dry_run: bool, config_summary: str = ""):
    """Notify when the bot starts successfully and is connected."""
    mode = "🧪 DRY RUN" if dry_run else "🔴 LIVE TRADING"
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    config_line = f"\n📋 <b>Config:</b> {config_summary}" if config_summary else ""
    msg = (
        f"🚀 <b>Gabagool Bot Started</b>\n\n"
        f"🕐 <b>Time:</b> {now}\n"
        f"⚙️ <b>Mode:</b> {mode}"
        f"{config_line}\n\n"
        f"The bot is now scanning Polymarket for arbitrage opportunities."
    )
    await send_telegram_async(msg)


async def send_bot_stopped(reason: str = "Shutdown signal received"):
    """Notify when the bot stops for any reason."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    msg = (
        f"🛑 <b>Gabagool Bot Stopped</b>\n\n"
        f"🕐 <b>Time:</b> {now}\n"
        f"📋 <b>Reason:</b> <code>{reason}</code>\n\n"
        f"The bot is no longer scanning markets."
    )
    await send_telegram_async(msg)


# ─── TRADE EVENTS ─────────────────────────────────────────────────────────────

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
    """Notify when an arbitrage trade is successfully executed on both sides."""
    tot_volume = stats.get("total_volume", 0.0)
    tot_profit = stats.get("total_profit", 0.0)
    win_rate = stats.get("win_rate", 0.0)
    completed = stats.get("completed_trades", 0)
    now = datetime.now().strftime("%H:%M:%S")

    msg = (
        f"✅ <b>Arbitrage Executed!</b>\n\n"
        f"🕐 <b>Time:</b> {now}\n"
        f"🎯 <b>Market:</b> <code>{market_id[:20]}...</code>\n"
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


async def send_order_failed(
    market_id: str,
    side: str,
    error_msg: str,
    yes_price: float = 0.0,
    no_price: float = 0.0,
    profit_margin: float = 0.0
):
    """Notify when a YES or NO order placement fails (400, 403, etc.)."""
    now = datetime.now().strftime("%H:%M:%S")
    msg = (
        f"❌ <b>Order Failed — {side} Side</b>\n\n"
        f"🕐 <b>Time:</b> {now}\n"
        f"🎯 <b>Market:</b> <code>{market_id[:20]}...</code>\n"
        f"📈 <b>YES Price:</b> ${yes_price:.4f}\n"
        f"📉 <b>NO Price:</b> ${no_price:.4f}\n"
        f"💰 <b>Missed Margin:</b> {(profit_margin * 100):.2f}%\n\n"
        f"⚠️ <b>Error:</b> <code>{error_msg}</code>\n\n"
        f"The opportunity was found but the order was rejected. "
        f"Possible causes: geo-block (403), bad params (400), or insufficient funds."
    )
    await send_telegram_async(msg)


# ─── STATUS & ERRORS ──────────────────────────────────────────────────────────

async def send_status_update(
    curr_exposure: float,
    max_exposure: float,
    stats: Dict[str, Any],
    consecutive_failures: int = 0
):
    """Send a scheduled periodic status report (00:00, 08:00, 16:00)."""
    tot_volume = stats.get("total_volume", 0.0)
    tot_profit = stats.get("total_profit", 0.0)
    win_rate = stats.get("win_rate", 0.0)
    completed = stats.get("completed_trades", 0)
    pending = stats.get("pending_trades", 0)
    failed = stats.get("failed_trades", 0)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    status_emoji = "🟢" if consecutive_failures == 0 else "🟠"

    msg = (
        f"⏱️ <b>Bot Status Update</b>\n\n"
        f"{status_emoji} Bot is actively scanning markets.\n"
        f"🕐 <b>Report Time:</b> {now}\n"
        f"⚠️ <b>Consecutive Failures:</b> {consecutive_failures}\n\n"
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
    """Send an immediate error alert for critical failures."""
    now = datetime.now().strftime("%H:%M:%S")
    msg = (
        f"⚠️ <b>BOT ERROR ALERT</b>\n\n"
        f"🕐 <b>Time:</b> {now}\n"
        f"<b>Context:</b> {context}\n"
        f"<b>Error:</b> <code>{error_message}</code>\n\n"
        f"Please check the server logs immediately."
    )
    await send_telegram_async(msg)
