"""
Melhorias 2, 5 — Circuit Breaker de erros de API + Kill Switch + saldo mínimo.
"""
import time
from datetime import datetime, date
from typing import Optional

from rich.console import Console

import config
import storage.db as db
from storage.logger import get_errors_logger

console = Console()
_log = get_errors_logger()


class CircuitBreakerError(Exception):
    """Levantada quando o circuit breaker está aberto."""


class RiskManager:
    def __init__(self):
        # Posições ativas
        self._active_positions: dict[str, dict] = {}  # market_id → position

        # Perdas consecutivas / cooldown de perdas
        self._consecutive_losses = 0
        self._cooldown_until: float = 0.0

        # Daily tracking
        self._daily_start_bankroll: float = 0.0
        self._daily_date: Optional[str] = None

        # ── Melhoria 2: circuit breaker ─────────────────────────────────
        self._api_errors_consecutive: int = 0
        self._circuit_open: bool = False
        self._circuit_open_until: float = 0.0

        # ── Melhoria 5: kill switch ──────────────────────────────────────
        self._kill_switch_triggered: bool = False
        self._kill_reason: str = ""

    # ── Daily tracking ────────────────────────────────────────────────────

    def reset_daily(self, bankroll: float):
        today = date.today().isoformat()
        if self._daily_date != today:
            self._daily_date = today
            self._daily_start_bankroll = bankroll

    # ── Cooldown por perdas consecutivas ─────────────────────────────────

    def is_in_cooldown(self) -> bool:
        if time.time() < self._cooldown_until:
            remaining = int(self._cooldown_until - time.time())
            console.print(f"[yellow][Risk] Cooldown: {remaining}s restantes[/yellow]")
            return True
        return False

    # ── Daily loss limit ─────────────────────────────────────────────────

    def is_daily_limit_hit(self, bankroll: float) -> bool:
        if self._daily_start_bankroll <= 0:
            return False
        loss_pct = (self._daily_start_bankroll - bankroll) / self._daily_start_bankroll
        if loss_pct >= config.DAILY_LOSS_LIMIT_PCT:
            msg = f"[Risk] Limite de perda diária! Perda: {loss_pct*100:.1f}%. Bot pausado."
            console.print(f"[red]{msg}[/red]")
            _log.error(msg, extra={"extra": {"bankroll": bankroll, "loss_pct": loss_pct}})
            return True
        return False

    # ── Melhoria 2: Circuit Breaker ──────────────────────────────────────

    def record_api_success(self):
        """Chamar após qualquer chamada de API bem-sucedida."""
        if self._api_errors_consecutive > 0:
            self._api_errors_consecutive = 0
        # Fecha o circuit breaker se o cooldown passou
        if self._circuit_open and time.time() > self._circuit_open_until:
            self._circuit_open = False
            console.print("[green][CircuitBreaker] Reativado — trading liberado[/green]")
            _log.info("[CircuitBreaker] Reativado")

    def record_api_error(self, retriable: bool = True, context: str = ""):
        """
        Registra erro de API.
        retriable=True: erro de rede (conta para o circuit breaker).
        retriable=False: erro lógico fatal (loga mas não abre circuit breaker).
        """
        if not retriable:
            msg = f"[CircuitBreaker] Erro fatal (não retriável): {context}"
            console.print(f"[red]{msg}[/red]")
            _log.error(msg)
            return

        self._api_errors_consecutive += 1
        msg = (
            f"[CircuitBreaker] Erro de API #{self._api_errors_consecutive}: {context}"
        )
        console.print(f"[yellow]{msg}[/yellow]")
        _log.warning(msg)

        if self._api_errors_consecutive >= config.CIRCUIT_BREAKER_API_ERRORS and not self._circuit_open:
            self._circuit_open = True
            self._circuit_open_until = time.time() + config.CIRCUIT_BREAKER_COOLDOWN_S
            alert = (
                f"[CircuitBreaker] ABERTO após {self._api_errors_consecutive} erros. "
                f"Cooldown {config.CIRCUIT_BREAKER_COOLDOWN_S // 60} min."
            )
            console.print(f"[bold red]{alert}[/bold red]")
            _log.error(alert)

    def is_circuit_open(self) -> bool:
        if self._circuit_open:
            if time.time() > self._circuit_open_until:
                self._circuit_open = False
                console.print("[green][CircuitBreaker] Cooldown expirado — reativando[/green]")
                return False
            remaining = int(self._circuit_open_until - time.time())
            console.print(f"[red][CircuitBreaker] Aberto — trading bloqueado. {remaining}s restantes[/red]")
            return True
        return False

    # ── Melhoria 5: Kill switch ──────────────────────────────────────────

    def check_minimum_balance(self, bankroll: float) -> bool:
        """Retorna True se o saldo estiver abaixo do mínimo configurado."""
        if bankroll <= config.MINIMUM_BALANCE_USDC:
            self.trigger_kill_switch(
                bankroll,
                f"Saldo ${bankroll:.2f} abaixo do mínimo ${config.MINIMUM_BALANCE_USDC:.2f}",
            )
            return True
        return False

    def trigger_kill_switch(self, bankroll: float, reason: str):
        """Aciona o kill switch manualmente (emergência ou saldo mínimo)."""
        if self._kill_switch_triggered:
            return
        self._kill_switch_triggered = True
        self._kill_reason = reason
        msg = f"[KillSwitch] ACIONADO — {reason}"
        console.print(f"[bold red]{msg}[/bold red]")
        _log.error(msg, extra={"extra": {"bankroll": bankroll, "reason": reason}})

    def reset_kill_switch(self):
        """Reset manual pelo operador."""
        self._kill_switch_triggered = False
        self._kill_reason = ""
        console.print("[green][KillSwitch] Resetado — trading pode ser retomado[/green]")

    @property
    def kill_switch_triggered(self) -> bool:
        return self._kill_switch_triggered

    @property
    def kill_reason(self) -> str:
        return self._kill_reason

    # ── Gate de abertura de posição ──────────────────────────────────────

    def can_open_position(self, market_id: str, bankroll: float) -> tuple[bool, str]:
        if self._kill_switch_triggered:
            return False, f"kill_switch: {self._kill_reason}"
        if self.is_circuit_open():
            return False, "circuit_breaker"
        if self.is_in_cooldown():
            return False, "cooldown"
        if self.is_daily_limit_hit(bankroll):
            return False, "daily_limit"
        if self.check_minimum_balance(bankroll):
            return False, "minimum_balance"
        if len(self._active_positions) >= config.MAX_SIMULTANEOUS_POSITIONS:
            return False, f"max_positions ({config.MAX_SIMULTANEOUS_POSITIONS})"
        if market_id in self._active_positions:
            return False, "already_in_market"
        return True, "ok"

    # ── Posições ─────────────────────────────────────────────────────────

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
                msg = (
                    f"[Risk] {self._consecutive_losses} perdas consecutivas. "
                    f"Cooldown {config.COOLDOWN_DURATION_SECONDS // 60} min."
                )
                console.print(f"[yellow]{msg}[/yellow]")
                _log.warning(msg)
        else:
            self._consecutive_losses = 0

    def close_all_positions(self) -> list[str]:
        """Fechar todas as posições abertas (kill switch de emergência)."""
        ids = list(self._active_positions.keys())
        for mid in ids:
            self._active_positions.pop(mid, None)
        if ids:
            console.print(f"[red][Risk] {len(ids)} posição(ões) fechada(s) via kill switch[/red]")
        return ids

    def check_stop_losses(self, market_prices: dict[str, float]) -> list[str]:
        to_close = []
        for market_id, pos in self._active_positions.items():
            entry = pos.get("entry_price", 0)
            current = market_prices.get(market_id, entry)
            if entry > 0:
                loss_pct = (entry - current) / entry
                if loss_pct >= config.STOP_LOSS_PCT:
                    console.print(
                        f"[red][Risk] Stop loss em {market_id[:20]}. "
                        f"Perda: {loss_pct*100:.1f}%[/red]"
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
            # Circuit breaker
            "circuit_open": self._circuit_open,
            "api_errors_consecutive": self._api_errors_consecutive,
            "circuit_open_until": self._circuit_open_until,
            # Kill switch
            "kill_switch": self._kill_switch_triggered,
            "kill_reason": self._kill_reason,
        }
