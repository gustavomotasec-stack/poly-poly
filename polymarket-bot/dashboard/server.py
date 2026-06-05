import asyncio
import json
import time
from typing import AsyncGenerator

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse, FileResponse
from pathlib import Path

import storage.db as db

app = FastAPI(title="Polymarket Bot Dashboard", docs_url=None, redoc_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = Path(__file__).parent / "static"

# Engine reference is injected at startup
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
    bankroll = 100.0
    return db.get_metrics(bankroll)


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


# --------------------------------------------------------------------------- #
# SSE stream                                                                   #
# --------------------------------------------------------------------------- #

@app.get("/events")
async def sse_stream():
    async def event_generator() -> AsyncGenerator[str, None]:
        # Send initial data on connect
        if _engine:
            metrics = _engine.get_metrics()
            yield f"data: {json.dumps({'type': 'metrics_update', 'data': metrics})}\n\n"

        while True:
            try:
                if _engine:
                    event = await asyncio.wait_for(_engine.get_sse_event(), timeout=25.0)
                    yield f"data: {json.dumps(event)}\n\n"
                else:
                    # Heartbeat when no engine
                    yield f"data: {json.dumps({'type': 'heartbeat', 'ts': time.time()})}\n\n"
                    await asyncio.sleep(10)
            except asyncio.TimeoutError:
                yield f"data: {json.dumps({'type': 'heartbeat', 'ts': time.time()})}\n\n"
            except Exception:
                break

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# --------------------------------------------------------------------------- #
# Static files                                                                 #
# --------------------------------------------------------------------------- #

@app.get("/")
async def serve_index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
