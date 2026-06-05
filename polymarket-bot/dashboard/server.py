"""
Dashboard FastAPI.
Melhorias: toggle simulação↔live (4), kill switch (5), health check (9).
"""
import asyncio
import json
import time
from typing import AsyncGenerator

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse, FileResponse
from pathlib import Path

import config
import storage.db as db

app = FastAPI(title="Polymarket Bot Dashboard", docs_url=None, redoc_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = Path(__file__).parent / "static"
_engine = None


def set_engine(engine):
    global _engine
    _engine = engine


# ── Métricas ──────────────────────────────────────────────────────────────

@app.get("/api/metrics")
async def get_metrics():
    if _engine:
        return _engine.get_metrics()
    return db.get_metrics(100.0)


@app.get("/api/trades")
async def get_trades(limit: int = 50):
    return db.get_trades(limit)


@app.get("/api/positions")
async def get_positions():
    if _engine:
        return _engine.get_positions()
    return db.get_open_trades()


@app.get("/api/signals")
async def get_signals():
    if _engine:
        return _engine.get_signals()
    return db.get_recent_signals()


@app.get("/api/equity")
async def get_equity():
    return db.get_metrics_history(200)


@app.get("/api/copy-signals")
async def get_copy_signals():
    if _engine:
        return {"signals": _engine.get_copy_signals()}
    return {"signals": []}


@app.get("/api/news-sentiment")
async def get_news_sentiment():
    if _engine:
        return _engine.get_news_sentiment()
    return {"BTC": None, "ETH": None}


# ── Controles ─────────────────────────────────────────────────────────────

@app.post("/api/pause")
async def pause_bot():
    if _engine:
        _engine.pause()
    return {"status": "paused"}


@app.post("/api/resume")
async def resume_bot():
    if _engine:
        _engine.resume()
    return {"status": "resumed"}


# ── Melhoria 4: toggle simulação ↔ live ──────────────────────────────────

@app.post("/api/mode/toggle")
async def toggle_mode(payload: dict):
    """
    Alterna entre modo simulação e live.
    Requer confirmação obrigatória no payload:
      {"target": "live", "confirmation": "CONFIRMAR"}   ← para ativar live
      {"target": "simulation"}                          ← para voltar a sim
    """
    target = payload.get("target", "simulation")
    confirmation = payload.get("confirmation", "")

    if target == "live":
        if confirmation != config.LIVE_MODE_PASSWORD:
            raise HTTPException(
                status_code=403,
                detail=f"Confirmação incorreta. Digite '{config.LIVE_MODE_PASSWORD}' para ativar.",
            )
        if not config.POLYMARKET_PK:
            raise HTTPException(
                status_code=400,
                detail="POLYMARKET_PK não configurada. Defina a variável de ambiente antes de ativar o modo live.",
            )

    if _engine:
        _engine.switch_mode(live=(target == "live"))
    else:
        config.SIMULATION_MODE = (target != "live")

    return {
        "mode": target,
        "simulation": config.SIMULATION_MODE,
        "status": "ok",
    }


@app.get("/api/mode")
async def get_mode():
    return {
        "simulation": config.SIMULATION_MODE,
        "mode": "simulation" if config.SIMULATION_MODE else "live",
        "live_password_set": bool(config.LIVE_MODE_PASSWORD),
        "pk_configured": bool(config.POLYMARKET_PK),
    }


# ── Melhoria 5: kill switch ───────────────────────────────────────────────

@app.post("/api/kill-switch")
async def trigger_kill_switch(payload: dict | None = None):
    """Fecha todas as posições abertas imediatamente."""
    if _engine:
        result = await _engine.emergency_stop()
        return result
    return {"closed_positions": 0, "reason": "Engine não iniciada"}


@app.post("/api/kill-switch/reset")
async def reset_kill_switch():
    """Reseta o kill switch (permite retomar trading)."""
    if _engine:
        _engine.risk.reset_kill_switch()
    return {"status": "reset"}


# ── Melhoria 9: health check ──────────────────────────────────────────────

@app.get("/api/health")
async def get_health():
    if _engine:
        return _engine.get_health()
    return {
        "status": "no_engine",
        "last_tick_at": None,
        "last_signal_at": None,
        "binance_ws": {"connected": False},
    }


# ── Config ────────────────────────────────────────────────────────────────

@app.get("/api/config")
async def get_config():
    return {
        "max_position_size": config.MAX_POSITION_SIZE,
        "max_simultaneous_positions": config.MAX_SIMULTANEOUS_POSITIONS,
        "stop_loss_pct": config.STOP_LOSS_PCT,
        "min_edge": config.MIN_EDGE,
        "minimum_balance_usdc": config.MINIMUM_BALANCE_USDC,
        "circuit_breaker_errors": config.CIRCUIT_BREAKER_API_ERRORS,
    }


@app.post("/api/config")
async def update_config(payload: dict):
    if "max_position_size" in payload:
        try:
            val = float(payload["max_position_size"])
            if 0 < val <= 1000:
                config.MAX_POSITION_SIZE = val
        except (TypeError, ValueError):
            pass
    if "minimum_balance_usdc" in payload:
        try:
            val = float(payload["minimum_balance_usdc"])
            if val >= 0:
                config.MINIMUM_BALANCE_USDC = val
        except (TypeError, ValueError):
            pass
    return {"status": "updated", "max_position_size": config.MAX_POSITION_SIZE}


# ── Backtest ──────────────────────────────────────────────────────────────

@app.post("/api/backtest")
async def run_backtest(limit: int = 100):
    try:
        from simulation.backtester import Backtester
        async with Backtester() as bt:
            report = await bt.run(limit=limit)
        return report
    except Exception as exc:
        return {"error": f"Falha no backtest: {exc}"}


# ── SSE ───────────────────────────────────────────────────────────────────

@app.get("/events")
async def sse_stream():
    async def event_generator() -> AsyncGenerator[str, None]:
        if _engine:
            yield f"data: {json.dumps({'type': 'metrics_update', 'data': _engine.get_metrics()})}\n\n"
            yield f"data: {json.dumps({'type': 'sentiment_update', 'data': _engine.get_news_sentiment()})}\n\n"
            yield f"data: {json.dumps({'type': 'health_update', 'data': _engine.get_health()})}\n\n"

        while True:
            try:
                if _engine:
                    event = await asyncio.wait_for(_engine.get_sse_event(), timeout=25.0)
                    yield f"data: {json.dumps(event)}\n\n"
                else:
                    yield f"data: {json.dumps({'type': 'heartbeat', 'ts': time.time()})}\n\n"
                    await asyncio.sleep(10)
            except asyncio.TimeoutError:
                yield f"data: {json.dumps({'type': 'heartbeat', 'ts': time.time()})}\n\n"
            except Exception:
                break

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Estáticos ─────────────────────────────────────────────────────────────

@app.get("/")
async def serve_index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
