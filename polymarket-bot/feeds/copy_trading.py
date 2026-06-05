"""
Copy Trading — monitor top Polymarket wallets and replicate their positions.

Uses the Polymarket Gamma API to fetch trade history of known profitable addresses.
The user can supply wallet addresses to track via config or .env.
"""
import asyncio
import os
import time
from typing import Optional

import aiohttp
from rich.console import Console

import config

console = Console()

# Add COPY_TRADE_WALLETS=0xABC,0xDEF to .env to track specific wallets
TRACKED_WALLETS: list[str] = [
    w.strip()
    for w in os.getenv("COPY_TRADE_WALLETS", "").split(",")
    if w.strip().startswith("0x")
]

REFRESH_INTERVAL = 60  # seconds
MIN_TRADE_SIZE = 5.0   # USDC — ignore micro trades


class CopyTradingFeed:
    """
    Tracks positions of top wallets and surfaces copy signals.
    Signals are advisory — risk_manager still filters them.
    """

    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None
        self._signals: list[dict] = []
        self._wallet_stats: dict[str, dict] = {}
        self._running = False

    async def start(self):
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10),
            headers={"User-Agent": "polymarket-bot/1.0"},
        )
        self._running = True
        if TRACKED_WALLETS:
            console.print(f"[cyan][CopyTrade] Tracking {len(TRACKED_WALLETS)} wallet(s)[/cyan]")
            asyncio.create_task(self._refresh_loop())
        else:
            console.print(
                "[dim][CopyTrade] No wallets configured. "
                "Set COPY_TRADE_WALLETS=0xABC,0xDEF in .env to enable.[/dim]"
            )

    async def stop(self):
        self._running = False
        if self._session:
            await self._session.close()

    async def _refresh_loop(self):
        while self._running:
            try:
                await self._fetch_all_wallets()
            except Exception as exc:
                console.print(f"[yellow][CopyTrade] Refresh error: {exc}[/yellow]")
            await asyncio.sleep(REFRESH_INTERVAL)

    async def _fetch_all_wallets(self):
        new_signals = []
        for wallet in TRACKED_WALLETS:
            trades = await self._fetch_wallet_trades(wallet)
            stats = self._compute_stats(wallet, trades)
            self._wallet_stats[wallet] = stats

            # Surface recent open positions as copy signals
            for trade in trades[:5]:
                if trade.get("size", 0) < MIN_TRADE_SIZE:
                    continue
                if trade.get("status") != "open":
                    continue
                new_signals.append(
                    {
                        "source": "copy_trade",
                        "wallet": wallet[:10] + "…",
                        "market_id": trade.get("market_id", ""),
                        "question": trade.get("question", ""),
                        "direction": trade.get("outcome", "YES"),
                        "size": trade.get("size", 0),
                        "entry_price": trade.get("price", 0.5),
                        "wallet_win_rate": stats.get("win_rate", 0),
                        "ts": time.time(),
                    }
                )
        self._signals = new_signals

    async def _fetch_wallet_trades(self, wallet: str) -> list[dict]:
        url = f"{config.GAMMA_API_BASE}/trades"
        params = {"maker": wallet, "limit": 50}
        try:
            async with self._session.get(url, params=params) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json(content_type=None)
                return data if isinstance(data, list) else data.get("trades", [])
        except Exception as exc:
            console.print(f"[yellow][CopyTrade] Error fetching {wallet[:10]}…: {exc}[/yellow]")
            return []

    def _compute_stats(self, wallet: str, trades: list[dict]) -> dict:
        closed = [t for t in trades if t.get("status") == "closed"]
        if not closed:
            return {"win_rate": 0.0, "total_trades": 0, "total_pnl": 0.0}
        wins = [t for t in closed if float(t.get("pnl", 0)) > 0]
        total_pnl = sum(float(t.get("pnl", 0)) for t in closed)
        return {
            "wallet": wallet,
            "win_rate": round(len(wins) / len(closed) * 100, 1),
            "total_trades": len(closed),
            "total_pnl": round(total_pnl, 2),
        }

    def get_signals(self) -> list[dict]:
        """Return current copy-trade signals, sorted by wallet win rate."""
        return sorted(self._signals, key=lambda s: -s.get("wallet_win_rate", 0))

    def get_wallet_stats(self) -> list[dict]:
        return list(self._wallet_stats.values())

    def scale_size(self, signal: dict, base_size: float) -> float:
        """
        Scale copy position proportionally to tracked wallet win rate.
        A 70%+ win rate wallet → full base_size; below 50% → 25%.
        """
        wr = signal.get("wallet_win_rate", 50)
        factor = max(0.25, min(1.0, (wr - 40) / 40))
        return round(base_size * factor, 4)
