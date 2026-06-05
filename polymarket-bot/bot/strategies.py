"""
Trading strategies for Polymarket prediction markets.

Strategies (in priority order):
  1. ARBITRAGE          — price_yes + price_no < 0.97 → guaranteed profit
  2. CORRELATION_ARB    — two related markets with inconsistent prices
  3. MARKET_MAKING      — bid/ask quotes to capture spread
  4. MOMENTUM           — RSI + price momentum + news sentiment
  5. COPY_TRADE         — follow top-wallet signals
  6. MEAN_REVERSION     — last-2-min divergence trade
"""
import time
from datetime import datetime, timezone
from typing import Optional

import config


def _time_left(market: dict) -> float:
    end_time = market.get("end_time")
    if not end_time:
        return float("inf")
    return max(0.0, end_time - datetime.now(timezone.utc).timestamp())


# --------------------------------------------------------------------------- #
# 1. Arbitrage                                                                 #
# --------------------------------------------------------------------------- #

def arbitrage_strategy(market: dict, signal: dict, **_) -> Optional[dict]:
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
# 2. Correlation Arbitrage                                                     #
# --------------------------------------------------------------------------- #

def correlation_arb_strategy(market: dict, signal: dict, related_markets: list[dict] = None, **_) -> Optional[dict]:
    """
    Detect two markets that are logically mutually exclusive but together
    price above 1.0 (overlapping) — e.g. "BTC > 70k" YES at 0.55 AND
    "BTC < 70k" YES at 0.55. Sum = 1.10 → sell the expensive side of both.

    Since Polymarket doesn't support shorting, we buy the underpriced NO sides.
    """
    if not related_markets:
        return None

    best_opportunity = None
    for other in related_markets:
        if other["market_id"] == market["market_id"]:
            continue

        q1 = market.get("question", "").lower()
        q2 = other.get("question", "").lower()

        # Check if questions are about the same asset (BTC/ETH) but opposite direction
        same_asset = any(
            asset in q1 and asset in q2
            for asset in ["btc", "bitcoin", "eth", "ethereum"]
        )
        if not same_asset:
            continue

        # Correlation: both YES prices sum to > 1.0 (impossible if mutually exclusive)
        yes_sum = market["price_yes"] + other["price_yes"]
        if yes_sum <= 1.03:  # 3% threshold to cover fees
            continue

        # Buy the NO side of whichever is more overpriced
        excess = yes_sum - 1.0
        if market["price_yes"] > other["price_yes"]:
            target, cheaper = market, other
        else:
            target, cheaper = other, market

        size = config.MAX_POSITION_SIZE / 2
        opportunity = {
            "strategy": "CORRELATION_ARB",
            "market_id": target["market_id"],
            "question": target["question"],
            "action": "BUY_NO",
            "direction": "NO",
            "size": size,
            "price": target["price_no"],
            "related_market_id": cheaper["market_id"],
            "yes_sum": round(yes_sum, 4),
            "excess": round(excess, 4),
            "confidence": min(1.0, excess * 10),
            "priority": 2,
            "timestamp": time.time(),
        }
        if best_opportunity is None or excess > best_opportunity["excess"]:
            best_opportunity = opportunity

    return best_opportunity


# --------------------------------------------------------------------------- #
# 3. Market Making                                                             #
# --------------------------------------------------------------------------- #

def market_making_strategy(market: dict, signal: dict, **_) -> Optional[dict]:
    """
    Quote bid and ask around the current mid-price to capture the spread.
    Only viable when spread is >= 4% and market has > 10 min left.
    """
    tl = _time_left(market)
    if tl < 600:  # need time for spread to close
        return None

    price_yes = market.get("price_yes", 0.5)
    price_no = market.get("price_no", 0.5)
    spread = 1.0 - (price_yes + price_no)  # how much is left on the table

    if spread < 0.04:  # minimum 4% spread to be worth it
        return None

    # Quote just inside the spread
    bid = round(price_yes + 0.01, 3)  # slightly above current YES
    ask = round(price_no + 0.01, 3)   # slightly above current NO
    size_each = config.MAX_POSITION_SIZE * 0.4  # smaller size for MM

    return {
        "strategy": "MARKET_MAKING",
        "market_id": market["market_id"],
        "question": market["question"],
        "action": "QUOTE_BOTH",
        "direction": "BOTH",
        "bid_yes": bid,
        "ask_no": ask,
        "size_yes": size_each,
        "size_no": size_each,
        "spread": round(spread, 4),
        "time_left_seconds": round(tl),
        # Treat as similar to arb for simulation purposes
        "size_each": size_each,
        "expected_profit_pct": round(spread / 2 * 100, 2),
        "confidence": min(1.0, spread * 15),
        "priority": 3,
        "timestamp": time.time(),
    }


# --------------------------------------------------------------------------- #
# 4. Momentum                                                                  #
# --------------------------------------------------------------------------- #

def momentum_strategy(market: dict, signal: dict, news_sentiment=None, **_) -> Optional[dict]:
    if signal["direction"] == "NEUTRAL":
        return None

    confidence = signal["confidence"]

    # News sentiment adjustment
    if news_sentiment:
        asset = signal.get("asset", "")
        symbol = asset.replace("USDT", "")
        sentiment = news_sentiment.get_sentiment(symbol)
        if sentiment:
            confidence = news_sentiment.adjust_confidence(symbol, signal["direction"], confidence)

    if confidence < config.MIN_CONFIDENCE:
        return None

    direction = signal["direction"]
    implied_prob = 0.50 + confidence * 0.25
    price_main = market["price_yes"] if direction == "UP" else market["price_no"]
    price_hedge = market["price_no"] if direction == "UP" else market["price_yes"]

    implied_edge = implied_prob - price_main
    if implied_edge < config.MIN_EDGE:
        return None

    # Dynamic sizing: scale with confidence
    scale = 0.6 + confidence * 0.4
    size_main = config.MAX_POSITION_SIZE * scale * (1 - config.HEDGE_RATIO)
    size_hedge = config.MAX_POSITION_SIZE * scale * config.HEDGE_RATIO
    bet_direction = "YES" if direction == "UP" else "NO"
    hedge_direction = "NO" if direction == "UP" else "YES"

    return {
        "strategy": "MOMENTUM",
        "market_id": market["market_id"],
        "question": market["question"],
        "action": "BUY_WITH_HEDGE",
        "direction": bet_direction,
        "size_main": round(size_main, 4),
        "size_hedge": round(size_hedge, 4),
        "price_main": price_main,
        "price_hedge": price_hedge,
        "hedge_direction": hedge_direction,
        "implied_prob": round(implied_prob, 4),
        "implied_edge": round(implied_edge, 4),
        "confidence": round(confidence, 4),
        "news_adjusted": news_sentiment is not None,
        "priority": 4,
        "timestamp": time.time(),
    }


# --------------------------------------------------------------------------- #
# 5. Copy Trade                                                                #
# --------------------------------------------------------------------------- #

def copy_trade_strategy(market: dict, signal: dict, copy_signals: list[dict] = None, **_) -> Optional[dict]:
    """Enter a position if a high-win-rate wallet is already in this market."""
    if not copy_signals:
        return None

    for cs in copy_signals:
        if cs.get("market_id") != market["market_id"]:
            continue
        if cs.get("wallet_win_rate", 0) < 55:  # only follow wallets with >55% win rate
            continue

        direction = cs.get("direction", "YES")
        price = market["price_yes"] if direction == "YES" else market["price_no"]
        size = min(cs.get("size", config.MAX_POSITION_SIZE) * 0.5, config.MAX_POSITION_SIZE)

        return {
            "strategy": "COPY_TRADE",
            "market_id": market["market_id"],
            "question": market["question"],
            "action": "BUY",
            "direction": direction,
            "size": round(size, 4),
            "price": price,
            "source_wallet": cs.get("wallet", "unknown"),
            "wallet_win_rate": cs.get("wallet_win_rate", 0),
            "confidence": min(1.0, cs.get("wallet_win_rate", 50) / 100),
            "priority": 5,
            "timestamp": time.time(),
        }
    return None


# --------------------------------------------------------------------------- #
# 6. Mean Reversion                                                            #
# --------------------------------------------------------------------------- #

def mean_reversion_strategy(market: dict, signal: dict, **_) -> Optional[dict]:
    tl = _time_left(market)
    if tl > config.MEAN_REVERSION_MIN_TIME_LEFT or tl <= 0:
        return None

    rsi = signal.get("rsi")
    if rsi is None:
        return None

    rsi_prob_up = rsi / 100.0
    market_prob_yes = market.get("price_yes", 0.5)
    divergence = market_prob_yes - rsi_prob_up

    if abs(divergence) < config.MEAN_REVERSION_DIVERGENCE:
        return None

    direction = "NO" if divergence > 0 else "YES"
    price = market["price_no"] if divergence > 0 else market["price_yes"]
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
        "priority": 6,
        "timestamp": time.time(),
    }


# --------------------------------------------------------------------------- #
# Runner                                                                       #
# --------------------------------------------------------------------------- #

def evaluate_all_strategies(
    market: dict,
    signal: dict,
    related_markets: list[dict] = None,
    news_sentiment=None,
    copy_signals: list[dict] = None,
) -> list[dict]:
    """Run all strategies and return sorted recommendations (highest priority first)."""
    recommendations = []
    kwargs = dict(
        related_markets=related_markets or [],
        news_sentiment=news_sentiment,
        copy_signals=copy_signals or [],
    )

    for fn in [
        arbitrage_strategy,
        correlation_arb_strategy,
        market_making_strategy,
        momentum_strategy,
        copy_trade_strategy,
        mean_reversion_strategy,
    ]:
        try:
            rec = fn(market, signal, **kwargs)
            if rec:
                recommendations.append(rec)
        except Exception:
            pass

    recommendations.sort(key=lambda r: r.get("priority", 99))
    return recommendations
