import time
from datetime import datetime, date
from typing import Optional

from rich.console import Console

import config
import storage.db as db

console = Console()


class RiskManager:
    def __init__(self):
        self._active_positions: dict[str, dict] = {}  # market_id → position
        self._consecutive_losses = 0
        self._cooldown_until: float = 0.0
        self._daily_start_bankroll: float = 0.0
        self._daily_date: Optional[str] = None

    def reset_daily(self, bankroll: float):
        today = date.today().isoformat()
        if self._daily_date != today:
            self._daily_date = today
            self._daily_start_bankroll = bankroll

    def is_in_cooldown(self) -> bool:
        if time.time() < self._cooldown_until:
            remaining = int(self._cooldown_until - time.time())
            console.print(f"[yellow][Risk] Bot in cooldown for {remaining}s more[/yellow]")
            return True
        return False

    def is_daily_limit_hit(self, bankroll: float) -> bool:
        if self._daily_start_bankroll <= 0:
            return False
        loss_pct = (self._daily_start_bankroll - bankroll) / self._daily_start_bankroll
        if loss_pct >= config.DAILY_LOSS_LIMIT_PCT:
            console.print(
                f"[red][Risk] Daily loss limit reached! Lost {loss_pct*100:.1f}% today. Bot stopped.[/red]"
            )
            return True
        return False

    def can_open_position(self, market_id: str, bankroll: float) -> tuple[bool, str]:
        """Check all risk rules before opening a position."""
        if self.is_in_cooldown():
            return False, "cooldown"
        if self.is_daily_limit_hit(bankroll):
            return False, "daily_limit"
        if len(self._active_positions) >= config.MAX_SIMULTANEOUS_POSITIONS:
            return False, f"max_positions ({config.MAX_SIMULTANEOUS_POSITIONS})"
        if market_id in self._active_positions:
            return False, "already_in_market"
        return True, "ok"

    def register_open(self, market_id: str, position: dict):
        self._active_positions[market_id] = {
            **position,
            "opened_at": time.time(),
        }

    def register_close(self, market_id: str, pnl: float):
        self._active_positions.pop(market_id, None)
        if pnl < 0:
            self._consecutive_losses += 1
            if self._consecutive_losses >= config.COOLDOWN_AFTER_LOSSES:
                self._cooldown_until = time.time() + config.COOLDOWN_DURATION_SECONDS
                console.print(
                    f"[yellow][Risk] {self._consecutive_losses} consecutive losses. "
                    f"Cooling down for {config.COOLDOWN_DURATION_SECONDS // 60} min.[/yellow]"
                )
        else:
            self._consecutive_losses = 0

    def check_stop_losses(self, market_prices: dict[str, float]) -> list[str]:
        """Return list of market_ids that hit stop loss."""
        to_close = []
        for market_id, pos in self._active_positions.items():
            entry = pos.get("entry_price", 0)
            current = market_prices.get(market_id, entry)
            if entry > 0:
                loss_pct = (entry - current) / entry
                if loss_pct >= config.STOP_LOSS_PCT:
                    console.print(
                        f"[red][Risk] Stop loss triggered for {market_id[:20]}. "
                        f"Loss: {loss_pct*100:.1f}%[/red]"
                    )
                    to_close.append(market_id)
        return to_close

    def get_active_positions(self) -> list[dict]:
        return list(self._active_positions.values())

    def get_status(self) -> dict:
        return {
            "active_positions": len(self._active_positions),
            "consecutive_losses": self._consecutive_losses,
            "in_cooldown": self.is_in_cooldown(),
            "cooldown_until": self._cooldown_until,
        }
