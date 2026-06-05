import math
import time
from datetime import datetime
from typing import Optional

from rich.console import Console

import config
import storage.db as db

console = Console()


class PaperTrader:
    """Simulate trade execution using real Polymarket prices."""

    def __init__(self, initial_bankroll: float = config.INITIAL_PAPER_BANKROLL):
        self._bankroll = initial_bankroll
        self._initial = initial_bankroll
        self._open_trades: dict[str, dict] = {}  # trade_id → trade
        self._closed_trades: list[dict] = []

    @property
    def bankroll(self) -> float:
        return self._bankroll

    def execute(self, recommendation: dict) -> Optional[dict]:
        """Simulate opening a trade from a strategy recommendation."""
        strategy = recommendation["strategy"]
        market_id = recommendation["market_id"]
        question = recommendation["question"]

        if strategy == "ARBITRAGE":
            return self._open_arb(recommendation)
        elif strategy == "MOMENTUM":
            return self._open_momentum(recommendation)
        elif strategy == "MEAN_REVERSION":
            return self._open_single(recommendation)
        return None

    def _open_arb(self, rec: dict) -> Optional[dict]:
        total_cost = rec["size_yes"] + rec["size_no"]
        if self._bankroll < total_cost:
            console.print("[yellow][Paper] Insufficient bankroll for arb[/yellow]")
            return None

        self._bankroll -= total_cost
        trade = {
            "timestamp": datetime.utcnow().isoformat(),
            "market_id": rec["market_id"],
            "question": rec["question"],
            "direction": "BOTH",
            "size": total_cost,
            "entry_price": rec["price_yes"] + rec["price_no"],
            "strategy": "ARBITRAGE",
            "simulated": True,
            "status": "open",
            "meta": rec,
        }
        trade_id = db.save_trade(trade)
        trade["id"] = trade_id
        self._open_trades[str(trade_id)] = trade
        console.print(
            f"[green][Paper] ARB opened on '{rec['question'][:40]}' "
            f"Cost: ${total_cost:.3f}, Expected profit: {rec['expected_profit_pct']:.2f}%[/green]"
        )
        return trade

    def _open_momentum(self, rec: dict) -> Optional[dict]:
        total_cost = rec["size_main"] + rec["size_hedge"]
        if self._bankroll < total_cost:
            return None

        self._bankroll -= total_cost
        trade = {
            "timestamp": datetime.utcnow().isoformat(),
            "market_id": rec["market_id"],
            "question": rec["question"],
            "direction": rec["direction"],
            "size": total_cost,
            "entry_price": rec["price_main"],
            "strategy": "MOMENTUM",
            "simulated": True,
            "status": "open",
            "meta": rec,
        }
        trade_id = db.save_trade(trade)
        trade["id"] = trade_id
        self._open_trades[str(trade_id)] = trade
        console.print(
            f"[green][Paper] MOMENTUM {rec['direction']} on '{rec['question'][:40]}' "
            f"Edge: {rec['implied_edge']*100:.1f}%[/green]"
        )
        return trade

    def _open_single(self, rec: dict) -> Optional[dict]:
        size = rec["size"]
        if self._bankroll < size:
            return None

        self._bankroll -= size
        trade = {
            "timestamp": datetime.utcnow().isoformat(),
            "market_id": rec["market_id"],
            "question": rec["question"],
            "direction": rec["direction"],
            "size": size,
            "entry_price": rec["price"],
            "strategy": rec["strategy"],
            "simulated": True,
            "status": "open",
            "meta": rec,
        }
        trade_id = db.save_trade(trade)
        trade["id"] = trade_id
        self._open_trades[str(trade_id)] = trade
        return trade

    def settle(self, trade_id: str, outcome: str, exit_price: float) -> Optional[float]:
        """
        Settle a trade. outcome = 'YES' | 'NO' | 'BOTH' (for arb).
        Returns PnL.
        """
        trade = self._open_trades.pop(trade_id, None)
        if not trade:
            return None

        size = trade["size"]
        strategy = trade["strategy"]
        direction = trade["direction"]

        if strategy == "ARBITRAGE":
            pnl = size * trade["meta"].get("expected_profit_pct", 0) / 100
        elif direction == outcome:
            # Won main bet
            pnl = size * (1.0 / trade["entry_price"] - 1) * (1 - config.HEDGE_RATIO)
            # Hedge lost
            pnl -= size * config.HEDGE_RATIO
        else:
            # Lost main, hedge paid off partially
            pnl = size * config.HEDGE_RATIO * (1.0 / exit_price - 1)
            pnl -= size * (1 - config.HEDGE_RATIO)

        self._bankroll += size + pnl
        db.update_trade(int(trade_id), exit_price, pnl)
        trade["exit_price"] = exit_price
        trade["pnl"] = pnl
        trade["status"] = "closed"
        self._closed_trades.append(trade)

        color = "green" if pnl >= 0 else "red"
        console.print(
            f"[{color}][Paper] Settled '{trade['question'][:40]}' "
            f"PnL: ${pnl:+.4f} | Bankroll: ${self._bankroll:.2f}[/{color}]"
        )
        return pnl

    def auto_settle_expired(self, active_markets: list[dict], current_prices: dict) -> list[float]:
        """Auto-settle trades whose markets no longer appear in active_markets."""
        active_ids = {m["market_id"] for m in active_markets}
        pnls = []
        for tid, trade in list(self._open_trades.items()):
            if trade["market_id"] not in active_ids:
                price = current_prices.get(trade["market_id"], trade["entry_price"])
                # Simulate outcome based on final price vs entry
                outcome = trade["direction"]
                if price > 0.5:
                    outcome = "YES"
                else:
                    outcome = "NO"
                pnl = self.settle(tid, outcome, price)
                if pnl is not None:
                    pnls.append(pnl)
        return pnls

    def get_metrics(self) -> dict:
        closed = self._closed_trades
        wins = [t for t in closed if t.get("pnl", 0) > 0]
        pnls = [t["pnl"] for t in closed if t.get("pnl") is not None]

        total_pnl = sum(pnls)
        win_rate = len(wins) / len(closed) * 100 if closed else 0.0

        # Sharpe ratio (simple daily)
        sharpe = 0.0
        if len(pnls) > 1:
            avg = sum(pnls) / len(pnls)
            std = math.sqrt(sum((p - avg) ** 2 for p in pnls) / len(pnls))
            sharpe = (avg / std * math.sqrt(252)) if std > 0 else 0.0

        # Max drawdown on bankroll trajectory
        peak = self._initial
        max_dd = 0.0
        running = self._initial
        for t in closed:
            running += t.get("pnl", 0)
            peak = max(peak, running)
            dd = (peak - running) / peak if peak > 0 else 0
            max_dd = max(max_dd, dd)

        avg_pnl = sum(pnls) / len(pnls) if pnls else 0.0

        return {
            "bankroll": round(self._bankroll, 4),
            "initial_bankroll": self._initial,
            "total_pnl": round(total_pnl, 4),
            "total_pnl_pct": round(total_pnl / self._initial * 100, 2) if self._initial else 0,
            "win_rate": round(win_rate, 1),
            "total_trades": len(closed),
            "open_trades": len(self._open_trades),
            "sharpe_ratio": round(sharpe, 3),
            "max_drawdown_pct": round(max_dd * 100, 2),
            "avg_trade_pnl": round(avg_pnl, 4),
        }
