"""
Melhoria 7 — Logs estruturados com rotação diária.

Cria três loggers:
  - trades  → logs/trades.log
  - errors  → logs/errors.log
  - signals → logs/signals.log

Formato JSON para análise posterior.
Rotação: 10 MB por arquivo, mantém 7 cópias.
"""
import json
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from datetime import datetime

import config

_LOG_DIR = Path(config.LOG_DIR)
_LOG_DIR.mkdir(exist_ok=True)

_LOGGERS: dict[str, logging.Logger] = {}


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.utcnow().isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        if hasattr(record, "extra"):
            payload.update(record.extra)
        return json.dumps(payload, ensure_ascii=False)


def _build(name: str, filename: str) -> logging.Logger:
    logger = logging.getLogger(f"bot.{name}")
    if logger.handlers:
        return logger
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    path = _LOG_DIR / filename
    fh = RotatingFileHandler(
        path,
        maxBytes=config.LOG_MAX_BYTES,
        backupCount=config.LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    fh.setFormatter(_JsonFormatter())
    logger.addHandler(fh)

    # Também exibe erros no console (sem rich, para não duplicar)
    if name == "errors":
        sh = logging.StreamHandler(sys.stderr)
        sh.setLevel(logging.WARNING)
        sh.setFormatter(logging.Formatter("[%(asctime)s][%(levelname)s] %(message)s"))
        logger.addHandler(sh)

    return logger


def get_trades_logger() -> logging.Logger:
    if "trades" not in _LOGGERS:
        _LOGGERS["trades"] = _build("trades", "trades.log")
    return _LOGGERS["trades"]


def get_errors_logger() -> logging.Logger:
    if "errors" not in _LOGGERS:
        _LOGGERS["errors"] = _build("errors", "errors.log")
    return _LOGGERS["errors"]


def get_signals_logger() -> logging.Logger:
    if "signals" not in _LOGGERS:
        _LOGGERS["signals"] = _build("signals", "signals.log")
    return _LOGGERS["signals"]


# ── Funções de conveniência ────────────────────────────────────────────────

def log_trade(trade: dict):
    get_trades_logger().info("trade", extra={"extra": trade})


def log_error(msg: str, exc: Exception | None = None, context: dict | None = None):
    extra = context or {}
    if exc:
        extra["exception"] = str(exc)
    get_errors_logger().error(msg, extra={"extra": extra})


def log_signal(signal: dict):
    get_signals_logger().debug("signal", extra={"extra": signal})
