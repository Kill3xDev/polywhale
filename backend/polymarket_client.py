"""
polymarket_client.py - Fixed to use correct Polymarket API endpoints
  - gamma-api.polymarket.com  -> markets & events
  - data-api.polymarket.com   -> trades & activity
  - clob.polymarket.com       -> order book
"""
import httpx
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

GAMMA_BASE = "https://gamma-api.polymarket.com"
DATA_BASE  = "https://data-api.polymarket.com"
CLOB_BASE  = "https://clob.polymarket.com"

_client: Optional[httpx.AsyncClient] = None

def set_client(client: httpx.AsyncClient):
    global _client
    _client = client

async def _get(url: str, params: dict = None) -> dict | list | None:
    try:
        r = await _client.get(url, params=params, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.warning(f"GET {url} failed: {e}")
        return None

async def get_active_markets(limit=100, offset=0) -> list[dict]:
    data = await _get(f"{GAMMA_BASE}/markets", params={
        "active": "true",
        "closed": "false",
        "limit": limit,
        "offset": offset,
        "order": "volume24hr",
        "ascending": "false",
    })
    if data is None:
        return []
    return data if isinstance(data, list) else data.get("markets", [])

async def get_recent_trades(market_id: str, limit=50) -> list[dict]:
    """Fetch trades from data-api (correct endpoint)."""
    data = await _get(f"{DATA_BASE}/trades", params={
        "market": market_id,
        "limit": limit,
    })
    if data is None:
        return []
    return data if isinstance(data, list) else []

async def get_orderbook(token_id: str) -> dict | None:
    return await _get(f"{CLOB_BASE}/book", params={"token_id": token_id})

async def get_events(limit=50) -> list[dict]:
    data = await _get(f"{GAMMA_BASE}/events", params={
        "active": "true",
        "closed": "false",
        "limit": limit,
        "order": "volume",
        "ascending": "false",
    })
    if data is None:
        return []
    return data if isinstance(data, list) else []

async def get_wallet_trades(wallet: str, limit=200) -> list[dict]:
    data = await _get(f"{DATA_BASE}/activity", params={
        "user": wallet,
        "limit": limit,
    })
    if data is None:
        return []
    return data if isinstance(data, list) else []

def market_url(slug: str) -> str:
    return f"https://polymarket.com/event/{slug}"

def parse_trade(raw: dict) -> dict:
    price = float(raw.get("price", 0) or 0)
    size  = float(raw.get("size", 0) or raw.get("shares", 0) or 0)
    usd   = float(raw.get("usdcSize", 0) or raw.get("amount", 0) or (price * size))
    return {
        "id":        raw.get("id") or raw.get("transactionHash", ""),
        "market_id": raw.get("market", "") or raw.get("conditionId", ""),
        "timestamp": int(raw.get("timestamp", time.time()) or time.time()),
        "side":      raw.get("side", "BUY").upper(),
        "outcome":   raw.get("outcome", "YES"),
        "price":     price,
        "size":      size,
        "usd_value": usd,
        "wallet":    raw.get("proxyWallet") or raw.get("maker") or raw.get("user") or "",
        "tx_hash":   raw.get("transactionHash") or raw.get("txHash") or "",
    }
