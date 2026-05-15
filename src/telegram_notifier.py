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
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        logging.getLogger("telegram").error("Failed to send telegram msg: %s", e)


async def send_telegram_async(message: str):
    await asyncio.to_thread(send_telegram_sync, message)


# ─── Polymarket profile link helper ──────────────────────────────────────────

def _profile_line() -> str:
    """Return a 'Profile: <url>' line for the configured safe address, or ''."""
    addr = os.environ.get("POLY_SAFE_ADDRESS", "").strip()
    if not addr:
        return ""
    url = f"https://polymarket.com/it/profile/{addr}"
    return f"👤 <b>Profile:</b> <a href=\"{url}\">{addr[:10]}…</a>"


def _resolve_market_info(market_id: str) -> tuple:
    """Resolve a Polymarket internal market id to (event_url, question_text).

    Hits Gamma API. Returns ('', '') on any failure.
    Sync function — run it via asyncio.to_thread() from coroutines.
    """
    if not market_id:
        return "", ""
    try:
        r = requests.get(
            f"https://gamma-api.polymarket.com/markets/{market_id}",
            timeout=3,
        )
        if r.status_code != 200:
            return "", ""
        data = r.json()
        slug = data.get("slug") or ""
        question = data.get("question") or ""
        url = f"https://polymarket.com/it/event/{slug}" if slug else ""
        return url, question
    except Exception:
        return "", ""


def _market_line(market_id: str, market_url: str, market_question: str = "") -> str:
    """Build the '🎯 Market' line, preferring readable question text + URL."""
    if market_question:
        label = market_question if len(market_question) <= 60 else market_question[:57] + "..."
    elif market_id:
        label = f"{market_id[:20]}..."
    else:
        label = "?"
    if market_url:
        return f"🎯 <b>Market:</b> <a href=\"{market_url}\">{label}</a>"
    return f"🎯 <b>Market:</b> <code>{label}</code>"


def _interpret_order_error(error_msg: str) -> str:
    """Return a one-line, plain-English explanation for the given CLOB error.

    Returns '' if the error doesn't match any known pattern — in that case the
    notification will just show the raw error message without padding.
    """
    if not error_msg:
        return ""
    e = error_msg.lower()

    if "cancel-only" in e or "cancel only" in e:
        return (
            "Polymarket is in <b>cancel-only mode</b> (maintenance). "
            "New orders are temporarily blocked; the bot will resume "
            "automatically when trading reopens."
        )
    if "not enough" in e and "balance" in e:
        return "Funder wallet has not enough pUSD to fill this order."
    if "insufficient balance" in e or "insufficient funds" in e:
        return "Funder wallet has not enough pUSD to fill this order."
    if "invalid tick" in e or "tick size" in e:
        return "Order price is not aligned to the market tick size (0.01 / 0.001 / 0.0001)."
    if "maker address not allowed" in e:
        return (
            "Backend rejected the maker address — wrong "
            "<code>signature_type</code> / <code>funder</code> combination for this account."
        )
    if "order signer address has to be the address of the api key" in e or (
        "signer" in e and "api key" in e
    ):
        return (
            "CLOB API key is bound to a different signer than the one in the order. "
            "Re-derive credentials with the right <code>signature_type</code> and <code>funder</code>."
        )
    if "orderbook does not exist" in e:
        return "This market has no active orderbook (probably closed or paused)."
    if "trading restricted" in e or "geo" in e:
        return "Trading restricted in your region. VPN required."
    if "503" in e and "service" in e:
        return "Polymarket service temporarily unavailable. Retry later."
    if "403" in e:
        return "Request blocked (likely geo-restriction). Check VPN."

    return ""


# ─── LIFECYCLE EVENTS ────────────────────────────────────────────────────────

async def send_bot_started(dry_run: bool, config_summary: str = ""):
    """Notify when the bot starts successfully and is connected."""
    mode = "🧪 DRY RUN" if dry_run else "🔴 LIVE TRADING"
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    config_line = f"\n📋 <b>Config:</b> {config_summary}" if config_summary else ""
    profile_line = _profile_line()
    profile_block = f"\n{profile_line}" if profile_line else ""
    msg = (
        f"🚀 <b>Gabagool Bot Started</b>\n\n"
        f"🕐 <b>Time:</b> {now}\n"
        f"⚙️ <b>Mode:</b> {mode}"
        f"{config_line}"
        f"{profile_block}\n\n"
        f"The bot is now scanning Polymarket for arbitrage opportunities "
        f"and placing GTC limit orders when a profitable spread is found."
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
    profile_line = _profile_line()
    profile_block = f"\n{profile_line}" if profile_line else ""
    market_url, market_question = await asyncio.to_thread(_resolve_market_info, market_id)
    market_line = _market_line(market_id, market_url, market_question)

    msg = (
        f"✅ <b>Arbitrage Executed!</b>\n\n"
        f"🕐 <b>Time:</b> {now}\n"
        f"{market_line}\n"
        f"📦 <b>Order type:</b> GTC Limit (YES + NO)\n"
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
        f"{profile_block}"
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
    """Notify when a YES or NO order placement fails (400, 403, 503, etc.)."""
    now = datetime.now().strftime("%H:%M:%S")
    profile_line = _profile_line()
    profile_block = f"\n\n{profile_line}" if profile_line else ""
    interpretation = _interpret_order_error(error_msg)
    interpretation_block = f"\n\nℹ️ {interpretation}" if interpretation else ""
    market_url, market_question = await asyncio.to_thread(_resolve_market_info, market_id)
    market_line = _market_line(market_id, market_url, market_question)
    msg = (
        f"❌ <b>Order Failed — {side} Side</b>\n\n"
        f"🕐 <b>Time:</b> {now}\n"
        f"{market_line}\n"
        f"📦 <b>Order type:</b> GTC Limit\n"
        f"📈 <b>YES Price:</b> ${yes_price:.4f}\n"
        f"📉 <b>NO Price:</b> ${no_price:.4f}\n"
        f"💰 <b>Missed Margin:</b> {(profit_margin * 100):.2f}%\n\n"
        f"⚠️ <b>Error:</b> <code>{error_msg}</code>"
        f"{interpretation_block}"
        f"{profile_block}"
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
    profile_line = _profile_line()
    profile_block = f"\n\n{profile_line}" if profile_line else ""

    status_emoji = "🟢" if consecutive_failures == 0 else "🟠"

    msg = (
        f"⏱️ <b>Bot Status Update</b>\n\n"
        f"{status_emoji} Bot is actively scanning markets (GTC limit orders).\n"
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
        f"{profile_block}"
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
