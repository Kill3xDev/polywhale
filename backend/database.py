"""
database.py - SQLite schema and connection management
"""
import aiosqlite
import logging

DB_PATH = "polywhale.db"
logger = logging.getLogger(__name__)

CREATE_TABLES = """
-- Markets we're actively monitoring
CREATE TABLE IF NOT EXISTS markets (
    id TEXT PRIMARY KEY,
    question TEXT NOT NULL,
    category TEXT,
    end_date TEXT,
    active INTEGER DEFAULT 1,
    last_prob REAL,           -- latest YES probability (0-1)
    volume_24h REAL,
    last_updated INTEGER,     -- unix timestamp
    polymarket_url TEXT
);

-- Individual trades captured from the API
CREATE TABLE IF NOT EXISTS trades (
    id TEXT PRIMARY KEY,
    market_id TEXT NOT NULL,
    timestamp INTEGER NOT NULL,
    side TEXT,                -- BUY or SELL
    outcome TEXT,             -- YES or NO
    price REAL,               -- price = implied prob
    size REAL,                -- shares
    usd_value REAL,           -- size * price
    wallet TEXT,
    tx_hash TEXT,
    FOREIGN KEY (market_id) REFERENCES markets(id)
);

-- Whale alert events
CREATE TABLE IF NOT EXISTS whale_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id TEXT NOT NULL,
    timestamp INTEGER NOT NULL,
    alert_type TEXT NOT NULL,     -- LARGE_TRADE | RAPID_SHIFT | IMBALANCE | CROSS_MARKET
    description TEXT,
    trade_id TEXT,
    wallet TEXT,
    prob_before REAL,
    prob_after REAL,
    usd_value REAL,
    insider_score INTEGER,
    notified INTEGER DEFAULT 0,   -- whether alert was sent
    FOREIGN KEY (market_id) REFERENCES markets(id)
);

-- Wallet tracking / leaderboard
CREATE TABLE IF NOT EXISTS wallets (
    address TEXT PRIMARY KEY,
    first_seen INTEGER,
    last_seen INTEGER,
    total_volume_usd REAL DEFAULT 0,
    trade_count INTEGER DEFAULT 0,
    markets_traded INTEGER DEFAULT 0,
    win_rate REAL,            -- estimated win rate
    total_pnl_usd REAL DEFAULT 0,
    watchlist INTEGER DEFAULT 0,  -- manually watchlisted
    tags TEXT                 -- JSON array of tags
);

-- Probability history for charting
CREATE TABLE IF NOT EXISTS prob_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id TEXT NOT NULL,
    timestamp INTEGER NOT NULL,
    prob REAL NOT NULL,
    volume REAL,
    FOREIGN KEY (market_id) REFERENCES markets(id)
);
"""

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(CREATE_TABLES)
        await db.commit()
    logger.info("Database initialized")

async def get_db():
    return aiosqlite.connect(DB_PATH)
