import time
from datetime import datetime, timezone
from typing import Optional

import config


def _time_left(market: dict) -> float:
    """Seconds until market ends."""
    end_time = market.get("end_time")
    if not end_time:
        return float("inf")
    return max(0.0, end_time - datetime.now(timezone.utc).timestamp())


# --------------------------------------------------------------------------- #
# Strategy 1 — Arbitrage                                                       #
# --------------------------------------------------------------------------- #

def arbitrage_strategy(market: dict, signal: dict) -> Optional[dict]:
    """Buy both sides when price_yes + price_no < 0.97 for guaranteed profit."""
    price_yes = market.get("price_yes", 1.0)
    price_no = market.get("price_no", 1.0)
    total = price_yes + price_no

    if total >= 0.97:
        return None

    guaranteed_profit_pct = (1.0 - total) / total
    size_each = config.MAX_POSITION_SIZE / 2

    return {
        "strategy": "ARBITRAGE",
        "market_id": market["market_id"],
        "question": market["question"],
        "action": "BUY_BOTH",
        "direction": "BOTH",
        "size_yes": size_each,
        "size_no": size_each,
        "price_yes": price_yes,
        "price_no": price_no,
        "expected_profit_pct": round(guaranteed_profit_pct * 100, 2),
        "confidence": 1.0,
        "priority": 1,
        "timestamp": time.time(),
    }


# --------------------------------------------------------------------------- #
# Strategy 2 — Momentum                                                        #
# --------------------------------------------------------------------------- #

def momentum_strategy(market: dict, signal: dict) -> Optional[dict]:
    """
    Enter when RSI + momentum agree and implied edge > MIN_EDGE.
    Position: 80% main side, 20% hedge on opposite side.
    """
    if signal["direction"] == "NEUTRAL":
        return None
    if signal["confidence"] < config.MIN_CONFIDENCE:
        return None

    direction = signal["direction"]  # UP → bet YES, DOWN → bet NO
    confidence = signal["confidence"]

    # Implied probability from the signal (confidence maps to 55-75% range)
    implied_prob = 0.50 + confidence * 0.25

    price_main = market["price_yes"] if direction == "UP" else market["price_no"]
    price_hedge = market["price_no"] if direction == "UP" else market["price_yes"]

    implied_edge = implied_prob - price_main
    if implied_edge < config.MIN_EDGE:
        return None

    size_main = config.MAX_POSITION_SIZE * (1 - config.HEDGE_RATIO)
    size_hedge = config.MAX_POSITION_SIZE * config.HEDGE_RATIO

    bet_direction = "YES" if direction == "UP" else "NO"
    hedge_direction = "NO" if direction == "UP" else "YES"

    return {
        "strategy": "MOMENTUM",
        "market_id": market["market_id"],
        "question": market["question"],
        "action": "BUY_WITH_HEDGE",
        "direction": bet_direction,
        "size_main": size_main,
        "size_hedge": size_hedge,
        "price_main": price_main,
        "price_hedge": price_hedge,
        "hedge_direction": hedge_direction,
        "implied_prob": round(implied_prob, 4),
        "implied_edge": round(implied_edge, 4),
        "confidence": confidence,
        "priority": 2,
        "timestamp": time.time(),
    }


# --------------------------------------------------------------------------- #
# Strategy 3 — Mean Reversion                                                  #
# --------------------------------------------------------------------------- #

def mean_reversion_strategy(market: dict, signal: dict) -> Optional[dict]:
    """
    Bet against the crowd when market price diverges >10% from RSI-implied probability.
    Only activates in the last 2 minutes of the market.
    """
    tl = _time_left(market)
    if tl > config.MEAN_REVERSION_MIN_TIME_LEFT or tl <= 0:
        return None

    rsi = signal.get("rsi")
    if rsi is None:
        return None

    # Convert RSI to an implied probability (0 RSI = 0% likely UP, 100 RSI = 100% likely UP)
    rsi_prob_up = rsi / 100.0
    market_prob_yes = market.get("price_yes", 0.5)

    divergence = market_prob_yes - rsi_prob_up

    if abs(divergence) < config.MEAN_REVERSION_DIVERGENCE:
        return None

    # If market overestimates YES → bet NO, and vice versa
    if divergence > 0:
        direction = "NO"
        price = market["price_no"]
        size = config.MAX_POSITION_SIZE
    else:
        direction = "YES"
        price = market["price_yes"]
        size = config.MAX_POSITION_SIZE

    return {
        "strategy": "MEAN_REVERSION",
        "market_id": market["market_id"],
        "question": market["question"],
        "action": "BUY",
        "direction": direction,
        "size": size,
        "price": price,
        "rsi_prob_up": round(rsi_prob_up, 4),
        "market_prob_yes": round(market_prob_yes, 4),
        "divergence": round(divergence, 4),
        "time_left_seconds": round(tl),
        "confidence": min(1.0, abs(divergence) * 5),
        "priority": 3,
        "timestamp": time.time(),
    }


# --------------------------------------------------------------------------- #
# Runner                                                                       #
# --------------------------------------------------------------------------- #

def evaluate_all_strategies(market: dict, signal: dict) -> list[dict]:
    """Run all strategies and return sorted recommendations (highest priority first)."""
    recommendations = []

    for fn in [arbitrage_strategy, momentum_strategy, mean_reversion_strategy]:
        try:
            rec = fn(market, signal)
            if rec:
                recommendations.append(rec)
        except Exception:
            pass

    recommendations.sort(key=lambda r: r.get("priority", 99))
    return recommendations
