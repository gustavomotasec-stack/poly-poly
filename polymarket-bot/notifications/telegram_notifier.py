"""
Melhoria 8 — Alertas por Telegram.

Usa aiohttp diretamente (sem biblioteca extra).
Notifica:
  - Bot parado por erro
  - Daily loss limit atingido
  - Trade executado acima de TELEGRAM_ALERT_MIN_TRADE_SIZE
  - Kill switch acionado
  - Circuit breaker ativado

Config (.env):
  TELEGRAM_BOT_TOKEN=123456:ABCDEF...
  TELEGRAM_CHAT_ID=@seuchat  ou  -100XXXXXX
  TELEGRAM_ALERT_MIN_TRADE_SIZE=5.0
"""
import asyncio
import time
from typing import Optional

import aiohttp
from storage.logger import get_errors_logger

import config

_log = get_errors_logger()

# Taxa mínima entre mensagens para o mesmo evento (evita flood)
_COOLDOWN_S = 60.0
_last_sent: dict[str, float] = {}


class TelegramNotifier:
    def __init__(self):
        self._token = config.TELEGRAM_BOT_TOKEN
        self._chat_id = config.TELEGRAM_CHAT_ID
        self._session: Optional[aiohttp.ClientSession] = None
        self._enabled = bool(self._token and self._chat_id)
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=50)

    async def start(self):
        if not self._enabled:
            return
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10),
            headers={"User-Agent": "polymarket-bot/1.0"},
        )
        asyncio.create_task(self._sender_loop())

    async def stop(self):
        if self._session:
            await self._session.close()

    # ── Métodos públicos ──────────────────────────────────────────────────

    def notify_trade(self, trade: dict):
        size = trade.get("size", 0)
        if size < config.TELEGRAM_ALERT_MIN_TRADE_SIZE:
            return
        direction = trade.get("direction", "?")
        strategy = trade.get("strategy", "?")
        question = trade.get("question", "")[:60]
        mode = "🔴 LIVE" if not config.SIMULATION_MODE else "🟡 SIM"
        msg = (
            f"📊 *Trade Executado* [{mode}]\n"
            f"Mercado: {question}\n"
            f"Direção: `{direction}` | Tamanho: `${size:.2f}`\n"
            f"Estratégia: `{strategy}`"
        )
        self._enqueue("trade", msg)

    def notify_daily_limit(self, bankroll: float, loss_pct: float):
        msg = (
            f"🚨 *Limite de Perda Diária Atingido!*\n"
            f"Perda: `{loss_pct*100:.1f}%`\n"
            f"Banca atual: `${bankroll:.2f}`\n"
            f"⛔ Bot pausado até amanhã."
        )
        self._enqueue("daily_limit", msg, force=True)

    def notify_kill_switch(self, bankroll: float, reason: str):
        msg = (
            f"🔴 *Kill Switch Acionado!*\n"
            f"Motivo: `{reason}`\n"
            f"Banca: `${bankroll:.2f}`\n"
            f"⛔ Todas as posições fechadas."
        )
        self._enqueue("kill_switch", msg, force=True)

    def notify_circuit_breaker(self, errors: int):
        msg = (
            f"⚡ *Circuit Breaker Ativado*\n"
            f"`{errors}` erros de API consecutivos.\n"
            f"Trading pausado por {config.CIRCUIT_BREAKER_COOLDOWN_S // 60} minutos."
        )
        self._enqueue("circuit_breaker", msg)

    def notify_bot_error(self, error: str):
        msg = (
            f"💥 *Erro Crítico no Bot*\n"
            f"```\n{error[:300]}\n```"
        )
        self._enqueue("bot_error", msg)

    def notify_mode_change(self, new_mode: str):
        emoji = "🔴" if new_mode == "live" else "🟡"
        msg = (
            f"{emoji} *Modo alterado: {new_mode.upper()}*\n"
            f"Modo de trading alterado para `{new_mode}`."
        )
        self._enqueue("mode_change", msg, force=True)

    # ── Internals ─────────────────────────────────────────────────────────

    def _enqueue(self, key: str, text: str, force: bool = False):
        if not self._enabled:
            return
        now = time.time()
        if not force and now - _last_sent.get(key, 0) < _COOLDOWN_S:
            return
        _last_sent[key] = now
        try:
            self._queue.put_nowait(text)
        except asyncio.QueueFull:
            pass

    async def _sender_loop(self):
        while True:
            try:
                text = await self._queue.get()
                await self._send(text)
            except Exception as exc:
                _log.warning(f"[Telegram] sender loop error: {exc}")
            await asyncio.sleep(0.5)

    async def _send(self, text: str) -> bool:
        if not self._enabled or not self._session:
            return False
        url = f"https://api.telegram.org/bot{self._token}/sendMessage"
        payload = {
            "chat_id": self._chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }
        try:
            async with self._session.post(url, json=payload) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    _log.warning(f"[Telegram] HTTP {resp.status}: {body[:200]}")
                    return False
                return True
        except Exception as exc:
            _log.warning(f"[Telegram] send failed: {exc}")
            return False
