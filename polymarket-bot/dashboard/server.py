import asyncio
import json
import time
from typing import AsyncGenerator

from fastapi import FastAPI, Response
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


# --------------------------------------------------------------------------- #
# REST endpoints                                                               #
# --------------------------------------------------------------------------- #

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


@app.get("/api/config")
async def get_config():
    """Return live-editable runtime configuration."""
    return {
        "max_position_size": config.MAX_POSITION_SIZE,
        "max_simultaneous_positions": config.MAX_SIMULTANEOUS_POSITIONS,
        "stop_loss_pct": config.STOP_LOSS_PCT,
        "min_edge": config.MIN_EDGE,
    }


@app.post("/api/config")
async def update_config(payload: dict):
    """Update runtime config (applied live, no restart needed)."""
    if "max_position_size" in payload:
        try:
            val = float(payload["max_position_size"])
            if 0 < val <= 1000:
                config.MAX_POSITION_SIZE = val
        except (TypeError, ValueError):
            pass
    return {
        "max_position_size": config.MAX_POSITION_SIZE,
        "status": "updated",
    }


@app.post("/api/backtest")
async def run_backtest(limit: int = 100):
    """Run backtest on historical markets and return report."""
    from simulation.backtester import Backtester
    async with Backtester() as bt:
        report = await bt.run(limit=limit)
    return report


# --------------------------------------------------------------------------- #
# SSE stream                                                                   #
# --------------------------------------------------------------------------- #

@app.get("/events")
async def sse_stream():
    async def event_generator() -> AsyncGenerator[str, None]:
        if _engine:
            metrics = _engine.get_metrics()
            yield f"data: {json.dumps({'type': 'metrics_update', 'data': metrics})}\n\n"
            sentiment = _engine.get_news_sentiment()
            yield f"data: {json.dumps({'type': 'sentiment_update', 'data': sentiment})}\n\n"

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


# --------------------------------------------------------------------------- #
# Static files                                                                 #
# --------------------------------------------------------------------------- #

@app.get("/")
async def serve_index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
