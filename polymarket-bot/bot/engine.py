"""
Engine principal do bot.
Integra melhorias: circuit breaker, kill switch, health check,
logs estruturados, alertas Telegram.
"""
import asyncio
import time
from datetime import datetime, timezone

from rich.console import Console
from rich.table import Table

import config
import storage.db as db
from bot.risk_manager import RiskManager
from bot.signal_generator import SignalGenerator
from bot.strategies import evaluate_all_strategies
from feeds.binance_ws import BinanceWebSocket
from feeds.copy_trading import CopyTradingFeed
from feeds.news_sentiment import NewsSentiment
from feeds.polymarket_api import PolymarketAPI
from notifications.telegram_notifier import TelegramNotifier
from simulation.paper_trader import PaperTrader
from storage.logger import get_errors_logger, get_signals_logger, log_trade

console = Console()
_err_log = get_errors_logger()
_sig_log = get_signals_logger()


class BotEngine:
    def __init__(self, simulation_mode: bool = True):
        self._simulation_mode = simulation_mode
        self._paused = False
        self._running = False

        self.binance = BinanceWebSocket()
        self.polymarket = PolymarketAPI()
        self.signal_gen = SignalGenerator(self.binance)
        self.risk = RiskManager()
        self.paper = PaperTrader()
        self.news = NewsSentiment()
        self.copy_feed = CopyTradingFeed()
        self.telegram = TelegramNotifier()

        self._active_markets: list[dict] = []
        self._last_signals: list[dict] = []
        self._sse_queue: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._last_metrics_log = 0.0
        self._acted_this_cycle: set[str] = set()

        # ── Melhoria 9: health check ────────────────────────────────────
        self._last_tick_at: float = 0.0
        self._last_signal_at: float = 0.0
        self._tick_count: int = 0

    # ── Controles públicos ────────────────────────────────────────────────

    def pause(self):
        self._paused = True
        console.print("[yellow][Engine] Bot pausado[/yellow]")

    def resume(self):
        self._paused = False
        console.print("[green][Engine] Bot retomado[/green]")

    def is_paused(self) -> bool:
        return self._paused

    # ── Melhoria 4: toggle de modo em runtime ────────────────────────────

    def switch_mode(self, live: bool):
        """Altera simulação ↔ live em runtime (após confirmação pelo dashboard)."""
        config.SIMULATION_MODE = not live
        self._simulation_mode = not live
        mode_str = "live" if live else "simulation"
        console.print(
            f"[{'bold red' if live else 'yellow'}][Engine] Modo alterado para {mode_str.upper()}[/bold {'bold red' if live else 'yellow'}]"
        )
        self.telegram.notify_mode_change(mode_str)

    # ── Melhoria 5: kill switch manual ───────────────────────────────────

    async def emergency_stop(self) -> dict:
        """Fecha todas as posições abertas imediatamente (botão de emergência)."""
        bankroll = self.paper.bankroll if self._simulation_mode else 0.0
        reason = "Emergência manual pelo dashboard"
        self.risk.trigger_kill_switch(bankroll, reason)

        closed_ids = self.risk.close_all_positions()
        # Registra fechamento de emergência no banco
        for mid in closed_ids:
            db.update_trade(-1, 0.0, 0.0, "emergency_closed")

        self.telegram.notify_kill_switch(bankroll, reason)
        self._push_event("kill_switch", {"reason": reason, "closed": len(closed_ids)})
        return {"closed_positions": len(closed_ids), "reason": reason}

    # ── SSE ───────────────────────────────────────────────────────────────

    async def get_sse_event(self) -> dict:
        return await self._sse_queue.get()

    def _push_event(self, event_type: str, data: dict):
        try:
            self._sse_queue.put_nowait({"type": event_type, "data": data, "ts": time.time()})
        except asyncio.QueueFull:
            pass

    # ── Startup ───────────────────────────────────────────────────────────

    async def start(self):
        db.init_db()
        self._running = True
        await self.polymarket.start()
        await self.binance.start()
        await self.news.start()
        await self.copy_feed.start()
        await self.telegram.start()

        console.print("[bold cyan]Polymarket Bot iniciando…[/bold cyan]")
        console.print(
            f"  Modo: [bold {'yellow' if self._simulation_mode else 'red'}]"
            f"{'SIMULAÇÃO' if self._simulation_mode else 'LIVE TRADING'}[/bold]"
        )
        console.print("  Estratégias: ARB · CORR_ARB · MARKET_MAKING · MOMENTUM · COPY · MEAN_REV")

        await asyncio.sleep(5)
        await asyncio.gather(
            self._main_loop(),
            self._metrics_logger(),
        )

    async def stop(self):
        self._running = False
        await self.binance.stop()
        await self.polymarket.stop()
        await self.news.stop()
        await self.copy_feed.stop()
        await self.telegram.stop()

    # ── Main loop ────────────────────────────────────────────────────────

    async def _main_loop(self):
        while self._running:
            try:
                if not self._paused and not self.risk.kill_switch_triggered:
                    await self._tick()
            except Exception as exc:
                msg = f"[Engine] Erro no tick: {exc}"
                console.print(f"[red]{msg}[/red]")
                _err_log.error(msg, exc_info=True)
                self.risk.record_api_error(retriable=True, context=str(exc))
                self.telegram.notify_bot_error(str(exc))
            await asyncio.sleep(config.ENGINE_LOOP_INTERVAL)

    async def _tick(self):
        self._acted_this_cycle.clear()
        self._last_tick_at = time.time()
        self._tick_count += 1

        # Melhoria 2: circuit breaker abre o loop
        if self.risk.is_circuit_open():
            return

        # 1. Mercados ativos
        try:
            markets = await self.polymarket.find_active_crypto_markets()
            self.risk.record_api_success()
        except Exception as exc:
            self.risk.record_api_error(retriable=True, context=f"find_markets: {exc}")
            if self.risk.is_circuit_open():
                self.telegram.notify_circuit_breaker(self.risk._api_errors_consecutive)
            return

        self._active_markets = markets
        if not markets:
            return

        bankroll = self.paper.bankroll if self._simulation_mode else 0.0
        self.risk.reset_daily(bankroll)

        # Melhoria 5: saldo mínimo
        if self.risk.check_minimum_balance(bankroll):
            self.telegram.notify_kill_switch(bankroll, self.risk.kill_reason)
            self._push_event("kill_switch", {"reason": self.risk.kill_reason, "bankroll": bankroll})
            return

        if self.risk.is_daily_limit_hit(bankroll):
            self.telegram.notify_daily_limit(
                bankroll,
                (self.risk._daily_start_bankroll - bankroll) / max(self.risk._daily_start_bankroll, 1),
            )
            self._push_event("daily_limit", {"bankroll": bankroll})
            return

        copy_signals = self.copy_feed.get_signals()
        signals = []

        for market in markets[:20]:
            signal = self.signal_gen.generate(market)
            asset = signal.get("asset", "")
            if asset and asset != "UNKNOWN":
                symbol = asset.replace("USDT", "")
                signal["confidence"] = self.news.adjust_confidence(
                    symbol, signal["direction"], signal["confidence"]
                )

            db.save_signal(signal)
            # Melhoria 7: log estruturado de sinais
            _sig_log.debug("signal", extra={"extra": {
                "asset": signal.get("asset"),
                "direction": signal.get("direction"),
                "confidence": signal.get("confidence"),
                "rsi": signal.get("rsi"),
            }})
            signals.append(signal)
            self._last_signal_at = time.time()

            arb = self.polymarket.detect_arbitrage(market)
            if arb:
                console.print(
                    f"[bold green][ARB] {arb['question'][:50]} "
                    f"lucro={arb['guaranteed_profit']*100:.2f}%[/bold green]"
                )

            recommendations = evaluate_all_strategies(
                market, signal,
                related_markets=markets,
                news_sentiment=self.news,
                copy_signals=copy_signals,
            )

            for rec in recommendations:
                dedup_key = f"{market['market_id']}:{rec['strategy']}"
                if dedup_key in self._acted_this_cycle:
                    continue

                ok, reason = self.risk.can_open_position(market["market_id"], bankroll)
                if not ok:
                    break

                if self._simulation_mode:
                    trade = self.paper.execute(rec)
                    if trade:
                        self._acted_this_cycle.add(dedup_key)
                        self.risk.register_open(
                            market["market_id"],
                            {
                                "entry_price": trade.get("entry_price", 0),
                                "size": trade.get("size", 0),
                                "strategy": rec["strategy"],
                            },
                        )
                        # Melhoria 7: log de trade
                        log_trade(trade)
                        # Melhoria 8: alerta Telegram para trades grandes
                        self.telegram.notify_trade(trade)
                        self._push_event("trade_opened", trade)
                else:
                    console.print(
                        f"[bold red][LIVE] Executaria: {rec['strategy']} "
                        f"em {market['market_id'][:20]}[/bold red]"
                    )
                    self._acted_this_cycle.add(dedup_key)
                break

        self._last_signals = signals

        if self._simulation_mode:
            current_prices = {m["market_id"]: m.get("price_yes", 0.5) for m in markets}
            pnls = self.paper.auto_settle_expired(markets, current_prices)
            for pnl in pnls:
                self.risk.register_close("unknown", pnl)

        metrics = self.paper.get_metrics()
        db.save_metrics({
            **metrics,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "active_positions": metrics["open_trades"],
        })
        self._push_event("metrics_update", metrics)
        self._push_event("signals_update", {"signals": signals[-10:]})
        self._push_event("copy_signals", {
            "signals": copy_signals[:5],
            "stats": self.copy_feed.get_wallet_stats(),
        })
        self._push_event("health_update", self._build_health())

    # ── Melhoria 9: health check ──────────────────────────────────────────

    def _build_health(self) -> dict:
        now = time.time()
        return {
            "last_tick_at": self._last_tick_at,
            "last_tick_age_s": round(now - self._last_tick_at, 1) if self._last_tick_at else None,
            "last_signal_at": self._last_signal_at,
            "last_signal_age_s": round(now - self._last_signal_at, 1) if self._last_signal_at else None,
            "tick_count": self._tick_count,
            "binance_ws": self.binance.health(),
            "circuit_breaker_open": self.risk.is_circuit_open(),
            "kill_switch": self.risk.kill_switch_triggered,
            "paused": self._paused,
            "mode": "simulation" if self._simulation_mode else "live",
        }

    # ── Metrics logger ────────────────────────────────────────────────────

    async def _metrics_logger(self):
        while self._running:
            now = time.time()
            if now - self._last_metrics_log >= config.METRICS_LOG_INTERVAL:
                self._last_metrics_log = now
                metrics = self.paper.get_metrics() if self._simulation_mode else {}
                try:
                    self._print_metrics_table(metrics)
                except Exception:
                    pass
            await asyncio.sleep(5)

    def _print_metrics_table(self, metrics: dict):
        table = Table(title="Bot Métricas", style="cyan")
        table.add_column("Métrica", style="bold")
        table.add_column("Valor")
        for k, v in metrics.items():
            color = ""
            if "pnl" in k.lower() and isinstance(v, (int, float)):
                color = "green" if v >= 0 else "red"
            val_str = f"[{color}]{v}[/{color}]" if color else str(v)
            table.add_row(k.replace("_", " ").title(), val_str)
        console.print(table)

    # ── Accessors do dashboard ────────────────────────────────────────────

    def get_metrics(self) -> dict:
        m = self.paper.get_metrics() if self._simulation_mode else {
            "bankroll": 0, "total_pnl": 0, "win_rate": 0, "open_trades": 0
        }
        m["risk"] = self.risk.get_status()
        m["mode"] = "simulation" if self._simulation_mode else "live"
        m["paused"] = self._paused
        return m

    def get_signals(self) -> list[dict]:
        return self._last_signals

    def get_positions(self) -> list[dict]:
        return self.risk.get_active_positions()

    def get_copy_signals(self) -> list[dict]:
        return self.copy_feed.get_signals()

    def get_news_sentiment(self) -> dict:
        return {
            "BTC": self.news.get_sentiment("BTC"),
            "ETH": self.news.get_sentiment("ETH"),
        }

    def get_health(self) -> dict:
        return self._build_health()
