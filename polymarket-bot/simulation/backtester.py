"""
Backtester — replay historical Polymarket markets against our strategies.

Fetches closed markets from Gamma API and simulates what each strategy
would have done, reporting win rate, PnL, and per-strategy breakdown.
"""
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import aiohttp
from rich.console import Console
from rich.table import Table

import config
from bot.strategies import evaluate_all_strategies

console = Console()

CACHE_PATH = Path(__file__).parent.parent / "data" / "backtest_cache.json"


class Backtester:
    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None

    async def __aenter__(self):
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=15),
            headers={"User-Agent": "polymarket-bot/1.0"},
        )
        return self

    async def __aexit__(self, *_):
        if self._session:
            await self._session.close()

    # ------------------------------------------------------------------ #
    # Data fetching                                                        #
    # ------------------------------------------------------------------ #

    async def fetch_closed_markets(self, limit: int = 200) -> list[dict]:
        """Fetch recently closed crypto prediction markets."""
        cached = self._load_cache()
        if cached:
            console.print(f"[dim][Backtest] Using {len(cached)} cached markets[/dim]")
            return cached[:limit]

        url = f"{config.GAMMA_API_BASE}/markets"
        params = {"tag_slug": "crypto", "closed": "true", "limit": limit}
        try:
            async with self._session.get(url, params=params) as resp:
                if resp.status != 200:
                    console.print(f"[red][Backtest] API error {resp.status}[/red]")
                    return []
                data = await resp.json(content_type=None)
                markets = data if isinstance(data, list) else data.get("markets", [])
                self._save_cache(markets)
                return markets
        except Exception as exc:
            console.print(f"[red][Backtest] Fetch error: {exc}[/red]")
            return []

    # ------------------------------------------------------------------ #
    # Simulation                                                           #
    # ------------------------------------------------------------------ #

    async def run(self, limit: int = 100, initial_bankroll: float = 100.0) -> dict:
        markets = await self.fetch_closed_markets(limit)
        if not markets:
            return {"error": "No historical markets available"}

        bankroll = initial_bankroll
        trades: list[dict] = []
        strategy_stats: dict[str, dict] = {}

        console.print(f"[cyan][Backtest] Simulating {len(markets)} closed markets…[/cyan]")

        for m in markets:
            market = self._normalize_market(m)
            if not market:
                continue

            # Simulate a flat neutral signal (no live Binance data in backtest)
            # Use RSI-like proxy from market price divergence
            price_yes = market["price_yes"]
            synthetic_rsi = price_yes * 100  # 0-100 proxy
            signal = {
                "direction": "UP" if synthetic_rsi < 40 else ("DOWN" if synthetic_rsi > 60 else "NEUTRAL"),
                "confidence": abs(0.5 - price_yes) * 2,
                "rsi": synthetic_rsi,
                "momentum": 0.0,
                "indicators": {},
            }

            recs = evaluate_all_strategies(market, signal)
            if not recs:
                continue

            rec = recs[0]
            strategy = rec["strategy"]
            cost = rec.get("size_yes", 0) + rec.get("size_no", 0) + rec.get("size_main", 0) + rec.get("size_hedge", 0) + rec.get("size", 0)
            cost = min(cost, config.MAX_POSITION_SIZE)

            if bankroll < cost or cost <= 0:
                continue

            # Determine actual outcome from resolved market
            outcome_yes_wins = self._did_yes_win(m)
            if outcome_yes_wins is None:
                continue

            pnl = self._compute_pnl(rec, outcome_yes_wins, cost)
            bankroll += pnl

            trade_record = {
                "market_id": market["market_id"],
                "question": market["question"][:60],
                "strategy": strategy,
                "direction": rec.get("direction", "?"),
                "cost": round(cost, 4),
                "pnl": round(pnl, 4),
                "won": pnl > 0,
            }
            trades.append(trade_record)

            # Per-strategy stats
            if strategy not in strategy_stats:
                strategy_stats[strategy] = {"trades": 0, "wins": 0, "pnl": 0.0}
            strategy_stats[strategy]["trades"] += 1
            if pnl > 0:
                strategy_stats[strategy]["wins"] += 1
            strategy_stats[strategy]["pnl"] += pnl

        return self._build_report(trades, strategy_stats, initial_bankroll, bankroll)

    # ------------------------------------------------------------------ #
    # Helpers                                                              #
    # ------------------------------------------------------------------ #

    def _normalize_market(self, m: dict) -> Optional[dict]:
        tokens = m.get("tokens", m.get("clobTokenIds", []))
        if not tokens or len(tokens) < 2:
            return None
        if isinstance(tokens[0], dict):
            price_yes = float(tokens[0].get("price", 0.5))
            price_no = float(tokens[1].get("price", 0.5))
        else:
            prices = m.get("outcomePrices", [0.5, 0.5])
            price_yes = float(prices[0])
            price_no = float(prices[1])

        return {
            "market_id": str(m.get("id") or m.get("conditionId", "")),
            "question": m.get("question") or m.get("title", ""),
            "price_yes": price_yes,
            "price_no": price_no,
            "end_time": None,
        }

    def _did_yes_win(self, m: dict) -> Optional[bool]:
        """Try to determine if YES outcome won from resolved market data."""
        tokens = m.get("tokens", [])
        if isinstance(tokens, list) and len(tokens) >= 1:
            t = tokens[0] if isinstance(tokens[0], dict) else {}
            winner = t.get("winner")
            if winner is not None:
                return bool(winner)
        # Fallback: if resolved price is ~1.0, YES won
        if isinstance(tokens, list) and tokens:
            t = tokens[0] if isinstance(tokens[0], dict) else {}
            price = float(t.get("price", 0.5))
            if price >= 0.95:
                return True
            if price <= 0.05:
                return False
        return None

    def _compute_pnl(self, rec: dict, yes_wins: bool, cost: float) -> float:
        strategy = rec["strategy"]
        if strategy == "ARBITRAGE":
            return cost * rec.get("expected_profit_pct", 5) / 100
        direction = rec.get("direction", "YES")
        if strategy == "MOMENTUM":
            main_wins = (direction == "YES" and yes_wins) or (direction == "NO" and not yes_wins)
            size_main = rec.get("size_main", cost * 0.8)
            size_hedge = rec.get("size_hedge", cost * 0.2)
            price_main = rec.get("price_main", 0.5)
            price_hedge = rec.get("price_hedge", 0.5)
            if main_wins:
                return size_main * (1 / price_main - 1) - size_hedge
            else:
                return size_hedge * (1 / price_hedge - 1) - size_main
        # Mean reversion / single bet
        bet_wins = (direction == "YES" and yes_wins) or (direction == "NO" and not yes_wins)
        price = rec.get("price", 0.5)
        if price <= 0:
            return -cost
        if bet_wins:
            return cost * (1 / price - 1)
        return -cost

    def _build_report(self, trades, strategy_stats, initial, final) -> dict:
        if not trades:
            return {"error": "No trades simulated"}

        wins = [t for t in trades if t["won"]]
        total_pnl = final - initial
        win_rate = len(wins) / len(trades) * 100 if trades else 0

        # Max drawdown
        peak = initial
        running = initial
        max_dd = 0.0
        for t in trades:
            running += t["pnl"]
            peak = max(peak, running)
            dd = (peak - running) / peak if peak > 0 else 0
            max_dd = max(max_dd, dd)

        report = {
            "initial_bankroll": initial,
            "final_bankroll": round(final, 4),
            "total_pnl": round(total_pnl, 4),
            "total_pnl_pct": round(total_pnl / initial * 100, 2),
            "total_trades": len(trades),
            "win_rate": round(win_rate, 1),
            "max_drawdown_pct": round(max_dd * 100, 2),
            "per_strategy": {
                k: {
                    "trades": v["trades"],
                    "win_rate": round(v["wins"] / v["trades"] * 100, 1) if v["trades"] else 0,
                    "total_pnl": round(v["pnl"], 4),
                }
                for k, v in strategy_stats.items()
            },
            "trades": trades[-20:],  # last 20 for display
        }

        self._print_report(report)
        return report

    def _print_report(self, r: dict):
        console.print("\n[bold cyan]═══ Backtest Results ═══[/bold cyan]")
        console.print(f"  Trades: {r['total_trades']}  |  Win Rate: {r['win_rate']}%  |  PnL: ${r['total_pnl']:+.2f} ({r['total_pnl_pct']:+.1f}%)  |  Max DD: {r['max_drawdown_pct']:.1f}%")

        table = Table(title="Per-Strategy Breakdown", style="cyan")
        table.add_column("Strategy")
        table.add_column("Trades")
        table.add_column("Win Rate")
        table.add_column("PnL")
        for strat, s in r["per_strategy"].items():
            color = "green" if s["total_pnl"] >= 0 else "red"
            table.add_row(
                strat,
                str(s["trades"]),
                f"{s['win_rate']}%",
                f"[{color}]${s['total_pnl']:+.4f}[/{color}]",
            )
        console.print(table)

    # ------------------------------------------------------------------ #
    # Cache                                                                #
    # ------------------------------------------------------------------ #

    def _load_cache(self) -> list:
        if not CACHE_PATH.exists():
            return []
        try:
            data = json.loads(CACHE_PATH.read_text())
            # Cache valid for 6 hours
            if time.time() - data.get("ts", 0) < 21600:
                return data.get("markets", [])
        except Exception:
            pass
        return []

    def _save_cache(self, markets: list):
        import time
        CACHE_PATH.parent.mkdir(exist_ok=True)
        CACHE_PATH.write_text(json.dumps({"ts": time.time(), "markets": markets}))
