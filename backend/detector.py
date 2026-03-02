"""
detector.py - Advanced whale / informed trading detection engine
12 detection signals with composite scoring
"""
import time
import math
import logging
import os
import statistics
from collections import defaultdict, deque
from typing import Optional

logger = logging.getLogger(__name__)

WHALE_USD            = float(os.getenv("WHALE_TRADE_USD", 5000))
RAPID_SHIFT_PCT      = float(os.getenv("RAPID_ODDS_SHIFT_PCT", 8))
RAPID_WINDOW_MIN     = int(os.getenv("RAPID_ODDS_WINDOW_MIN", 5))
IMBALANCE_RATIO      = float(os.getenv("ORDERBOOK_IMBALANCE_RATIO", 2.5))
CROSS_MARKET_MIN_USD = float(os.getenv("CROSS_MARKET_MIN_USD", 500))
IMPACT_BOOK_PCT      = float(os.getenv("IMPACT_BOOK_PCT", 15))
SPREAD_THRESHOLD     = float(os.getenv("SPREAD_THRESHOLD", 5))
ARB_EPSILON          = float(os.getenv("ARB_EPSILON", 3))
VOL_ZSCORE           = float(os.getenv("VOL_ZSCORE", 2.5))
ROUND_NUM_TOLERANCE  = float(os.getenv("ROUND_NUM_TOLERANCE", 1.5))
FOLLOW_THROUGH_MIN   = int(os.getenv("FOLLOW_THROUGH_MIN", 15))
SMART_WALLET_HIT_RATE= float(os.getenv("SMART_WALLET_HIT_RATE", 0.62))

prob_snapshots:   dict = defaultdict(lambda: deque(maxlen=200))
volume_history:   dict = defaultdict(lambda: deque(maxlen=100))
book_snapshots:   dict = defaultdict(lambda: deque(maxlen=50))
wallet_recent:    dict = defaultdict(lambda: {"markets": set(), "ts": time.time(), "total_usd": 0.0})
pending_shocks:   dict = {}
market_vol_baseline: dict = defaultdict(lambda: deque(maxlen=60))
market_prob_cache:   dict = {}
external_probs:      dict = {}

ROUND_NUMBERS = [0.10, 0.20, 0.25, 0.33, 0.40, 0.50, 0.60, 0.67, 0.75, 0.80, 0.90]

SIGNAL_WEIGHTS = {
    "IMPACT_TRADE": 20, "PRICE_SHOCK": 15, "FOLLOW_THROUGH": 12,
    "OB_IMBALANCE": 10, "SPREAD_FRAGILITY": 5, "ARB_VIOLATION": 10,
    "TIME_DECAY_DRIFT": 8, "SNAPBACK": 6, "SMART_WALLET": 15,
    "CROSS_VENUE": 10, "VOL_REGIME": 6, "ROUND_NUM_ANCHOR": 4,
    "EXTREME_PROB": 3, "HEADLINE_OVERREACTION": 5,
    "LARGE_TRADE": 20, "RAPID_SHIFT": 15, "IMBALANCE": 10, "CROSS_MARKET": 8,
}

def _make_alert(alert_type, market_id, description, score,
                wallet=None, trade_id=None, prob_before=None, prob_after=None,
                usd_value=None, extra=None):
    import json as _j
    return {
        "alert_type": alert_type, "market_id": market_id,
        "description": description, "insider_score": min(100, max(0, score)),
        "wallet": wallet, "trade_id": trade_id,
        "prob_before": prob_before, "prob_after": prob_after,
        "usd_value": usd_value, "timestamp": int(time.time()),
        "extra": _j.dumps(extra or {}),
    }

def _score_trade_size(usd):
    if usd >= 100_000: return 90
    if usd >= 50_000:  return 75
    if usd >= 20_000:  return 60
    if usd >= 10_000:  return 45
    if usd >= 5_000:   return 30
    return 15

# ── Signal 1: Impact Trade ────────────────────────────────────────────────────
def check_impact_trade(trade, orderbook):
    if not orderbook or trade["usd_value"] < 1000:
        return None
    bids = orderbook.get("bids", [])
    asks = orderbook.get("asks", [])
    side_book = asks if trade["side"] == "BUY" else bids
    if not side_book:
        return None
    book_depth_usd = sum(float(l.get("size",0))*float(l.get("price",0.5)) for l in side_book[:20])
    if book_depth_usd <= 0:
        return None
    impact_pct = (trade["usd_value"] / book_depth_usd) * 100
    if impact_pct < IMPACT_BOOK_PCT:
        return None
    levels = _count_levels_consumed(side_book, trade["usd_value"])
    score = min(100, int(_score_trade_size(trade["usd_value"])*0.4 + min(40, impact_pct*0.8) + min(20, levels*3)))
    return _make_alert("IMPACT_TRADE", trade["market_id"],
        f"Impact trade: ${trade['usd_value']:,.0f} = {impact_pct:.0f}% of book depth, walked {levels} levels",
        score, wallet=trade["wallet"], trade_id=trade["id"], usd_value=trade["usd_value"],
        extra={"impact_pct": round(impact_pct,1), "levels_walked": levels, "book_depth_usd": round(book_depth_usd,0)})

def _count_levels_consumed(book_side, usd_spent):
    remaining = usd_spent
    levels = 0
    for level in book_side:
        if remaining <= 0: break
        remaining -= float(level.get("size",0)) * float(level.get("price",0.5))
        levels += 1
    return levels

# ── Signal 2: Price Shock + Follow-Through ────────────────────────────────────
def record_prob(market_id, prob):
    prob_snapshots[market_id].append((time.time(), prob))

def check_price_shock(market_id, current_prob):
    snaps = prob_snapshots[market_id]
    cutoff = time.time() - RAPID_WINDOW_MIN * 60
    baseline = None
    for ts, p in snaps:
        if ts >= cutoff:
            baseline = p
            break
    if baseline is None:
        return None
    shift = (current_prob - baseline) * 100
    abs_shift = abs(shift)
    if abs_shift < RAPID_SHIFT_PCT:
        return None
    direction = "UP" if shift > 0 else "DOWN"
    if market_id not in pending_shocks:
        pending_shocks[market_id] = {"shock_ts": time.time(), "shock_prob": baseline,
                                      "post_prob": current_prob, "direction": direction, "shift_pct": abs_shift}
    score = min(100, int(abs_shift * 5))
    return _make_alert("PRICE_SHOCK", market_id,
        f"Prob {direction} {abs_shift:.1f}pp in <{RAPID_WINDOW_MIN}min ({baseline*100:.1f}% → {current_prob*100:.1f}%)",
        score, prob_before=baseline, prob_after=current_prob,
        extra={"shift_pp": round(abs_shift,2), "direction": direction})

def check_follow_through(market_id, current_prob):
    shock = pending_shocks.get(market_id)
    if not shock:
        return None
    elapsed_min = (time.time() - shock["shock_ts"]) / 60
    if elapsed_min < FOLLOW_THROUGH_MIN:
        return None
    del pending_shocks[market_id]
    drift = (current_prob - shock["post_prob"]) * 100
    total_move = (current_prob - shock["shock_prob"]) * 100
    if abs(total_move) < 2:
        return None
    follow_through = drift * (1 if shock["direction"] == "UP" else -1)
    signal_type = "MOMENTUM" if follow_through > 1 else "MEAN_REVERSION"
    score = min(100, int(abs(total_move) * 4))
    return _make_alert("FOLLOW_THROUGH", market_id,
        f"Post-shock {signal_type}: total move {total_move:+.1f}pp over {elapsed_min:.0f}min",
        score, prob_before=shock["shock_prob"], prob_after=current_prob,
        extra={"signal_type": signal_type, "drift_pp": round(drift,2), "total_move_pp": round(total_move,2)})

# ── Signal 3: OB Imbalance ────────────────────────────────────────────────────
def check_ob_imbalance(market_id, orderbook, recent_trade_side=None):
    if not orderbook:
        return None
    bids = orderbook.get("bids", [])
    asks = orderbook.get("asks", [])
    if not bids or not asks:
        return None
    top_bid = sum(float(b.get("size",0)) for b in bids[:3])
    top_ask = sum(float(a.get("size",0)) for a in asks[:3])
    if top_ask == 0 or top_bid == 0:
        return None
    ratio = top_bid/top_ask if top_bid > top_ask else top_ask/top_bid
    dominant = "BID" if top_bid > top_ask else "ASK"
    if ratio < IMBALANCE_RATIO:
        return None
    flow_confirmed = (recent_trade_side == "BUY" and dominant == "BID") or \
                     (recent_trade_side == "SELL" and dominant == "ASK")
    score = min(100, int(ratio*12) + (15 if flow_confirmed else 0))
    return _make_alert("OB_IMBALANCE", market_id,
        f"Order book {dominant} pressure: {ratio:.1f}x imbalance near mid" +
        (" (confirmed by trade flow)" if flow_confirmed else ""),
        score, usd_value=top_bid if dominant=="BID" else top_ask,
        extra={"imbalance_ratio": round(ratio,2), "dominant_side": dominant, "flow_confirmed": flow_confirmed})

# ── Signal 4: Spread Fragility ────────────────────────────────────────────────
def check_spread_fragility(market_id, orderbook):
    if not orderbook:
        return None
    bids = orderbook.get("bids", [])
    asks = orderbook.get("asks", [])
    if not bids or not asks:
        return None
    best_bid = float(bids[0].get("price",0)) if bids else 0
    best_ask = float(asks[0].get("price",1)) if asks else 1
    if best_bid <= 0 or best_ask <= 0:
        return None
    mid = (best_bid + best_ask) / 2
    spread_pct = ((best_ask - best_bid) / mid) * 100 if mid > 0 else 0
    top_depth = float(bids[0].get("size",0))*best_bid + float(asks[0].get("size",0))*best_ask
    if spread_pct < SPREAD_THRESHOLD:
        return None
    score = min(100, int(spread_pct*4 + (30 if top_depth < 500 else 10)))
    return _make_alert("SPREAD_FRAGILITY", market_id,
        f"Fragile market: spread {spread_pct:.1f}%, top-of-book ${top_depth:,.0f}",
        score, extra={"spread_pct": round(spread_pct,2), "top_depth_usd": round(top_depth,0)})

# ── Signal 5: Arb Violation ───────────────────────────────────────────────────
def update_market_cache(market_id, prob, category, question):
    market_prob_cache[market_id] = {"prob": prob, "category": category, "question": question, "ts": time.time()}

def check_arb_violations(market_id):
    alerts_out = []
    target = market_prob_cache.get(market_id)
    if not target:
        return alerts_out
    same_cat = {mid: m for mid, m in market_prob_cache.items()
                if m["category"] == target["category"] and mid != market_id
                and time.time() - m["ts"] < 300}
    if not same_cat:
        return alerts_out
    probs = [target["prob"]] + [m["prob"] for m in same_cat.values()]
    if len(probs) >= 3:
        total = sum(probs) * 100
        if abs(total - 100) > ARB_EPSILON + 3:
            score = min(100, int(abs(total-100)*3))
            alerts_out.append(_make_alert("ARB_VIOLATION", market_id,
                f"Arb: {len(probs)} related probs sum to {total:.1f}% (gap {abs(total-100):.1f}pp)",
                score, extra={"prob_sum": round(total,1), "gap_pp": round(abs(total-100),1)}))
    return alerts_out

# ── Signal 6: Time Decay Drift ────────────────────────────────────────────────
def check_time_decay(market_id, current_prob, end_date_str):
    if not end_date_str:
        return None
    snaps = prob_snapshots[market_id]
    if len(snaps) < 10:
        return None
    cutoff = time.time() - 1800
    recent = [(ts, p) for ts, p in snaps if ts >= cutoff]
    if len(recent) < 5:
        return None
    probs_recent = [p for _, p in recent]
    drift = (probs_recent[-1] - probs_recent[0]) * 100
    if drift > 3 and current_prob < 0.4:
        score = min(100, int(drift*8))
        return _make_alert("TIME_DECAY_DRIFT", market_id,
            f"Suspicious: prob RISING +{drift:.1f}pp in 30min on low-prob dated market ({current_prob*100:.1f}%)",
            score, prob_before=probs_recent[0], prob_after=current_prob,
            extra={"drift_30min": round(drift,2), "current_prob": current_prob})
    return None

# ── Signal 7: Snapback / Mean Reversion ──────────────────────────────────────
def check_snapback(market_id, current_prob):
    snaps = list(prob_snapshots[market_id])
    if len(snaps) < 20:
        return None
    cutoff = time.time() - 1200
    recent = [(ts, p) for ts, p in snaps if ts >= cutoff]
    if len(recent) < 8:
        return None
    probs = [p for _, p in recent]
    max_p = max(probs)
    min_p = min(probs)
    swing = (max_p - min_p) * 100
    mid_idx = len(probs) // 2
    revert_down = probs[mid_idx] == max_p and (current_prob - max_p)*100 < -3 and swing > 5
    revert_up   = probs[mid_idx] == min_p and (current_prob - min_p)*100 > 3 and swing > 5
    if not (revert_down or revert_up):
        return None
    direction = "DOWN" if revert_down else "UP"
    revert_pct = abs((current_prob - max_p)*100 if revert_down else (current_prob - min_p)*100)
    score = min(100, int(swing*4 + revert_pct*3))
    return _make_alert("SNAPBACK", market_id,
        f"Mean reversion {direction}: {swing:.1f}pp swing, reverting {revert_pct:.1f}pp",
        score, prob_before=max_p if revert_down else min_p, prob_after=current_prob,
        extra={"swing_pp": round(swing,2), "revert_pp": round(revert_pct,2), "direction": direction})

# ── Signal 8: Smart Wallet ────────────────────────────────────────────────────
def record_wallet_market(wallet, market_id, usd_value=0):
    now = time.time()
    rec = wallet_recent[wallet]
    if now - rec["ts"] > 1800:
        rec["markets"] = set()
        rec["ts"] = now
        rec["total_usd"] = 0.0
    rec["markets"].add(market_id)
    rec["total_usd"] += usd_value

def check_smart_wallet(wallet, market_id, usd_value, wallet_db_record=None):
    markets = wallet_recent.get(wallet, {}).get("markets", set())
    total_usd = wallet_recent.get(wallet, {}).get("total_usd", 0)
    if total_usd < CROSS_MARKET_MIN_USD and usd_value < CROSS_MARKET_MIN_USD:
        return None
    base_score = 0
    if len(markets) >= 3:
        base_score += min(30, len(markets)*7)
    win_rate = 0.5
    trade_count = 0
    if wallet_db_record:
        win_rate = float(wallet_db_record.get("win_rate") or 0.5)
        trade_count = int(wallet_db_record.get("trade_count") or 0)
        vol_usd = float(wallet_db_record.get("total_volume_usd") or 0)
        if trade_count >= 10 and win_rate >= SMART_WALLET_HIT_RATE:
            base_score += min(40, int((win_rate-0.5)*80))
        if vol_usd >= 50_000: base_score += 15
        elif vol_usd >= 10_000: base_score += 8
    if usd_value >= WHALE_USD:
        base_score += min(25, _score_trade_size(usd_value)//2)
    if base_score < 25:
        return None
    win_str = f"{win_rate*100:.0f}% win rate" if trade_count >= 5 else "new wallet"
    return _make_alert("SMART_WALLET", market_id,
        f"Smart wallet: {len(markets)} markets, ${total_usd:,.0f} in 30min ({win_str}, {trade_count} trades)",
        min(100, base_score), wallet=wallet, usd_value=usd_value,
        extra={"markets_count": len(markets), "total_usd_30min": round(total_usd,0),
               "win_rate": win_rate, "trade_count": trade_count})

# ── Signal 9: Cross-Venue Divergence ─────────────────────────────────────────
def register_external_prob(keyword, source, prob):
    external_probs[keyword] = {"source": source, "prob": prob, "ts": time.time()}

def check_cross_venue(market_id, question, current_prob):
    q_lower = question.lower()
    for keyword, ext in external_probs.items():
        if keyword.lower() in q_lower and time.time() - ext["ts"] < 3600:
            gap = abs(current_prob - ext["prob"]) * 100
            if gap > ARB_EPSILON + 2:
                score = min(100, int(gap*5))
                return _make_alert("CROSS_VENUE", market_id,
                    f"Polymarket ({current_prob*100:.1f}%) vs {ext['source']} ({ext['prob']*100:.1f}%): {gap:.1f}pp gap",
                    score, prob_before=ext["prob"], prob_after=current_prob,
                    extra={"external_source": ext["source"], "gap_pp": round(gap,1)})
    return None

# ── Signal 10: Volume Regime Shift ───────────────────────────────────────────
def record_volume(market_id, volume):
    market_vol_baseline[market_id].append((time.time(), volume))

def check_vol_regime(market_id, current_volume):
    history = list(market_vol_baseline[market_id])
    if len(history) < 10:
        return None
    vols = [v for _, v in history]
    try:
        mean_vol = statistics.mean(vols)
        std_vol  = statistics.stdev(vols)
    except Exception:
        return None
    if std_vol == 0 or mean_vol == 0:
        return None
    z = (current_volume - mean_vol) / std_vol
    if z < VOL_ZSCORE:
        return None
    regime = "HIGH_VOL" if z > 3 else "ELEVATED_VOL"
    score = min(100, int(z*15))
    return _make_alert("VOL_REGIME", market_id,
        f"Volume regime shift: {regime}, z={z:.1f} (current ${current_volume:,.0f}, mean ${mean_vol:,.0f})",
        score, usd_value=current_volume,
        extra={"z_score": round(z,2), "regime": regime, "mean_vol": round(mean_vol,0)})

# ── Signal 11: Round Number Anchor / Extreme Prob ────────────────────────────
def check_resolution_edge(market_id, current_prob, question=""):
    for rn in ROUND_NUMBERS:
        distance = abs(current_prob - rn) * 100
        if distance <= ROUND_NUM_TOLERANCE:
            score = min(100, int((ROUND_NUM_TOLERANCE - distance + 1)*20))
            return _make_alert("ROUND_NUM_ANCHOR", market_id,
                f"Price anchored at {rn*100:.0f}% ({current_prob*100:.1f}%, dist {distance:.1f}pp) — possible mispricing",
                score, prob_after=current_prob,
                extra={"round_number": rn, "distance_pp": round(distance,2)})
    if current_prob > 0.95 or current_prob < 0.05:
        return _make_alert("EXTREME_PROB", market_id,
            f"Extreme probability {current_prob*100:.1f}% — possible overconfidence or tail risk",
            35, prob_after=current_prob,
            extra={"extreme_type": "HIGH" if current_prob > 0.95 else "LOW"})
    return None

# ── Signal 12: Behavioral Bias / Headline Overreaction ───────────────────────
def check_behavioral_bias(market_id, current_prob):
    snaps = list(prob_snapshots[market_id])
    if len(snaps) < 15:
        return None
    cutoff = time.time() - 3600
    recent = [(ts, p) for ts, p in snaps if ts >= cutoff]
    if len(recent) < 10:
        return None
    probs = [p for _, p in recent]
    third = len(probs) // 3
    first_third = probs[:third]
    last_third  = probs[2*third:]
    initial_spike = (max(first_third) - min(first_third)) * 100
    recent_drift  = (statistics.mean(last_third) - statistics.mean(first_third)) * 100
    if initial_spike > 8 and abs(recent_drift) < 2:
        score = min(100, int(initial_spike*4))
        return _make_alert("HEADLINE_OVERREACTION", market_id,
            f"Headline overreaction: {initial_spike:.1f}pp spike now stalling (drift {recent_drift:+.1f}pp) — fade candidate",
            score, prob_before=statistics.mean(first_third), prob_after=current_prob,
            extra={"initial_spike_pp": round(initial_spike,2), "recent_drift_pp": round(recent_drift,2)})
    return None

# ── Composite Score ───────────────────────────────────────────────────────────
def compute_composite_score(active_signals):
    if not active_signals:
        return 0
    total = sum((sig.get("insider_score",0) * SIGNAL_WEIGHTS.get(sig.get("alert_type",""),5)) / 100
                for sig in active_signals)
    if len(active_signals) >= 3: total *= 1.2
    elif len(active_signals) >= 2: total *= 1.1
    return min(100, int(total))

# ── Legacy aliases ────────────────────────────────────────────────────────────
def check_large_trade(trade):
    if trade["usd_value"] >= WHALE_USD:
        score = _score_trade_size(trade["usd_value"])
        return _make_alert("LARGE_TRADE", trade["market_id"],
            f"Large {trade['side']} {trade['outcome']}: ${trade['usd_value']:,.0f}",
            score, wallet=trade["wallet"], trade_id=trade["id"],
            prob_before=trade.get("prob_before"), prob_after=trade.get("prob_after"),
            usd_value=trade["usd_value"])
    return None

def check_rapid_shift(market_id, current_prob):
    return check_price_shock(market_id, current_prob)

def check_orderbook_imbalance(market_id, orderbook):
    return check_ob_imbalance(market_id, orderbook)

def check_cross_market(wallet, market_id, usd_value):
    return check_smart_wallet(wallet, market_id, usd_value)
