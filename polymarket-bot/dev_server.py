"""
Dev server — roda apenas o dashboard FastAPI com dados mock simulados.
Usado para preview/desenvolvimento sem precisar do Binance/Polymarket conectados.
"""
import asyncio
import random
import sys
import time
from datetime import datetime

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
                "timestamp": datetime.utcnow().isoformat(),
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
                "timestamp": datetime.utcnow().isoformat(),
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
        pnl = round(self._bankroll - 100.0, 4)
        return {
            "bankroll": round(self._bankroll, 2),
            "initial_bankroll": 100.0,
            "total_pnl": pnl,
            "total_pnl_pct": round(pnl, 2),
            "win_rate": 57.3,
            "total_trades": 20 + self._tick,
            "open_trades": random.randint(0, 3),
            "sharpe_ratio": 1.42,
            "max_drawdown_pct": 4.8,
            "avg_trade_pnl": 0.0023,
            "today_trades": 8 + self._tick,
            "risk": {"in_cooldown": False, "consecutive_losses": 0},
            "mode": "simulation",
            "paused": self._paused,
        }

    def get_signals(self):
        return [
            {"asset": "BTCUSDT", "direction": "UP",   "confidence": 0.71, "rsi": 28.4, "momentum":  0.23, "indicators": {"rsi": 28.4}},
            {"asset": "ETHUSDT", "direction": "DOWN",  "confidence": 0.63, "rsi": 72.1, "momentum": -0.18, "indicators": {"rsi": 72.1}},
            {"asset": "BTCUSDT", "direction": "NEUTRAL","confidence": 0.35, "rsi": 51.0, "momentum":  0.02, "indicators": {"rsi": 51.0}},
        ]

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

    async def push_live_updates(self):
        """Simula eventos SSE em tempo real."""
        strategies = ["ARBITRAGE", "MOMENTUM", "MARKET_MAKING", "CORRELATION_ARB"]
        questions = [
            "Will BTC be above $70,000 at 3:05 PM?",
            "Will ETH be above $3,500 at 3:10 PM?",
            "Will BTC reach $71,000 before 4 PM?",
        ]
        while True:
            await asyncio.sleep(8)
            self._tick += 1

            # Simula novo trade
            pnl = round(random.uniform(-0.08, 0.12), 4)
            self._bankroll += pnl
            trade = {
                "market_id": f"live-{self._tick}",
                "question": random.choice(questions),
                "direction": random.choice(["YES", "NO"]),
                "size": round(random.uniform(0.4, 1.0) * config.MAX_POSITION_SIZE, 3),
                "entry_price": round(random.uniform(0.38, 0.62), 3),
                "strategy": random.choice(strategies),
                "pnl": pnl,
                "status": "open",
            }
            db.save_trade({**trade, "timestamp": datetime.utcnow().isoformat(),
                           "exit_price": None, "simulated": True})
            db.save_metrics({
                "timestamp": datetime.utcnow().isoformat(),
                "bankroll": self._bankroll,
                "total_pnl": self._bankroll - 100.0,
                "win_rate": 57.0,
                "active_positions": 2,
            })

            try:
                self._sse_queue.put_nowait({"type": "trade_opened", "data": trade, "ts": time.time()})
                self._sse_queue.put_nowait({"type": "metrics_update", "data": self.get_metrics(), "ts": time.time()})
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
