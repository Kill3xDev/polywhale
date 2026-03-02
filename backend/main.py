"""
main.py - FastAPI entry point with REST API and WebSocket for the dashboard
"""
import asyncio
import logging
import os
import time

import httpx
import aiosqlite
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

load_dotenv()

import polymarket_client as client
import poller
from database import DB_PATH, init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="PolyWhale — Polymarket Whale Tracker", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Connected WebSocket clients for live-push
ws_clients: list[WebSocket] = []

# ── Startup / Shutdown ─────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    await init_db()
    # Shared HTTP client
    http = httpx.AsyncClient(
        headers={"User-Agent": "PolyWhale/1.0"},
        follow_redirects=True,
    )
    client.set_client(http)
    # Start background poller
    asyncio.create_task(poller.run_forever())
    # Start WebSocket broadcaster
    asyncio.create_task(_broadcast_loop())
    logger.info("PolyWhale backend started")


# ── REST Endpoints ─────────────────────────────────────────────────────────────

@app.get("/api/markets")
async def get_markets(limit: int = 50, offset: int = 0):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT m.*,
              (SELECT COUNT(*) FROM whale_alerts a WHERE a.market_id=m.id) as alert_count,
              (SELECT MAX(insider_score) FROM whale_alerts a WHERE a.market_id=m.id) as max_score
            FROM markets m
            WHERE m.active=1
            ORDER BY m.volume_24h DESC
            LIMIT ? OFFSET ?
        """, (limit, offset)) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


@app.get("/api/markets/{market_id}/history")
async def get_prob_history(market_id: str, hours: int = 24):
    cutoff = int(time.time()) - hours * 3600
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT timestamp, prob FROM prob_history
            WHERE market_id=? AND timestamp>?
            ORDER BY timestamp ASC
        """, (market_id, cutoff)) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


@app.get("/api/alerts")
async def get_alerts(limit: int = 100, min_score: int = 0):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT a.*, m.question, m.polymarket_url
            FROM whale_alerts a
            LEFT JOIN markets m ON m.id = a.market_id
            WHERE a.insider_score >= ?
            ORDER BY a.timestamp DESC
            LIMIT ?
        """, (min_score, limit)) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


@app.get("/api/wallets")
async def get_wallets(limit: int = 50, watchlist_only: bool = False):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        where = "WHERE watchlist=1" if watchlist_only else ""
        async with db.execute(f"""
            SELECT * FROM wallets {where}
            ORDER BY total_volume_usd DESC
            LIMIT ?
        """, (limit,)) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


@app.get("/api/wallets/{address}")
async def get_wallet_detail(address: str):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM wallets WHERE address=?", (address,)) as cur:
            wallet = await cur.fetchone()
        async with db.execute("""
            SELECT t.*, m.question FROM trades t
            LEFT JOIN markets m ON m.id=t.market_id
            WHERE t.wallet=?
            ORDER BY t.timestamp DESC LIMIT 50
        """, (address,)) as cur:
            trades = await cur.fetchall()
    return {
        "wallet": dict(wallet) if wallet else None,
        "trades": [dict(t) for t in trades],
    }


@app.post("/api/wallets/{address}/watchlist")
async def toggle_watchlist(address: str, add: bool = True):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE wallets SET watchlist=? WHERE address=?",
            (1 if add else 0, address)
        )
        await db.commit()
    return {"address": address, "watchlist": add}


@app.get("/api/trades")
async def get_trades(limit: int = 100, min_usd: float = 0, market_id: str = None):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        where_parts = ["t.usd_value >= ?"]
        params: list = [min_usd]
        if market_id:
            where_parts.append("t.market_id=?")
            params.append(market_id)
        where = "WHERE " + " AND ".join(where_parts)
        async with db.execute(f"""
            SELECT t.*, m.question, m.polymarket_url FROM trades t
            LEFT JOIN markets m ON m.id=t.market_id
            {where}
            ORDER BY t.usd_value DESC, t.timestamp DESC
            LIMIT ?
        """, (*params, limit)) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


@app.get("/api/stats")
async def get_stats():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM markets WHERE active=1") as c:
            market_count = (await c.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM trades") as c:
            trade_count = (await c.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM whale_alerts") as c:
            alert_count = (await c.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM wallets") as c:
            wallet_count = (await c.fetchone())[0]
        async with db.execute(
            "SELECT COUNT(*) FROM whale_alerts WHERE timestamp > ?",
            (int(time.time()) - 3600,)
        ) as c:
            alerts_1h = (await c.fetchone())[0]
    return {
        "markets": market_count,
        "trades": trade_count,
        "alerts": alert_count,
        "wallets": wallet_count,
        "alerts_last_hour": alerts_1h,
    }


# ── WebSocket – live push ──────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    ws_clients.append(ws)
    try:
        while True:
            await ws.receive_text()  # keep alive
    except WebSocketDisconnect:
        ws_clients.remove(ws)


async def _broadcast_loop():
    """Push latest alerts to all connected WS clients every 5 seconds."""
    while True:
        await asyncio.sleep(5)
        if not ws_clients:
            continue
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute("""
                    SELECT a.*, m.question, m.polymarket_url
                    FROM whale_alerts a
                    LEFT JOIN markets m ON m.id=a.market_id
                    WHERE a.timestamp > ?
                    ORDER BY a.timestamp DESC LIMIT 10
                """, (int(time.time()) - 10,)) as cur:
                    rows = await cur.fetchall()
            if rows:
                import json
                data = json.dumps([dict(r) for r in rows])
                dead = []
                for ws in ws_clients:
                    try:
                        await ws.send_text(data)
                    except Exception:
                        dead.append(ws)
                for d in dead:
                    ws_clients.remove(d)
        except Exception as e:
            logger.error(f"Broadcast error: {e}")


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", 8000)),
        reload=False,
    )
