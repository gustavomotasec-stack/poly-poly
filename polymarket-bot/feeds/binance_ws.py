import asyncio
import json
import time
from collections import defaultdict, deque
from typing import Callable, Optional

import websockets
from rich.console import Console

import config

console = Console()


class BinanceWebSocket:
    """Real-time price feed from Binance via WebSocket."""

    def __init__(self):
        self._candles: dict[str, deque] = defaultdict(
            lambda: deque(maxlen=config.CANDLE_BUFFER_SIZE)
        )
        self._tickers: dict[str, dict] = {}
        self._running = False
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._callbacks: list[Callable] = []
        self._reconnect_delay = 1.0

    def add_callback(self, cb: Callable):
        self._callbacks.append(cb)

    def get_candles(self, symbol: str) -> list[dict]:
        """Return OHLCV candle list for symbol (e.g. 'BTCUSDT')."""
        return list(self._candles[symbol.upper()])

    def get_ticker(self, symbol: str) -> Optional[dict]:
        return self._tickers.get(symbol.upper())

    def get_closes(self, symbol: str) -> list[float]:
        return [c["close"] for c in self._candles[symbol.upper()]]

    async def start(self):
        self._running = True
        asyncio.create_task(self._run_forever())

    async def stop(self):
        self._running = False
        if self._ws:
            await self._ws.close()

    async def _run_forever(self):
        while self._running:
            try:
                await self._connect()
            except Exception as exc:
                console.print(f"[red][Binance WS] Disconnected: {exc}. Reconnecting in {self._reconnect_delay:.1f}s[/red]")
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(self._reconnect_delay * 2, 60.0)

    async def _connect(self):
        streams = "/".join(config.BINANCE_STREAMS)
        url = f"{config.BINANCE_WS_URL}/stream?streams={streams}"
        console.print(f"[cyan][Binance WS] Connecting to {url}[/cyan]")

        async with websockets.connect(url, ping_interval=20, ping_timeout=10) as ws:
            self._ws = ws
            self._reconnect_delay = 1.0
            console.print("[green][Binance WS] Connected[/green]")

            async for raw in ws:
                if not self._running:
                    break
                try:
                    msg = json.loads(raw)
                    self._handle_message(msg)
                except Exception as exc:
                    console.print(f"[yellow][Binance WS] Parse error: {exc}[/yellow]")

    def _handle_message(self, msg: dict):
        data = msg.get("data", msg)
        event = data.get("e")

        if event == "kline":
            self._handle_kline(data)
        elif event == "24hrTicker":
            self._handle_ticker(data)

    def _handle_kline(self, data: dict):
        k = data["k"]
        symbol = data["s"].upper()
        candle = {
            "time": k["t"],
            "open": float(k["o"]),
            "high": float(k["h"]),
            "low": float(k["l"]),
            "close": float(k["c"]),
            "volume": float(k["v"]),
            "closed": k["x"],
        }
        # Replace last candle if still open, append when closed
        buf = self._candles[symbol]
        if buf and not buf[-1].get("closed"):
            buf[-1] = candle
        else:
            buf.append(candle)

        for cb in self._callbacks:
            asyncio.create_task(self._safe_call(cb, "kline", symbol, candle))

    def _handle_ticker(self, data: dict):
        symbol = data["s"].upper()
        self._tickers[symbol] = {
            "symbol": symbol,
            "price": float(data["c"]),
            "change_pct": float(data["P"]),
            "volume": float(data["v"]),
            "high": float(data["h"]),
            "low": float(data["l"]),
            "timestamp": time.time(),
        }

    async def _safe_call(self, cb: Callable, *args):
        try:
            result = cb(*args)
            if asyncio.iscoroutine(result):
                await result
        except Exception as exc:
            console.print(f"[yellow][Binance WS] Callback error: {exc}[/yellow]")
