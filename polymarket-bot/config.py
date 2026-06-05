import os
import sys
from dotenv import load_dotenv

load_dotenv()

# ── Encoding seguro no console Windows ─────────────────────────────────────
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ── Polymarket credentials ─────────────────────────────────────────────────
# POLYMARKET_PK lida EXCLUSIVAMENTE de variável de ambiente do sistema.
# Nunca do arquivo .env. Nunca logada ou printada.
POLYMARKET_PK: str = os.environ.get("POLYMARKET_PK", "")
POLYMARKET_API_KEY: str = os.getenv("POLYMARKET_API_KEY", "")
POLYMARKET_API_SECRET: str = os.getenv("POLYMARKET_API_SECRET", "")
POLYMARKET_PASSPHRASE: str = os.getenv("POLYMARKET_PASSPHRASE", "")

# ── Mode ───────────────────────────────────────────────────────────────────
SIMULATION_MODE: bool = True  # sempre True a menos que --live seja passado

# ── Trading parameters ─────────────────────────────────────────────────────
MAX_POSITION_SIZE: float = 1.0       # USDC por mercado
STOP_LOSS_PCT: float = 0.30
MIN_EDGE: float = 0.03               # edge mínimo implícito para entrar
MAX_SIMULTANEOUS_POSITIONS: int = 5
COOLDOWN_AFTER_LOSSES: int = 3       # perdas consecutivas antes do cooldown
COOLDOWN_DURATION_SECONDS: int = 15 * 60
DAILY_LOSS_LIMIT_PCT: float = 0.20   # 20% da banca

# ── Kill switch ────────────────────────────────────────────────────────────
MINIMUM_BALANCE_USDC: float = float(os.getenv("MINIMUM_BALANCE_USDC", "10.0"))
# Ao cair abaixo disso, o bot para tudo e fecha posições abertas

# ── Circuit breaker ────────────────────────────────────────────────────────
CIRCUIT_BREAKER_API_ERRORS: int = 5       # erros de API consecutivos antes de pausar
CIRCUIT_BREAKER_COOLDOWN_S: int = 5 * 60  # 5 min de cooldown após acionar

# ── Strategy parameters ───────────────────────────────────────────────────
RSI_PERIOD: int = 14
RSI_OVERBOUGHT: int = 70
RSI_OVERSOLD: int = 30
MOMENTUM_WINDOW: int = 3
VOLUME_SPIKE_MULTIPLIER: float = 2.0
MIN_CONFIDENCE: float = 0.60
HEDGE_RATIO: float = 0.20
MEAN_REVERSION_DIVERGENCE: float = 0.10
MEAN_REVERSION_MIN_TIME_LEFT: int = 2 * 60

# ── Simulation ─────────────────────────────────────────────────────────────
INITIAL_PAPER_BANKROLL: float = 100.0  # USDC

# ── Binance ────────────────────────────────────────────────────────────────
BINANCE_WS_URL: str = "wss://stream.binance.com:9443/ws"
BINANCE_STREAMS: list[str] = [
    "btcusdt@kline_1m", "ethusdt@kline_1m",
    "btcusdt@ticker",   "ethusdt@ticker",
]
CANDLE_BUFFER_SIZE: int = 50
# Backoff exponencial da reconexão Binance
WS_RECONNECT_INITIAL_DELAY: float = 1.0   # segundos
WS_RECONNECT_MAX_DELAY: float = 60.0
WS_RECONNECT_JITTER: float = 2.0          # jitter aleatório max (segundos)

# ── Polymarket APIs ───────────────────────────────────────────────────────
GAMMA_API_BASE: str = "https://gamma-api.polymarket.com"
CLOB_API_BASE: str = "https://clob.polymarket.com"
CLOB_WS_URL: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

# ── Dashboard ─────────────────────────────────────────────────────────────
DASHBOARD_HOST: str = "0.0.0.0"
DASHBOARD_PORT: int = 8080
# Senha de confirmação para ativar modo live pelo dashboard
LIVE_MODE_PASSWORD: str = os.getenv("LIVE_MODE_PASSWORD", "CONFIRMAR")

# ── Intervals ─────────────────────────────────────────────────────────────
ENGINE_LOOP_INTERVAL: int = 10    # segundos
METRICS_LOG_INTERVAL: int = 30

# ── Logs estruturados ─────────────────────────────────────────────────────
LOG_DIR: str = "logs"
LOG_MAX_BYTES: int = 10 * 1024 * 1024  # 10 MB por arquivo
LOG_BACKUP_COUNT: int = 7               # manter 7 arquivos (7 dias)

# ── Telegram ─────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")
# Valor mínimo de trade para gerar alerta Telegram
TELEGRAM_ALERT_MIN_TRADE_SIZE: float = float(os.getenv("TELEGRAM_ALERT_MIN_TRADE_SIZE", "5.0"))
