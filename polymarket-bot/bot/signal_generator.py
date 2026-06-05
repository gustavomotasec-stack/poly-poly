import time
from typing import Optional

from rich.console import Console

import config
from feeds.binance_ws import BinanceWebSocket

console = Console()

ASSET_SYMBOL_MAP = {
    "btc": "BTCUSDT",
    "eth": "ETHUSDT",
    "bitcoin": "BTCUSDT",
    "ethereum": "ETHUSDT",
}


def _rsi(closes: list[float], period: int = 14) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    diffs = [closes[i] - closes[i - 1] for i in range(len(closes) - period, len(closes))]
    gains = [d for d in diffs if d > 0]
    losses = [-d for d in diffs if d < 0]
    avg_gain = sum(gains) / period if gains else 0
    avg_loss = sum(losses) / period if losses else 0
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def _momentum(closes: list[float], window_minutes: int = 3) -> Optional[float]:
    """Price change % over the last `window_minutes` candles."""
    if len(closes) < window_minutes + 1:
        return None
    start = closes[-(window_minutes + 1)]
    end = closes[-1]
    if start == 0:
        return None
    return round((end - start) / start * 100, 4)


def _volume_spike(candles: list[dict], window: int = 20) -> bool:
    if len(candles) < window + 1:
        return False
    recent_vol = candles[-1]["volume"]
    avg_vol = sum(c["volume"] for c in candles[-window - 1 : -1]) / window
    if avg_vol == 0:
        return False
    return recent_vol >= avg_vol * config.VOLUME_SPIKE_MULTIPLIER


def _detect_asset(question: str) -> Optional[str]:
    q = question.lower()
    for keyword, symbol in ASSET_SYMBOL_MAP.items():
        if keyword in q:
            return symbol
    return None


class SignalGenerator:
    def __init__(self, binance: BinanceWebSocket):
        self._binance = binance

    def generate(self, market: dict) -> dict:
        """Generate trading signal for a given Polymarket market."""
        question = market.get("question", "")
        symbol = _detect_asset(question)

        base_result = {
            "market_id": market["market_id"],
            "question": question,
            "asset": symbol or "UNKNOWN",
            "direction": "NEUTRAL",
            "confidence": 0.0,
            "indicators": {},
            "timestamp": time.time(),
        }

        if not symbol:
            return base_result

        closes = self._binance.get_closes(symbol)
        candles = self._binance.get_candles(symbol)
        ticker = self._binance.get_ticker(symbol)

        if len(closes) < 15:
            return base_result

        rsi = _rsi(closes)
        momentum = _momentum(closes, config.MOMENTUM_WINDOW)
        vol_spike = _volume_spike(candles)
        current_price = ticker["price"] if ticker else (closes[-1] if closes else 0)

        direction = "NEUTRAL"
        confidence = 0.0

        if rsi is not None:
            if rsi < config.RSI_OVERSOLD:
                direction = "UP"
                confidence = min(1.0, (config.RSI_OVERSOLD - rsi) / config.RSI_OVERSOLD)
            elif rsi > config.RSI_OVERBOUGHT:
                direction = "DOWN"
                confidence = min(1.0, (rsi - config.RSI_OVERBOUGHT) / (100 - config.RSI_OVERBOUGHT))

        # Momentum confirmation / contradiction
        if momentum is not None and direction != "NEUTRAL":
            momentum_agrees = (direction == "UP" and momentum > 0) or (
                direction == "DOWN" and momentum < 0
            )
            if momentum_agrees:
                confidence = min(1.0, confidence * 1.25)
            else:
                confidence *= 0.60

        # Volume confirmation
        if vol_spike and direction != "NEUTRAL":
            confidence = min(1.0, confidence * 1.15)

        confidence = round(confidence, 4)
        result = {
            **base_result,
            "direction": direction,
            "confidence": confidence,
            "rsi": rsi,
            "momentum": momentum,
            "vol_spike": vol_spike,
            "current_price": current_price,
            "indicators": {
                "rsi": rsi,
                "momentum_pct": momentum,
                "volume_spike": vol_spike,
                "closes_count": len(closes),
            },
        }
        return result
