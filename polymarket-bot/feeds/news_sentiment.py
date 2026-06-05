"""
News sentiment feed using GNews API (free tier) + keyword scoring.
Provides bullish/bearish signals for BTC and ETH based on recent headlines.
"""
import asyncio
import time
from typing import Optional

import aiohttp
from rich.console import Console

console = Console()

# Free public RSS/JSON news endpoints (no key required)
NEWS_SOURCES = [
    "https://cryptopanic.com/api/v1/posts/?auth_token=free&currencies=BTC,ETH&kind=news&public=true",
    "https://min-api.cryptocompare.com/data/v2/news/?lang=EN&categories=BTC,ETH",
]

BULLISH_KEYWORDS = [
    "surge", "rally", "breakout", "bullish", "buy", "all-time high", "ath",
    "adoption", "etf", "institutional", "inflow", "halving", "upgrade",
    "partnership", "approval", "positive", "recovery", "rebound",
]
BEARISH_KEYWORDS = [
    "crash", "dump", "bearish", "sell", "ban", "hack", "exploit", "scam",
    "liquidation", "outflow", "fear", "regulatory", "crackdown", "fine",
    "collapse", "plunge", "decline", "warning", "fraud", "lawsuit",
]

CACHE_TTL = 120  # seconds


class NewsSentiment:
    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None
        self._cache: dict[str, dict] = {}  # asset → {score, headlines, ts}
        self._running = False

    async def start(self):
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=8),
            headers={"User-Agent": "polymarket-bot/1.0"},
        )
        self._running = True
        asyncio.create_task(self._refresh_loop())

    async def stop(self):
        self._running = False
        if self._session:
            await self._session.close()

    async def _refresh_loop(self):
        while self._running:
            try:
                await self._fetch_and_score()
            except Exception as exc:
                console.print(f"[yellow][News] Refresh error: {exc}[/yellow]")
            await asyncio.sleep(CACHE_TTL)

    async def _fetch_and_score(self):
        headlines: list[str] = []
        for url in NEWS_SOURCES:
            try:
                async with self._session.get(url) as resp:
                    if resp.status != 200:
                        continue
                    data = await resp.json(content_type=None)
                    headlines.extend(self._extract_headlines(data))
            except Exception:
                pass

        if not headlines:
            return

        btc_score = self._score(headlines, ["btc", "bitcoin"])
        eth_score = self._score(headlines, ["eth", "ethereum"])

        ts = time.time()
        self._cache["BTC"] = {"score": btc_score, "headlines": headlines[:5], "ts": ts}
        self._cache["ETH"] = {"score": eth_score, "headlines": headlines[:5], "ts": ts}
        console.print(
            f"[dim][News] Sentiment updated — BTC: {btc_score:+.2f}, ETH: {eth_score:+.2f}[/dim]"
        )

    def _extract_headlines(self, data: dict | list) -> list[str]:
        titles = []
        if isinstance(data, list):
            for item in data[:30]:
                t = item.get("title") or item.get("headline") or ""
                if t:
                    titles.append(t.lower())
        elif isinstance(data, dict):
            for key in ("results", "posts", "Data", "articles"):
                items = data.get(key, [])
                if isinstance(items, list):
                    for item in items[:30]:
                        t = item.get("title") or item.get("headline") or ""
                        if t:
                            titles.append(t.lower())
        return titles

    def _score(self, headlines: list[str], asset_keywords: list[str]) -> float:
        """Return sentiment score in [-1, +1]. Filters to asset-relevant headlines."""
        relevant = [h for h in headlines if any(k in h for k in asset_keywords)]
        if not relevant:
            relevant = headlines  # fallback: use all if no asset-specific news

        bull = sum(
            1 for h in relevant for kw in BULLISH_KEYWORDS if kw in h
        )
        bear = sum(
            1 for h in relevant for kw in BEARISH_KEYWORDS if kw in h
        )
        total = bull + bear
        if total == 0:
            return 0.0
        return round((bull - bear) / total, 3)

    def get_sentiment(self, symbol: str) -> Optional[dict]:
        """
        Returns {'score': float[-1,+1], 'direction': 'BULLISH'|'BEARISH'|'NEUTRAL',
                 'headlines': list[str], 'age_seconds': float}
        """
        asset = symbol.replace("USDT", "").upper()
        cached = self._cache.get(asset)
        if not cached:
            return None
        age = time.time() - cached["ts"]
        if age > CACHE_TTL * 3:
            return None
        score = cached["score"]
        direction = "NEUTRAL"
        if score > 0.15:
            direction = "BULLISH"
        elif score < -0.15:
            direction = "BEARISH"
        return {
            "asset": asset,
            "score": score,
            "direction": direction,
            "headlines": cached.get("headlines", []),
            "age_seconds": round(age),
        }

    def adjust_confidence(self, symbol: str, direction: str, confidence: float) -> float:
        """Boost or penalize confidence based on news alignment."""
        sentiment = self.get_sentiment(symbol)
        if not sentiment:
            return confidence

        s_dir = sentiment["direction"]
        strength = abs(sentiment["score"])

        if direction == "UP" and s_dir == "BULLISH":
            return min(1.0, confidence * (1 + 0.2 * strength))
        if direction == "DOWN" and s_dir == "BEARISH":
            return min(1.0, confidence * (1 + 0.2 * strength))
        if (direction == "UP" and s_dir == "BEARISH") or (direction == "DOWN" and s_dir == "BULLISH"):
            return confidence * (1 - 0.25 * strength)
        return confidence
