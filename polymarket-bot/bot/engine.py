import asyncio
import time
from datetime import datetime

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
from simulation.paper_trader import PaperTrader

console = Console()


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

        self._active_markets: list[dict] = []
        self._last_signals: list[dict] = []
        self._sse_queue: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._last_metrics_log = 0.0

        # Order deduplication: track (market_id, strategy) pairs already acted on this cycle
        self._acted_this_cycle: set[str] = set()

    # ------------------------------------------------------------------ #
    # Public controls                                                      #
    # ------------------------------------------------------------------ #

    def pause(self):
        self._paused = True
        console.print("[yellow][Engine] Bot paused[/yellow]")

    def resume(self):
        self._paused = False
        console.print("[green][Engine] Bot resumed[/green]")

    def is_paused(self) -> bool:
        return self._paused

    async def get_sse_event(self) -> dict:
        return await self._sse_queue.get()

    def _push_event(self, event_type: str, data: dict):
        try:
            self._sse_queue.put_nowait({"type": event_type, "data": data, "ts": time.time()})
        except asyncio.QueueFull:
            pass

    # ------------------------------------------------------------------ #
    # Startup                                                              #
    # ------------------------------------------------------------------ #

    async def start(self):
        db.init_db()
        self._running = True
        await self.polymarket.start()
        await self.binance.start()
        await self.news.start()
        await self.copy_feed.start()

        console.print("[bold cyan]⚡ Polymarket Bot starting…[/bold cyan]")
        console.print(
            f"  Mode: [bold {'yellow' if self._simulation_mode else 'red'}]"
            f"{'SIMULATION' if self._simulation_mode else '⚠ LIVE TRADING'}[/bold]"
        )
        console.print("  Strategies: ARB · CORR_ARB · MARKET_MAKING · MOMENTUM · COPY · MEAN_REV")

        # Let Binance WS fill the candle buffer
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

    # ------------------------------------------------------------------ #
    # Main loop                                                            #
    # ------------------------------------------------------------------ #

    async def _main_loop(self):
        while self._running:
            try:
                if not self._paused:
                    await self._tick()
            except Exception as exc:
                console.print(f"[red][Engine] Tick error: {exc}[/red]")
            await asyncio.sleep(config.ENGINE_LOOP_INTERVAL)

    async def _tick(self):
        self._acted_this_cycle.clear()

        # 1. Discover active markets
        markets = await self.polymarket.find_active_crypto_markets()
        self._active_markets = markets
        if not markets:
            console.print("[yellow][Engine] No active crypto markets found[/yellow]")
            return

        bankroll = self.paper.bankroll if self._simulation_mode else 0.0
        self.risk.reset_daily(bankroll)
        if self.risk.is_daily_limit_hit(bankroll):
            self._push_event("daily_limit", {"bankroll": bankroll})
            return

        # 2. Gather copy-trade signals
        copy_signals = self.copy_feed.get_signals()

        signals = []
        for market in markets[:20]:
            # 3. Generate technical signal
            signal = self.signal_gen.generate(market)

            # News-adjust confidence
            asset = signal.get("asset", "")
            if asset and asset != "UNKNOWN":
                symbol = asset.replace("USDT", "")
                signal["confidence"] = self.news.adjust_confidence(
                    symbol, signal["direction"], signal["confidence"]
                )

            db.save_signal(signal)
            signals.append(signal)

            # 4. Log arbitrage opportunities
            arb = self.polymarket.detect_arbitrage(market)
            if arb:
                console.print(
                    f"[bold green][ARB] {arb['question'][:50]} "
                    f"profit={arb['guaranteed_profit']*100:.2f}%[/bold green]"
                )

            # 5. Evaluate all strategies
            recommendations = evaluate_all_strategies(
                market,
                signal,
                related_markets=markets,          # enables CORRELATION_ARB
                news_sentiment=self.news,
                copy_signals=copy_signals,
            )

            # 6. Risk filter + deduplication + execute
            for rec in recommendations:
                dedup_key = f"{market['market_id']}:{rec['strategy']}"
                if dedup_key in self._acted_this_cycle:
                    continue

                ok, reason = self.risk.can_open_position(market["market_id"], bankroll)
                if not ok:
                    console.print(f"[dim][Risk] Skipping {rec['strategy']} — {reason}[/dim]")
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
                        self._push_event("trade_opened", trade)
                else:
                    console.print(
                        f"[bold red][LIVE] Would execute: {rec['strategy']} "
                        f"on {market['market_id'][:20]}[/bold red]"
                    )
                    self._acted_this_cycle.add(dedup_key)
                break  # one recommendation per market per tick

        self._last_signals = signals

        # 7. Auto-settle expired paper trades
        if self._simulation_mode:
            current_prices = {m["market_id"]: m.get("price_yes", 0.5) for m in markets}
            pnls = self.paper.auto_settle_expired(markets, current_prices)
            for pnl in pnls:
                self.risk.register_close("unknown", pnl)

        # 8. Push SSE updates
        metrics = self.paper.get_metrics()
        db.save_metrics(
            {
                **metrics,
                "timestamp": datetime.utcnow().isoformat(),
                "active_positions": metrics["open_trades"],
            }
        )
        self._push_event("metrics_update", metrics)
        self._push_event("signals_update", {"signals": signals[-10:]})
        self._push_event(
            "copy_signals",
            {"signals": copy_signals[:5], "stats": self.copy_feed.get_wallet_stats()},
        )

    # ------------------------------------------------------------------ #
    # Metrics logger                                                       #
    # ------------------------------------------------------------------ #

    async def _metrics_logger(self):
        while self._running:
            now = time.time()
            if now - self._last_metrics_log >= config.METRICS_LOG_INTERVAL:
                self._last_metrics_log = now
                metrics = self.paper.get_metrics() if self._simulation_mode else {}
                self._print_metrics_table(metrics)
            await asyncio.sleep(5)

    def _print_metrics_table(self, metrics: dict):
        table = Table(title="📊 Bot Metrics", style="cyan")
        table.add_column("Metric", style="bold")
        table.add_column("Value")
        for k, v in metrics.items():
            color = ""
            if "pnl" in k.lower() and isinstance(v, (int, float)):
                color = "green" if v >= 0 else "red"
            val_str = f"[{color}]{v}[/{color}]" if color else str(v)
            table.add_row(k.replace("_", " ").title(), val_str)
        console.print(table)

    # ------------------------------------------------------------------ #
    # Data accessors for dashboard                                         #
    # ------------------------------------------------------------------ #

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
