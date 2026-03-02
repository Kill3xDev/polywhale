# 🐋 PolyWhale — Polymarket Whale & Insider Trade Tracker

Real-time detection of large trades, suspicious order flow, and insider-like
activity on Polymarket prediction markets.

---

## What It Does

| Signal | Description |
|--------|-------------|
| 🐋 **Large Trade** | Single trade > configurable USD threshold (default $5k) |
| ⚡ **Rapid Odds Shift** | Probability moves >8pp in <5 minutes |
| 📊 **Order Book Imbalance** | Bid/ask wall 3× larger than opposite side |
| 🕸️ **Cross-Market** | Same wallet bets in 3+ markets within 30 minutes |
| 🧠 **Insider Score 0–100** | Composite score from size, speed, wallet history, correlation |

Alerts fire to Discord / Telegram when score ≥ threshold (default 60).

---

## Quick Start (Local Dev)

### Prerequisites
- Python 3.11+
- Node 18+

### 1. Clone & configure

```bash
git clone <your-repo>
cd polywhale
cp .env.example .env
# Edit .env — at minimum, set DISCORD_WEBHOOK_URL or Telegram credentials
```

### 2. Backend

```bash
cd backend
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

Backend starts at **http://localhost:8000**  
API docs at **http://localhost:8000/docs**

### 3. Frontend

```bash
cd frontend
npm install
npm run dev
```

Dashboard at **http://localhost:3000**

---

## Docker (Production)

```bash
cp .env.example .env
# fill in .env
docker compose up -d
```

- Frontend: http://localhost:3000
- Backend API: http://localhost:8000

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `POLYMARKET_API_BASE` | `https://gamma-api.polymarket.com` | Gamma REST API |
| `POLYMARKET_CLOB_BASE` | `https://clob.polymarket.com` | CLOB order book API |
| `WHALE_TRADE_USD` | `5000` | USD threshold for large trade alerts |
| `RAPID_ODDS_SHIFT_PCT` | `8` | % probability shift to trigger rapid shift alert |
| `RAPID_ODDS_WINDOW_MIN` | `5` | Window in minutes for rapid shift detection |
| `ORDERBOOK_IMBALANCE_RATIO` | `3.0` | Bid/ask ratio to flag imbalance |
| `INSIDER_SCORE_ALERT` | `60` | Minimum score to send a notification |
| `DISCORD_WEBHOOK_URL` | *(empty)* | Discord webhook URL for alerts |
| `TELEGRAM_BOT_TOKEN` | *(empty)* | Telegram bot token |
| `TELEGRAM_CHAT_ID` | *(empty)* | Telegram chat ID to send alerts to |
| `POLL_INTERVAL` | `10` | Seconds between market polls |
| `HOST` | `0.0.0.0` | Backend bind host |
| `PORT` | `8000` | Backend port |

---

## API Reference

| Endpoint | Description |
|----------|-------------|
| `GET /api/markets` | All monitored markets with alert counts |
| `GET /api/markets/{id}/history` | Probability history (24h default) |
| `GET /api/alerts?min_score=0` | All whale alerts |
| `GET /api/trades?min_usd=0` | All ingested trades |
| `GET /api/wallets` | Wallet leaderboard by volume |
| `GET /api/wallets/{addr}` | Wallet detail + trade history |
| `POST /api/wallets/{addr}/watchlist?add=true` | Add/remove from watchlist |
| `GET /api/stats` | Dashboard stats summary |
| `WS /ws` | WebSocket for real-time alert push |

---

## Data Sources (All Public, No API Key Needed)

| Source | Endpoint | Used For |
|--------|----------|----------|
| Polymarket Gamma API | `gamma-api.polymarket.com` | Markets, trades, prices |
| Polymarket CLOB API | `clob.polymarket.com` | Order book depth |

---

## Architecture

```
polywhale/
├── backend/
│   ├── main.py              # FastAPI app, REST endpoints, WebSocket
│   ├── poller.py            # Background polling + ingestion loop
│   ├── detector.py          # All detection signals + insider scoring
│   ├── polymarket_client.py # Thin HTTP client for Polymarket APIs
│   ├── alerts.py            # Discord / Telegram notifications
│   ├── database.py          # SQLite schema + helpers
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── App.jsx          # Full dashboard (single-file React)
│   │   └── main.jsx
│   ├── index.html
│   └── package.json
├── docker-compose.yml
├── .env.example
└── README.md
```

---

## Extending

**Add a news feed for timing signal:**
Integrate a news API (GDELT, NewsAPI) and compare trade timestamps to
news publication times. This would activate the `timing_score` component
in `detector.py:compute_insider_score()`.

**Backtest wallet accuracy:**
Use `GET /api/wallets/{addr}` trade history. Cross-reference market resolution
via Gamma's `resolved` market endpoint to compute win rate.

**EV estimation:**
Compare whale's implied probability (their bet price) against the current market
probability to estimate edge: `ev = prob_whale - prob_market`.

---

## Notes

- No Polymarket API key required — all endpoints are public
- Polymarket CLOB order book requires the **token_id** (ERC-1155 token ID for YES/NO)
  which is available in the market object under `clobTokenIds`
- SQLite is sufficient for MVP; swap to Postgres for production scale
- The insider score is a heuristic starting point — tune thresholds in `.env`
