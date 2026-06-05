import os
from dotenv import load_dotenv

load_dotenv()

# --- Polymarket credentials ---
POLYMARKET_PK = os.getenv("POLYMARKET_PK", "")
POLYMARKET_API_KEY = os.getenv("POLYMARKET_API_KEY", "")
POLYMARKET_API_SECRET = os.getenv("POLYMARKET_API_SECRET", "")
POLYMARKET_PASSPHRASE = os.getenv("POLYMARKET_PASSPHRASE", "")

# --- Mode ---
SIMULATION_MODE = True  # Always True unless --live flag is passed

# --- Trading parameters ---
MAX_POSITION_SIZE = 1.0       # USDC per market
STOP_LOSS_PCT = 0.30
MIN_EDGE = 0.03               # 3% minimum implied edge to enter
MAX_SIMULTANEOUS_POSITIONS = 5
COOLDOWN_AFTER_LOSSES = 3     # consecutive losses before cooldown
COOLDOWN_DURATION_SECONDS = 15 * 60
DAILY_LOSS_LIMIT_PCT = 0.20   # 20% of bankroll

# --- Strategy parameters ---
RSI_PERIOD = 14
RSI_OVERBOUGHT = 70
RSI_OVERSOLD = 30
MOMENTUM_WINDOW = 3           # minutes
VOLUME_SPIKE_MULTIPLIER = 2.0
MIN_CONFIDENCE = 0.60
HEDGE_RATIO = 0.20            # 20% on opposite side for momentum
MEAN_REVERSION_DIVERGENCE = 0.10  # 10% divergence threshold
MEAN_REVERSION_MIN_TIME_LEFT = 2 * 60  # last 2 minutes of market

# --- Simulation ---
INITIAL_PAPER_BANKROLL = 100.0  # USDC

# --- Binance ---
BINANCE_WS_URL = "wss://stream.binance.com:9443/ws"
BINANCE_STREAMS = ["btcusdt@kline_1m", "ethusdt@kline_1m", "btcusdt@ticker", "ethusdt@ticker"]
CANDLE_BUFFER_SIZE = 50

# --- Polymarket APIs ---
GAMMA_API_BASE = "https://gamma-api.polymarket.com"
CLOB_API_BASE = "https://clob.polymarket.com"
CLOB_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

# --- Dashboard ---
DASHBOARD_HOST = "0.0.0.0"
DASHBOARD_PORT = 8080

# --- Intervals ---
ENGINE_LOOP_INTERVAL = 10     # seconds
METRICS_LOG_INTERVAL = 30     # seconds
