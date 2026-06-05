import asyncio
import json
import time
from typing import Optional
from datetime import datetime, timezone

import aiohttp
import websockets
from rich.console import Console

import config

console = Console()

MARKET_KEYWORDS_PRIORITY = ["5-minute", "15-minute", "5 minute", "15 minute"]
MARKET_KEYWORDS_FALLBACK = ["btc", "eth", "bitcoin", "ethereum"]


class PolymarketAPI:
    """Client for Polymarket Gamma + CLOB APIs."""

    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None
        self._price_cache: dict[str, dict] = {}
        self._market_cache: list[dict] = []
        self._cache_ts = 0.0
        self._ws_running = False

    async def start(self):
        self._session = aiohttp.ClientSession(
            headers={"User-Agent": "polymarket-bot/1.0"},
            timeout=aiohttp.ClientTimeout(total=10),
        )

    async def stop(self):
        self._ws_running = False
        if self._session:
            await self._session.close()

    # ------------------------------------------------------------------ #
    # Market discovery                                                     #
    # ------------------------------------------------------------------ #

    async def find_active_crypto_markets(self) -> list[dict]:
        """Return active short-timeframe crypto prediction markets."""
        now = time.time()
        if now - self._cache_ts < 30 and self._market_cache:
            return self._market_cache

        markets = await self._fetch_gamma_markets()
        filtered = self._filter_markets(markets)
        self._market_cache = filtered
        self._cache_ts = now
        return filtered

    async def _fetch_gamma_markets(self) -> list[dict]:
        url = f"{config.GAMMA_API_BASE}/markets"
        params = {"tag_slug": "crypto", "closed": "false", "limit": 200}
        try:
            async with self._session.get(url, params=params) as resp:
                if resp.status != 200:
                    console.print(f"[yellow][Polymarket] Gamma API returned {resp.status}[/yellow]")
                    return []
                data = await resp.json(content_type=None)
                return data if isinstance(data, list) else data.get("markets", [])
        except Exception as exc:
            console.print(f"[red][Polymarket] Error fetching markets: {exc}[/red]")
            return []

    def _filter_markets(self, markets: list[dict]) -> list[dict]:
        result = []
        now_ts = datetime.now(timezone.utc).timestamp()

        for m in markets:
            title = (m.get("question") or m.get("title") or "").lower()
            end_time = self._parse_end_time(m)

            # Skip markets that have already ended or end in < 3 minutes
            if end_time and (end_time - now_ts) < 180:
                continue

            is_priority = any(kw in title for kw in MARKET_KEYWORDS_PRIORITY)
            is_fallback = any(kw in title for kw in MARKET_KEYWORDS_FALLBACK)

            if not (is_priority or is_fallback):
                continue

            tokens = m.get("tokens", m.get("clobTokenIds", []))
            if not tokens or len(tokens) < 2:
                continue

            # Gamma returns tokens as list of dicts or list of strings
            if isinstance(tokens[0], dict):
                token_yes = tokens[0].get("token_id") or tokens[0].get("id", "")
                token_no = tokens[1].get("token_id") or tokens[1].get("id", "")
                price_yes = float(tokens[0].get("price", 0.5))
                price_no = float(tokens[1].get("price", 0.5))
            else:
                token_yes, token_no = str(tokens[0]), str(tokens[1])
                price_yes = float(m.get("outcomePrices", [0.5, 0.5])[0])
                price_no = float(m.get("outcomePrices", [0.5, 0.5])[1])

            result.append(
                {
                    "market_id": str(m.get("id") or m.get("conditionId", "")),
                    "token_id_yes": token_yes,
                    "token_id_no": token_no,
                    "question": m.get("question") or m.get("title", ""),
                    "end_time": end_time,
                    "price_yes": price_yes,
                    "price_no": price_no,
                    "priority": is_priority,
                }
            )

        result.sort(key=lambda x: (not x["priority"], x.get("end_time") or 0))
        return result

    def _parse_end_time(self, market: dict) -> Optional[float]:
        for key in ("endDateIso", "end_date_iso", "endTime", "end_time"):
            val = market.get(key)
            if val:
                try:
                    if isinstance(val, (int, float)):
                        return float(val)
                    dt = datetime.fromisoformat(str(val).replace("Z", "+00:00"))
                    return dt.timestamp()
                except Exception:
                    pass
        return None

    # ------------------------------------------------------------------ #
    # Order book                                                           #
    # ------------------------------------------------------------------ #

    async def get_order_book(self, token_id: str) -> Optional[dict]:
        url = f"{config.CLOB_API_BASE}/book"
        try:
            async with self._session.get(url, params={"token_id": token_id}) as resp:
                if resp.status != 200:
                    return None
                return await resp.json(content_type=None)
        except Exception as exc:
            console.print(f"[yellow][Polymarket] Order book error for {token_id}: {exc}[/yellow]")
            return None

    def detect_arbitrage(self, market: dict) -> Optional[dict]:
        """Return arb opportunity if price_yes + price_no < 0.97."""
        total = market["price_yes"] + market["price_no"]
        if total < 0.97:
            profit = 1.0 - total
            return {
                "market_id": market["market_id"],
                "question": market["question"],
                "price_yes": market["price_yes"],
                "price_no": market["price_no"],
                "total": total,
                "guaranteed_profit": profit,
            }
        return None

    # ------------------------------------------------------------------ #
    # Real-time WS (prices)                                               #
    # ------------------------------------------------------------------ #

    async def subscribe_market_prices(self, token_ids: list[str], callback):
        """Subscribe to real-time price updates via CLOB WebSocket."""
        self._ws_running = True
        reconnect_delay = 1.0

        while self._ws_running:
            try:
                async with websockets.connect(config.CLOB_WS_URL) as ws:
                    subscribe_msg = json.dumps(
                        {"type": "subscribe", "channel": "price_change", "assets": token_ids}
                    )
                    await ws.send(subscribe_msg)
                    reconnect_delay = 1.0
                    console.print("[green][Polymarket WS] Connected[/green]")

                    async for raw in ws:
                        if not self._ws_running:
                            break
                        try:
                            events = json.loads(raw)
                            if not isinstance(events, list):
                                events = [events]
                            for event in events:
                                if event.get("event_type") == "price_change":
                                    token_id = event.get("asset_id", "")
                                    price = float(event.get("price", 0))
                                    self._price_cache[token_id] = {
                                        "price": price,
                                        "timestamp": time.time(),
                                    }
                                    await callback(token_id, price)
                        except Exception as exc:
                            console.print(f"[yellow][Polymarket WS] Parse error: {exc}[/yellow]")

            except Exception as exc:
                console.print(
                    f"[red][Polymarket WS] Disconnected: {exc}. Retry in {reconnect_delay}s[/red]"
                )
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, 60.0)

    def get_cached_price(self, token_id: str) -> Optional[float]:
        cached = self._price_cache.get(token_id)
        if cached and time.time() - cached["timestamp"] < 60:
            return cached["price"]
        return None
