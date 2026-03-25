# Nifty50 Options AutoScalper

A real-time options scalping bot for Nifty50, integrated with the **DHAN API** for live market data and order execution. The bot automatically trades Call (CE) or Put (PE) options based on multi-indicator confluence signals.

## Features

- **Multi-Indicator Strategy**: EMA crossover, RSI, VWAP, and SuperTrend confluence
- **DHAN API Integration**: Live option chain, market data feed, and order placement
- **Risk Management**: Per-trade stop-loss, profit targets, trailing stops, daily P&L limits
- **Daily Lot Cap**: Maximum 2 lots per day (configurable)
- **Paper Trading Mode**: Test strategies risk-free before going live
- **Real-Time Dashboard**: Terminal-based live monitoring with P&L tracking
- **Trade Logging**: CSV trade log for analysis
- **Fully Configurable**: All parameters tunable via `config.yaml`

## Architecture

```
scalper/
├── config.py          # Configuration loader & validator
├── dhan_client.py     # DHAN API client (market data, orders, option chain)
├── indicators.py      # Technical indicators (EMA, RSI, VWAP, SuperTrend)
├── strategy.py        # Scalping strategy engine (signal generation)
├── risk_manager.py    # Risk management (SL, targets, daily limits)
├── paper_trader.py    # Paper trading simulator
├── dashboard.py       # Real-time terminal dashboard
└── orchestrator.py    # Main trading loop coordinator
```

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure

Edit `config.yaml` with your settings:

```yaml
# DHAN API Credentials
dhan:
  client_id: "YOUR_CLIENT_ID"
  access_token: "YOUR_ACCESS_TOKEN"

# Risk Management
risk:
  stop_loss_percent: 20.0
  profit_target_percent: 30.0
  max_lots_per_day: 2
  max_daily_loss: 5000.0

# Start in paper mode
paper_trading:
  enabled: true
```

Or use environment variables:

```bash
export DHAN_CLIENT_ID="your_client_id"
export DHAN_ACCESS_TOKEN="your_access_token"
```

### 3. Run

```bash
# Paper trading mode (default)
python main.py

# With custom config
python main.py --config my_config.yaml

# Force live mode (requires DHAN credentials)
python main.py --live
```

## Configuration Reference

### Strategy Parameters

| Parameter | Default | Description |
|---|---|---|
| `ema_fast_period` | 9 | Fast EMA period |
| `ema_slow_period` | 21 | Slow EMA period |
| `rsi_period` | 14 | RSI calculation period |
| `rsi_overbought` | 70 | RSI overbought threshold |
| `rsi_oversold` | 30 | RSI oversold threshold |
| `supertrend_period` | 10 | SuperTrend ATR period |
| `supertrend_multiplier` | 3.0 | SuperTrend multiplier |
| `min_signals_for_entry` | 2 | Min confirming signals (out of 4) |
| `strike_mode` | ATM | Strike selection: ATM, ITM1, ITM2, OTM1, OTM2 |

### Risk Parameters

| Parameter | Default | Description |
|---|---|---|
| `stop_loss_percent` | 20.0 | Stop-loss as % of premium |
| `profit_target_percent` | 30.0 | Profit target as % of premium |
| `trailing_stop_enabled` | true | Enable trailing stop-loss |
| `trailing_stop_percent` | 15.0 | Trailing stop % from peak |
| `max_lots_per_day` | 2 | Maximum lots per day |
| `max_daily_loss` | 5000 | Daily loss limit (INR) |
| `max_daily_profit` | 10000 | Daily profit target (INR) |
| `max_open_positions` | 1 | Max simultaneous positions |

### Order Parameters

| Parameter | Default | Description |
|---|---|---|
| `product_type` | INTRADAY | Order product type |
| `order_type` | MARKET | MARKET or LIMIT |
| `auto_square_off_time` | 15:15 | Auto square-off time (IST) |
| `no_new_trade_after` | 15:00 | No new trades after (IST) |
| `market_open_time` | 09:20 | Start trading after (IST) |

## How It Works

### Entry Logic

The bot generates a **BUY CALL (CE)** signal when bullish indicators align, or a **BUY PUT (PE)** signal when bearish indicators align:

1. **EMA Crossover**: Fast EMA (9) crosses above slow EMA (21) = Bullish
2. **RSI**: Below 30 (oversold) = Bullish, Above 70 (overbought) = Bearish
3. **VWAP**: Price above VWAP = Bullish, below = Bearish
4. **SuperTrend**: Uptrend direction = Bullish, downtrend = Bearish

A trade is entered when `min_signals_for_entry` (default: 2) indicators agree.

### Exit Logic

Positions are exited when any of these conditions are met:

- **Stop-Loss**: Option premium drops by `stop_loss_percent`
- **Profit Target**: Option premium rises by `profit_target_percent`
- **Trailing Stop**: Premium drops from peak by `trailing_stop_percent`
- **Strategy Reversal**: EMA + SuperTrend both reverse direction
- **Daily Loss Limit**: Total daily loss exceeds `max_daily_loss` (kill switch)
- **Auto Square-Off**: At `auto_square_off_time` (default 15:15 IST)

### Safety Features

- Paper trading mode enabled by default
- Maximum 2 lots per day cap
- Daily P&L kill switch
- Auto square-off before market close
- Graceful shutdown on Ctrl+C (squares off all positions)

## DHAN API Setup

1. Log in to your [Dhan account](https://dhan.co)
2. Go to Profile → DhanHQ Trading APIs
3. Generate your Access Token
4. Copy your Client ID and Access Token to `config.yaml`

> **Important**: For live API orders, DHAN requires static IP whitelisting. Refer to [DHAN API docs](https://dhanhq.co/docs/v2/).

## Disclaimer

This software is for educational and informational purposes only. Trading in financial markets involves substantial risk of loss. Past performance is not indicative of future results. Use at your own risk. Always start with paper trading mode before using real money.
