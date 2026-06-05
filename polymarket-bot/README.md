# Polymarket Trading Bot

Automated prediction market trading bot for Polymarket crypto markets (5-min / 15-min), with a real-time web dashboard.

> ⚠️ **Financial risk warning**: Trading prediction markets involves significant risk of loss. This software is provided for educational and research purposes. Always start in simulation mode. Never trade with money you cannot afford to lose.

---

## Features

- **3 trading strategies**: Arbitrage, Momentum (RSI + volume), Mean Reversion
- **Real-time signals** from Binance WebSocket (BTC, ETH)
- **Paper trading** simulation with full metrics (win rate, Sharpe, drawdown)
- **Risk management**: stop loss, position limits, cooldown, daily loss cap
- **Live dashboard** at `http://localhost:8080` with SSE real-time updates
- **SQLite persistence** — survives restarts, dashboard works even when bot is paused

---

## Prerequisites

- Python 3.11+
- Internet access (Binance + Polymarket APIs)
- Polymarket account + API keys (only needed for live mode)

---

## Installation

```bash
cd polymarket-bot
pip install -r requirements.txt
```

---

## Configuration

Copy `.env.example` to `.env`:

```bash
cp .env.example .env
```

For **simulation mode** (default), no credentials are needed.

For **live mode**, fill in your Polymarket credentials:
- `POLYMARKET_PK` — your wallet private key
- `POLYMARKET_API_KEY`, `POLYMARKET_API_SECRET`, `POLYMARKET_PASSPHRASE` — L2 API keys

Get API keys at: https://docs.polymarket.com/#api-keys

---

## Running

### Simulation mode (safe, default)

```bash
python main.py
```

Opens dashboard at **http://localhost:8080**

### Live trading ⚠️

```bash
python main.py --live
```

This uses **real money**. Requires valid credentials in `.env`. Double-check all risk parameters in `config.py` before running.

### Without dashboard

```bash
python main.py --no-dashboard
```

### Custom port

```bash
python main.py --port 9090
```

---

## Dashboard

Open **http://localhost:8080** in your browser.

Features:
- **Equity curve** — bankroll over time
- **Live metrics** — PnL, win rate, drawdown, Sharpe ratio
- **Trade history** — every trade with entry/exit/PnL
- **Live signals** — RSI, momentum, confidence bars
- **Controls** — pause/resume, simulation toggle

Updates automatically via Server-Sent Events (SSE) — no polling needed.

---

## Strategies

### 1. Arbitrage
Triggers when `price_YES + price_NO < 0.97`. Buys both sides simultaneously, locking in a guaranteed profit of `1 - (price_YES + price_NO)`. Highest priority.

### 2. Momentum
Uses RSI(14) + 3-minute price momentum + volume spikes. Requires confidence ≥ 60% and implied edge > 3%. Allocates 80% to the main direction, 20% as a hedge.

### 3. Mean Reversion
Activates only in the last 2 minutes of a market. When the market price diverges >10% from the RSI-implied probability, bets against the overextended side.

---

## Risk Parameters (config.py)

| Parameter | Default | Description |
|-----------|---------|-------------|
| `MAX_POSITION_SIZE` | 1.0 USDC | Max bet per market |
| `STOP_LOSS_PCT` | 30% | Close position at 30% loss |
| `MAX_SIMULTANEOUS_POSITIONS` | 5 | Open positions at once |
| `COOLDOWN_AFTER_LOSSES` | 3 | Pause after 3 consecutive losses |
| `COOLDOWN_DURATION_SECONDS` | 900 | 15 minute cooldown |
| `DAILY_LOSS_LIMIT_PCT` | 20% | Stop all trading after 20% daily loss |
| `MIN_EDGE` | 3% | Minimum implied edge to enter |
| `MIN_CONFIDENCE` | 60% | Minimum signal confidence |

---

## Project Structure

```
polymarket-bot/
├── main.py                  # Entry point + CLI
├── config.py                # All parameters
├── bot/
│   ├── engine.py            # Async main loop
│   ├── strategies.py        # 3 trading strategies
│   ├── risk_manager.py      # Risk rules
│   └── signal_generator.py  # RSI + momentum signals
├── feeds/
│   ├── binance_ws.py        # Binance WebSocket feed
│   └── polymarket_api.py    # Polymarket Gamma + CLOB APIs
├── simulation/
│   └── paper_trader.py      # Paper trading simulator
├── dashboard/
│   ├── server.py            # FastAPI server
│   └── static/              # HTML + JS dashboard
├── storage/
│   └── db.py                # SQLite persistence
└── data/
    └── bot.db               # Auto-created database
```
