"""
Dev server — roda apenas o dashboard FastAPI com dados mock simulados.
Usado para preview/desenvolvimento sem precisar do Binance/Polymarket conectados.
"""
import asyncio
import random
import sys
import time
from datetime import datetime, timezone

# Evita UnicodeEncodeError do rich em consoles Windows (cp1252)
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import config
import storage.db as db
from dashboard.server import app, set_engine


class MockEngine:
    """Engine falso com dados realistas para preview do dashboard."""

    def __init__(self):
        self._bankroll = 100.0
        self._initial  = 100.0    # necessário para cálculo de métricas
        self._trades = []
        self._signals = []
        self._paused = False
        self._sse_queue: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._tick = 0

        # Pré-popular o banco com dados históricos fictícios
        db.init_db()
        self._seed_data()

    def _seed_data(self):
        strategies = ["ARBITRAGE", "MOMENTUM", "MARKET_MAKING", "CORRELATION_ARB", "MEAN_REVERSION"]
        questions = [
            "Will BTC be above $70,000 at 3:05 PM?",
            "Will ETH be above $3,500 at 3:10 PM?",
            "Will BTC be above $69,500 at 3:15 PM?",
            "Will ETH price increase in the next 5 minutes?",
            "Will BTC reach $71,000 before 4 PM?",
        ]
        self._bankroll = 100.0
        for i in range(20):
            strategy = random.choice(strategies)
            question = random.choice(questions)
            direction = random.choice(["YES", "NO", "BOTH"])
            # Tamanho escala com o limite configurado (40%-100% do máximo)
            size = round(random.uniform(0.4, 1.0) * config.MAX_POSITION_SIZE, 3)
            entry = round(random.uniform(0.35, 0.65), 3)
            won = random.random() > 0.42
            exit_p = round(entry + random.uniform(0.05, 0.25) if won else entry - random.uniform(0.05, 0.2), 3)
            pnl = round(size * (exit_p / entry - 1), 4)
            self._bankroll += pnl
            db.save_trade({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "market_id": f"mock-market-{i}",
                "question": question,
                "direction": direction,
                "size": size,
                "entry_price": entry,
                "exit_price": exit_p,
                "pnl": pnl,
                "strategy": strategy,
                "simulated": True,
                "status": "closed",
            })
            db.save_metrics({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "bankroll": self._bankroll,
                "total_pnl": self._bankroll - 100.0,
                "win_rate": 55.0,
                "active_positions": 0,
            })

    def pause(self):
        self._paused = True

    def resume(self):
        self._paused = False

    def is_paused(self):
        return self._paused

    async def get_sse_event(self):
        return await self._sse_queue.get()

    def get_metrics(self):
        # Calcula tudo dinamicamente a partir do SQLite — sem hardcode
        trades = db.get_trades(limit=500)
        closed = [t for t in trades if t.get("status") == "closed" and t.get("pnl") is not None]
        wins   = [t for t in closed if t["pnl"] > 0]
        pnls   = [t["pnl"] for t in closed]

        win_rate      = round(len(wins) / len(closed) * 100, 1) if closed else 0.0
        total_pnl     = round(sum(pnls), 4)
        avg_trade_pnl = round(total_pnl / len(pnls), 4) if pnls else 0.0

        # Sharpe simplificado
        import math
        sharpe = 0.0
        if len(pnls) > 1:
            avg = sum(pnls) / len(pnls)
            std = math.sqrt(sum((p - avg) ** 2 for p in pnls) / len(pnls))
            sharpe = round((avg / std * math.sqrt(252)), 3) if std > 0 else 0.0

        # Max drawdown
        peak, running, max_dd = self._initial, self._initial, 0.0
        for t in closed:
            running += t["pnl"]
            peak = max(peak, running)
            dd = (peak - running) / peak if peak > 0 else 0
            max_dd = max(max_dd, dd)

        from datetime import date
        today = date.today().isoformat()
        today_trades = sum(1 for t in trades if t.get("timestamp", "").startswith(today))

        return {
            "bankroll":         round(self._bankroll, 2),
            "initial_bankroll": self._initial,
            "total_pnl":        total_pnl,
            "total_pnl_pct":    round(total_pnl / self._initial * 100, 2) if self._initial else 0,
            "win_rate":         win_rate,
            "total_trades":     len(closed),
            "open_trades":      len([t for t in trades if t.get("status") == "open"]),
            "sharpe_ratio":     sharpe,
            "max_drawdown_pct": round(max_dd * 100, 2),
            "avg_trade_pnl":    avg_trade_pnl,
            "today_trades":     today_trades,
            "risk":  {"in_cooldown": False, "consecutive_losses": 0},
            "mode":  "simulation",
            "paused": self._paused,
        }

    def get_signals(self):
        # Lê os últimos sinais reais do SQLite
        return db.get_recent_signals(limit=10)

    def get_positions(self):
        return [
            {"strategy": "MOMENTUM", "size": 0.8, "entry_price": 0.42},
            {"strategy": "ARBITRAGE", "size": 0.5, "entry_price": 0.46},
        ]

    def get_copy_signals(self):
        return [
            {"wallet": "0xaBc1…", "question": "Will BTC be above $70k at 3:05 PM?",
             "direction": "YES", "entry_price": 0.44, "wallet_win_rate": 68},
        ]

    def get_news_sentiment(self):
        return {
            "BTC": {"asset": "BTC", "score": 0.31, "direction": "BULLISH",
                    "headlines": ["Bitcoin ETF inflows hit record high"],
                    "age_seconds": 45},
            "ETH": {"asset": "ETH", "score": -0.12, "direction": "NEUTRAL",
                    "headlines": ["Ethereum gas fees spike on high activity"],
                    "age_seconds": 60},
        }

    def get_health(self):
        import time
        return {
            "last_tick_at": time.time() - 5,
            "last_tick_age_s": 5.0,
            "last_signal_at": time.time() - 8,
            "last_signal_age_s": 8.0,
            "tick_count": self._tick,
            "binance_ws": {
                "connected": True,
                "reconnect_attempts": 0,
                "last_connected_at": time.time() - 60,
                "last_message_age_s": 2.1,
            },
            "circuit_breaker_open": False,
            "kill_switch": False,
            "paused": self._paused,
            "mode": "simulation",
        }

    # Kill switch e circuit breaker (mock)
    class _MockRisk:
        kill_switch_triggered = False
        kill_reason = ""
        def reset_kill_switch(self): pass

    risk = _MockRisk()

    async def emergency_stop(self):
        return {"closed_positions": 0, "reason": "Mock — sem posições abertas"}

    def switch_mode(self, live: bool):
        import config
        config.SIMULATION_MODE = not live

    async def push_live_updates(self):
        """Simula eventos SSE com trades completos (abertos e fechados)."""
        strategies = ["ARBITRAGE", "MOMENTUM", "MARKET_MAKING", "CORRELATION_ARB"]
        questions = [
            "Will BTC be above $70,000 at 3:05 PM?",
            "Will ETH be above $3,500 at 3:10 PM?",
            "Will BTC reach $71,000 before 4 PM?",
        ]
        while True:
            await asyncio.sleep(8)
            self._tick += 1

            # Trade com resultado completo (closed) — afeta PnL e win_rate
            pnl        = round(random.uniform(-0.08, 0.12), 4)
            entry      = round(random.uniform(0.38, 0.62), 3)
            exit_price = round(entry + pnl / config.MAX_POSITION_SIZE, 3)
            size       = round(random.uniform(0.4, 1.0) * config.MAX_POSITION_SIZE, 3)

            self._bankroll += pnl

            trade = {
                "market_id":   f"live-{self._tick}",
                "question":    random.choice(questions),
                "direction":   random.choice(["YES", "NO"]),
                "size":        size,
                "entry_price": entry,
                "exit_price":  exit_price,
                "strategy":    random.choice(strategies),
                "pnl":         pnl,
                "status":      "closed",   # ← closed para entrar no cálculo
                "simulated":   True,
                "timestamp":   datetime.now(timezone.utc).isoformat(),
            }
            db.save_trade(trade)

            # Calcula métricas reais do banco (sem hardcode)
            metrics = self.get_metrics()
            db.save_metrics({
                "timestamp":        datetime.now(timezone.utc).isoformat(),
                "bankroll":         metrics["bankroll"],
                "total_pnl":        metrics["total_pnl"],
                "win_rate":         metrics["win_rate"],
                "active_positions": metrics["open_trades"],
            })

            try:
                self._sse_queue.put_nowait({"type": "trade_opened",   "data": trade,   "ts": time.time()})
                self._sse_queue.put_nowait({"type": "metrics_update", "data": metrics, "ts": time.time()})
            except asyncio.QueueFull:
                pass


async def lifespan_task(engine: MockEngine):
    asyncio.create_task(engine.push_live_updates())


if __name__ == "__main__":
    import uvicorn
    from contextlib import asynccontextmanager
    from fastapi import FastAPI as _FA

    engine = MockEngine()
    set_engine(engine)

    @asynccontextmanager
    async def lifespan(_app):
        asyncio.create_task(engine.push_live_updates())
        yield

    app.router.lifespan_context = lifespan

    uvicorn.run(app, host="0.0.0.0", port=8099, log_level="warning")
