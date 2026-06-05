"""
Melhoria 1 — Reconexão automática com backoff exponencial + jitter.
Expõe status de conexão para o health check (melhoria 9).
"""
import asyncio
import json
import random
import time
from collections import defaultdict, deque
from typing import Callable, Optional

import websockets
from rich.console import Console

import config
from storage.logger import get_errors_logger

console = Console()
_log = get_errors_logger()


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

        # ── Melhoria 1: estado de reconexão ─────────────────────────────
        self._reconnect_delay = config.WS_RECONNECT_INITIAL_DELAY
        self._reconnect_attempts = 0
        self._connected = False
        self._last_connected_at: float = 0.0
        self._last_message_at: float = 0.0

    # ── Accessors de saúde (melhoria 9) ──────────────────────────────────

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def last_message_age(self) -> float:
        """Segundos desde a última mensagem recebida."""
        if self._last_message_at == 0:
            return float("inf")
        return time.time() - self._last_message_at

    def health(self) -> dict:
        return {
            "connected": self._connected,
            "reconnect_attempts": self._reconnect_attempts,
            "last_connected_at": self._last_connected_at,
            "last_message_age_s": round(self.last_message_age, 1),
        }

    # ── Callbacks ────────────────────────────────────────────────────────

    def add_callback(self, cb: Callable):
        self._callbacks.append(cb)

    # ── Data accessors ───────────────────────────────────────────────────

    def get_candles(self, symbol: str) -> list[dict]:
        return list(self._candles[symbol.upper()])

    def get_ticker(self, symbol: str) -> Optional[dict]:
        return self._tickers.get(symbol.upper())

    def get_closes(self, symbol: str) -> list[float]:
        return [c["close"] for c in self._candles[symbol.upper()]]

    # ── Lifecycle ────────────────────────────────────────────────────────

    async def start(self):
        self._running = True
        asyncio.create_task(self._run_forever())

    async def stop(self):
        self._running = False
        self._connected = False
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass

    # ── Melhoria 1: loop de reconexão com backoff exponencial + jitter ───

    async def _run_forever(self):
        while self._running:
            try:
                await self._connect()
            except Exception as exc:
                self._connected = False
                self._reconnect_attempts += 1
                jitter = random.uniform(0, config.WS_RECONNECT_JITTER)
                delay = self._reconnect_delay + jitter

                msg = (
                    f"[Binance WS] Desconectado (tentativa #{self._reconnect_attempts}): "
                    f"{exc}. Reconectando em {delay:.1f}s"
                )
                console.print(f"[red]{msg}[/red]")
                _log.warning(msg, extra={"extra": {
                    "attempt": self._reconnect_attempts,
                    "delay_s": round(delay, 2),
                }})

                await asyncio.sleep(delay)
                # Backoff exponencial: dobra a cada tentativa, até o máximo
                self._reconnect_delay = min(
                    self._reconnect_delay * 2,
                    config.WS_RECONNECT_MAX_DELAY,
                )

    async def _connect(self):
        streams = "/".join(config.BINANCE_STREAMS)
        url = f"{config.BINANCE_WS_URL}/stream?streams={streams}"
        console.print(f"[cyan][Binance WS] Conectando a {url}[/cyan]")

        async with websockets.connect(
            url,
            ping_interval=20,
            ping_timeout=10,
            close_timeout=5,
        ) as ws:
            self._ws = ws
            self._connected = True
            self._last_connected_at = time.time()
            # Reseta backoff ao conectar com sucesso
            self._reconnect_delay = config.WS_RECONNECT_INITIAL_DELAY
            self._reconnect_attempts = 0
            console.print("[green][Binance WS] Conectado[/green]")

            async for raw in ws:
                if not self._running:
                    break
                self._last_message_at = time.time()
                try:
                    msg = json.loads(raw)
                    self._handle_message(msg)
                except Exception as exc:
                    console.print(f"[yellow][Binance WS] Erro de parse: {exc}[/yellow]")

            self._connected = False

    # ── Message handlers ─────────────────────────────────────────────────

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
            console.print(f"[yellow][Binance WS] Erro no callback: {exc}[/yellow]")
