"""
poller.py - Background polling loop running all 12 detection signals
"""
import asyncio
import time
import logging
import os
import json

import aiosqlite
import polymarket_client as client
import detector
import alerts
from database import DB_PATH

logger = logging.getLogger(__name__)

POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", 10))
MARKET_LIMIT  = 100
INSIDER_ALERT = int(os.getenv("INSIDER_SCORE_ALERT", 60))

seen_trade_ids: set = set()
# market_id -> list of recent signal alerts (for composite scoring)
market_active_signals: dict = {}


async def run_forever():
    logger.info(f"Poller started (interval={POLL_INTERVAL}s, 12-signal engine)")
    while True:
        try:
            await poll_cycle()
        except Exception as e:
            logger.error(f"Poll cycle error: {e}", exc_info=True)
        await asyncio.sleep(POLL_INTERVAL)


async def poll_cycle():
    markets = await client.get_active_markets(limit=MARKET_LIMIT)
    if not markets:
        logger.warning("No markets returned")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        for m in markets:
            await _process_market(db, m)
        await db.commit()


async def _process_market(db, raw):
    mid      = raw.get("id") or raw.get("conditionId") or ""
    question = raw.get("question", "")
    category = raw.get("category") or ""
    end_date = raw.get("endDate") or raw.get("endDateIso") or ""
    if not mid:
        return

    prob = _parse_prob(raw)
    slug = raw.get("slug") or raw.get("groupSlug") or mid
    url  = f"https://polymarket.com/event/{slug}"
    vol24 = float(raw.get("volume24hr") or raw.get("volume") or 0)

    # Upsert market
    await db.execute("""
        INSERT INTO markets (id, question, category, end_date, active, last_prob, volume_24h, last_updated, polymarket_url)
        VALUES (?,?,?,?,1,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET last_prob=excluded.last_prob,
            volume_24h=excluded.volume_24h, last_updated=excluded.last_updated, active=1
    """, (mid, question, category, end_date, prob, vol24, int(time.time()), url))

    market_info = {"id": mid, "question": question, "polymarket_url": url}
    fired = []  # signals fired this cycle for this market

    if prob is not None:
        detector.record_prob(mid, prob)
        detector.record_volume(mid, vol24)
        detector.update_market_cache(mid, prob, category, question)

        await db.execute("INSERT INTO prob_history (market_id, timestamp, prob) VALUES (?,?,?)",
                         (mid, int(time.time()), prob))

        # Signal 2: Price Shock
        sig = detector.check_price_shock(mid, prob)
        if sig:
            fired.append(sig)
            await _save_and_notify(db, sig, market_info)

        # Signal 2b: Follow-Through (post-shock)
        sig = detector.check_follow_through(mid, prob)
        if sig:
            fired.append(sig)
            await _save_and_notify(db, sig, market_info)

        # Signal 6: Time Decay Drift
        sig = detector.check_time_decay(mid, prob, end_date)
        if sig:
            fired.append(sig)
            await _save_and_notify(db, sig, market_info)

        # Signal 7: Snapback
        sig = detector.check_snapback(mid, prob)
        if sig:
            fired.append(sig)
            await _save_and_notify(db, sig, market_info)

        # Signal 10: Volume Regime
        sig = detector.check_vol_regime(mid, vol24)
        if sig:
            fired.append(sig)
            await _save_and_notify(db, sig, market_info)

        # Signal 11: Round Number / Extreme Prob
        sig = detector.check_resolution_edge(mid, prob, question)
        if sig:
            fired.append(sig)
            await _save_and_notify(db, sig, market_info)

        # Signal 12: Behavioral Bias
        sig = detector.check_behavioral_bias(mid, prob)
        if sig:
            fired.append(sig)
            await _save_and_notify(db, sig, market_info)

        # Signal 5: Arb violations across related markets
        for arb_sig in detector.check_arb_violations(mid):
            fired.append(arb_sig)
            await _save_and_notify(db, arb_sig, market_info)

        # Signal 9: Cross-venue (if external probs registered)
        sig = detector.check_cross_venue(mid, question, prob)
        if sig:
            fired.append(sig)
            await _save_and_notify(db, sig, market_info)

    # Fetch order book for book-based signals
    ob = None
    clob_ids = raw.get("clobTokenIds")
    if clob_ids:
        try:
            ids = json.loads(clob_ids) if isinstance(clob_ids, str) else clob_ids
            if ids:
                ob = await client.get_orderbook(ids[0])
        except Exception:
            pass

    if ob:
        # Signal 3: OB Imbalance
        sig = detector.check_ob_imbalance(mid, ob)
        if sig:
            fired.append(sig)
            await _save_and_notify(db, sig, market_info)

        # Signal 4: Spread Fragility
        sig = detector.check_spread_fragility(mid, ob)
        if sig:
            fired.append(sig)
            await _save_and_notify(db, sig, market_info)

    # Fetch & process recent trades
    last_trade_side = None
    trades = await client.get_recent_trades(mid, limit=30)
    for raw_trade in trades:
        t = client.parse_trade(raw_trade)
        if not t["id"] or t["id"] in seen_trade_ids:
            continue
        seen_trade_ids.add(t["id"])
        if len(seen_trade_ids) > 50_000:
            seen_trade_ids.clear()

        t["market_id"]   = mid
        t["prob_before"] = prob
        t["prob_after"]  = prob
        last_trade_side  = t["side"]

        try:
            await db.execute("""
                INSERT OR IGNORE INTO trades
                  (id, market_id, timestamp, side, outcome, price, size, usd_value, wallet, tx_hash)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """, (t["id"], mid, t["timestamp"], t["side"], t["outcome"],
                  t["price"], t["size"], t["usd_value"], t["wallet"], t["tx_hash"]))
        except Exception:
            pass

        if t["wallet"]:
            await _upsert_wallet(db, t)
            detector.record_wallet_market(t["wallet"], mid, t["usd_value"])

        # Signal 1: Impact Trade (needs order book)
        if ob:
            sig = detector.check_impact_trade(t, ob)
            if sig:
                fired.append(sig)
                await _save_and_notify(db, sig, market_info)

        # Signal 8: Smart Wallet (large trade by tracked wallet)
        if t["wallet"] and t["usd_value"] >= 500:
            async with db.execute("SELECT * FROM wallets WHERE address=?", (t["wallet"],)) as cur:
                wallet_rec = await cur.fetchone()
            wallet_dict = dict(wallet_rec) if wallet_rec else None
            sig = detector.check_smart_wallet(t["wallet"], mid, t["usd_value"], wallet_dict)
            if sig:
                fired.append(sig)
                await _save_and_notify(db, sig, market_info)

        # Legacy: large trade signal
        la = detector.check_large_trade(t)
        if la:
            fired.append(la)
            await _save_and_notify(db, la, market_info)

    # Re-check OB imbalance with trade flow confirmation
    if ob and last_trade_side:
        sig = detector.check_ob_imbalance(mid, ob, last_trade_side)
        if sig and sig not in fired:
            fired.append(sig)
            await _save_and_notify(db, sig, market_info)

    # Compute composite score if multiple signals fired
    if len(fired) >= 2:
        composite = detector.compute_composite_score(fired)
        if composite >= INSIDER_ALERT:
            combo_alert = {
                "alert_type":    "COMPOSITE",
                "market_id":     mid,
                "description":   f"Multiple signals ({len(fired)}): {', '.join(set(s['alert_type'] for s in fired))}",
                "insider_score": composite,
                "wallet":        None,
                "trade_id":      None,
                "prob_before":   None,
                "prob_after":    prob,
                "usd_value":     None,
                "timestamp":     int(time.time()),
                "extra":         json.dumps({"signal_count": len(fired)}),
            }
            await _save_and_notify(db, combo_alert, market_info)


def _parse_prob(raw):
    op = raw.get("outcomePrices")
    if op:
        try:
            prices = json.loads(op) if isinstance(op, str) else op
            return float(prices[0])
        except Exception:
            pass
    for field in ("lastTradePrice", "bestBid", "price"):
        v = raw.get(field)
        if v is not None:
            try: return float(v)
            except Exception: pass
    return None

async def _get_last_prob(db, market_id):
    async with db.execute("SELECT last_prob FROM markets WHERE id=?", (market_id,)) as cur:
        row = await cur.fetchone()
        return float(row[0]) if row and row[0] is not None else None

async def _upsert_wallet(db, trade):
    w = trade["wallet"]
    now = int(time.time())
    await db.execute("""
        INSERT INTO wallets (address, first_seen, last_seen, total_volume_usd, trade_count)
        VALUES (?,?,?,?,1)
        ON CONFLICT(address) DO UPDATE SET
            last_seen=?, total_volume_usd=total_volume_usd+?, trade_count=trade_count+1
    """, (w, now, now, trade["usd_value"], now, trade["usd_value"]))

async def _save_and_notify(db, alert, market):
    cutoff = int(time.time()) - 300
    async with db.execute("""
        SELECT id FROM whale_alerts WHERE market_id=? AND alert_type=? AND timestamp>? LIMIT 1
    """, (alert["market_id"], alert["alert_type"], cutoff)) as cur:
        if await cur.fetchone():
            return

    await db.execute("""
        INSERT INTO whale_alerts
          (market_id, timestamp, alert_type, description, trade_id, wallet,
           prob_before, prob_after, usd_value, insider_score, notified)
        VALUES (?,?,?,?,?,?,?,?,?,?,0)
    """, (alert["market_id"], alert.get("timestamp", int(time.time())),
          alert["alert_type"], alert.get("description",""),
          alert.get("trade_id"), alert.get("wallet"),
          alert.get("prob_before"), alert.get("prob_after"),
          alert.get("usd_value"), alert.get("insider_score", 0)))

    if alert.get("insider_score", 0) >= INSIDER_ALERT:
        await alerts.send_alert(alert, market)
        await db.execute(
            "UPDATE whale_alerts SET notified=1 WHERE market_id=? AND alert_type=? AND timestamp=?",
            (alert["market_id"], alert["alert_type"], alert.get("timestamp", int(time.time()))))
