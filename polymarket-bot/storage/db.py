import sqlite3
import json
from datetime import datetime, date, timezone
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).parent.parent / "data" / "bot.db"

# Valor padrão de bankroll para novos bancos
INITIAL_BANKROLL_DEFAULT = 100.0


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS trades (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp   TEXT NOT NULL,
                market_id   TEXT NOT NULL,
                question    TEXT NOT NULL,
                direction   TEXT NOT NULL,
                size        REAL NOT NULL,
                entry_price REAL NOT NULL,
                exit_price  REAL,
                pnl         REAL,
                strategy    TEXT NOT NULL,
                simulated   INTEGER NOT NULL DEFAULT 1,
                status      TEXT NOT NULL DEFAULT 'open'
            );

            CREATE TABLE IF NOT EXISTS metrics_snapshot (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp        TEXT NOT NULL,
                bankroll         REAL NOT NULL,
                total_pnl        REAL NOT NULL,
                win_rate         REAL NOT NULL,
                active_positions INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS signals_log (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp  TEXT NOT NULL,
                asset      TEXT NOT NULL,
                direction  TEXT NOT NULL,
                confidence REAL NOT NULL,
                rsi        REAL,
                momentum   REAL,
                indicators TEXT
            );

            -- Bug 1: fonte de verdade persistente para configurações do dashboard.
            -- Sobrevive a F5 e a reinícios do servidor.
            CREATE TABLE IF NOT EXISTS bot_config (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)


# ── Bug 1: config persistente ─────────────────────────────────────────────

def get_config(key: str, default=None):
    """Lê uma configuração do banco. Retorna default se não existir."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT value FROM bot_config WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row[0])
        except (ValueError, TypeError):
            return row[0]


def set_config(key: str, value) -> None:
    """Salva ou atualiza uma configuração no banco."""
    with _connect() as conn:
        conn.execute(
            "INSERT INTO bot_config (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )


def get_all_config() -> dict:
    """Retorna todas as configurações salvas no banco."""
    with _connect() as conn:
        rows = conn.execute("SELECT key, value FROM bot_config").fetchall()
    result = {}
    for r in rows:
        try:
            result[r["key"]] = json.loads(r["value"])
        except (ValueError, TypeError):
            result[r["key"]] = r["value"]
    return result


# ── Trades ────────────────────────────────────────────────────────────────

def save_trade(trade: dict) -> int:
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO trades
               (timestamp, market_id, question, direction, size, entry_price,
                exit_price, pnl, strategy, simulated, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                trade.get("timestamp", datetime.now(timezone.utc).isoformat()),
                trade["market_id"],
                trade["question"],
                trade["direction"],
                trade["size"],
                trade["entry_price"],
                trade.get("exit_price"),
                trade.get("pnl"),
                trade["strategy"],
                int(trade.get("simulated", True)),
                trade.get("status", "open"),
            ),
        )
        return cur.lastrowid


def update_trade(trade_id: int, exit_price: float, pnl: float, status: str = "closed"):
    with _connect() as conn:
        conn.execute(
            "UPDATE trades SET exit_price=?, pnl=?, status=? WHERE id=?",
            (exit_price, pnl, status, trade_id),
        )


def get_trades(limit: int = 100) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM trades ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_open_trades() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM trades WHERE status='open' ORDER BY timestamp DESC"
        ).fetchall()
        return [dict(r) for r in rows]


# ── Bug 2: métricas com fonte única ──────────────────────────────────────
# bankroll é sempre recalculado como initial_bankroll + total_pnl
# para garantir consistência absoluta com os trades do banco.

def compute_metrics() -> dict:
    """
    Calcula todas as métricas a partir do SQLite.
    Fonte única de verdade — nunca usa variável de memória para bankroll.
    """
    initial = get_config("initial_bankroll", INITIAL_BANKROLL_DEFAULT)

    with _connect() as conn:
        total_closed = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE status='closed'"
        ).fetchone()[0]

        wins = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE status='closed' AND pnl > 0"
        ).fetchone()[0]

        total_pnl_row = conn.execute(
            "SELECT COALESCE(SUM(pnl), 0.0) FROM trades WHERE status='closed'"
        ).fetchone()[0]
        total_pnl = float(total_pnl_row)

        active = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE status='open'"
        ).fetchone()[0]

        today_str = date.today().isoformat()
        today_trades = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE timestamp LIKE ?",
            (f"{today_str}%",),
        ).fetchone()[0]

    win_rate = round(wins / total_closed * 100, 1) if total_closed > 0 else 0.0

    # Bug 2: bankroll é SEMPRE initial + pnl acumulado — nunca desincroniza
    bankroll = round(initial + total_pnl, 4)

    return {
        "bankroll":      round(bankroll, 2),
        "initial_bankroll": initial,
        "total_pnl":     round(total_pnl, 4),
        "total_pnl_pct": round(total_pnl / initial * 100, 2) if initial else 0.0,
        "win_rate":      win_rate,
        "total_trades":  total_closed,
        "open_trades":   active,
        "today_trades":  today_trades,
    }


# Mantém compatibilidade com chamadas legadas (engine, paper_trader)
def get_metrics(bankroll: float) -> dict:
    m = compute_metrics()
    return m


# ── Metrics snapshots ─────────────────────────────────────────────────────

def save_metrics(snapshot: dict):
    with _connect() as conn:
        conn.execute(
            """INSERT INTO metrics_snapshot
               (timestamp, bankroll, total_pnl, win_rate, active_positions)
               VALUES (?, ?, ?, ?, ?)""",
            (
                snapshot.get("timestamp", datetime.now(timezone.utc).isoformat()),
                snapshot["bankroll"],
                snapshot["total_pnl"],
                snapshot["win_rate"],
                snapshot["active_positions"],
            ),
        )


def get_metrics_history(limit: int = 200) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM metrics_snapshot ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in reversed(rows)]


def get_daily_pnl() -> float:
    today = date.today().isoformat()
    with _connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(pnl), 0) FROM trades WHERE timestamp LIKE ? AND status='closed'",
            (f"{today}%",),
        ).fetchone()
        return float(row[0])


# ── Signals ───────────────────────────────────────────────────────────────

def save_signal(signal: dict):
    with _connect() as conn:
        conn.execute(
            """INSERT INTO signals_log
               (timestamp, asset, direction, confidence, rsi, momentum, indicators)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                signal.get("timestamp", datetime.now(timezone.utc).isoformat()),
                signal["asset"],
                signal["direction"],
                signal["confidence"],
                signal.get("rsi"),
                signal.get("momentum"),
                json.dumps(signal.get("indicators", {})),
            ),
        )


def get_recent_signals(limit: int = 20) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM signals_log ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            d["indicators"] = json.loads(d["indicators"] or "{}")
            results.append(d)
        return results
