"""
alerts.py - Send notifications via Discord, Telegram, or log-only
"""
import os
import logging
import httpx
import asyncio

logger = logging.getLogger(__name__)

DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK_URL", "")
TELEGRAM_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT   = os.getenv("TELEGRAM_CHAT_ID", "")

def _short_wallet(w: str) -> str:
    if not w or len(w) < 10:
        return w or "unknown"
    return f"{w[:6]}…{w[-4:]}"

def _format_alert(alert: dict, market: dict) -> str:
    mname    = market.get("question", market.get("id", "Unknown Market"))
    url      = market.get("polymarket_url", "https://polymarket.com")
    before   = f"{alert.get('prob_before', 0)*100:.1f}%" if alert.get('prob_before') is not None else "N/A"
    after    = f"{alert.get('prob_after', 0)*100:.1f}%"  if alert.get('prob_after')  is not None else "N/A"
    wallet   = _short_wallet(alert.get("wallet") or "")
    usd      = f"${alert.get('usd_value', 0):,.0f}" if alert.get('usd_value') else ""
    score    = alert.get("insider_score", 0)
    atype    = alert.get("alert_type", "ALERT")
    reason   = alert.get("description", "")

    lines = [
        f"🐋 **WHALE ALERT — {atype}**",
        f"📊 Market: {mname}",
        f"🔗 {url}",
    ]
    if before != "N/A":
        lines.append(f"📈 Probability: {before} → {after}")
    if usd:
        lines.append(f"💰 Size: {usd}")
    if wallet:
        lines.append(f"👤 Wallet: `{wallet}`")
    lines.append(f"🧠 Insider Score: **{score}/100**")
    lines.append(f"⚠️ Reason: {reason}")
    return "\n".join(lines)

async def send_alert(alert: dict, market: dict):
    msg = _format_alert(alert, market)
    logger.info(f"ALERT [{alert.get('alert_type')}] {alert.get('description')} | score={alert.get('insider_score')}")

    tasks = []
    if DISCORD_WEBHOOK:
        tasks.append(_send_discord(msg))
    if TELEGRAM_TOKEN and TELEGRAM_CHAT:
        tasks.append(_send_telegram(msg))

    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)

async def _send_discord(msg: str):
    try:
        async with httpx.AsyncClient() as c:
            r = await c.post(DISCORD_WEBHOOK, json={"content": msg}, timeout=10)
            r.raise_for_status()
    except Exception as e:
        logger.error(f"Discord alert failed: {e}")

async def _send_telegram(msg: str):
    try:
        # Convert markdown bold to Telegram format
        tg_msg = msg.replace("**", "*")
        async with httpx.AsyncClient() as c:
            r = await c.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json={"chat_id": TELEGRAM_CHAT, "text": tg_msg, "parse_mode": "Markdown"},
                timeout=10,
            )
            r.raise_for_status()
    except Exception as e:
        logger.error(f"Telegram alert failed: {e}")
