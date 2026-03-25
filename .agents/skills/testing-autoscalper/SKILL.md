# Testing the Nifty50 AutoScalper

## Overview
This is a Python CLI trading bot that trades Nifty50 options via DHAN API. It has no web UI — all interaction is via terminal.

## Devin Secrets Needed
- `DHAN_CLIENT_ID` — Required only for live/connected paper testing
- `DHAN_ACCESS_TOKEN` — Required only for live/connected paper testing
- Without these secrets, paper mode runs but cannot fetch market data or generate live signals

## Setup
```bash
cd /home/ubuntu/repos/nifty50-autoscalper
pip install --pre dhanhq && pip install -r requirements.txt
```
- `dhanhq` may require `--pre` flag since v2.2.0 might still be a release candidate
- Python 3.12+ required

## Running Unit Tests
```bash
python -m pytest tests/ -v
```
- Should have 45+ tests across test_config.py, test_indicators.py, test_risk_manager.py, test_strategy.py
- All tests are self-contained with no external dependencies

## Running Lint
```bash
ruff check scalper/ tests/ main.py
```
- Use `ruff check --fix` to auto-fix common issues (unused imports, f-string issues)

## CLI Testing
```bash
python main.py --help          # Verify CLI flags
python main.py --paper          # Start in paper mode (default)
python main.py --live           # Requires DHAN credentials
python main.py --config X.yaml  # Custom config
```

## Paper Mode Without Credentials
- The app starts successfully without DHAN credentials in paper mode
- It logs warnings: "No DHAN credentials - provide client_id and access_token"
- The dashboard renders with empty state (no trades, no signals)
- The app loops and sleeps, waiting for credentials to be provided
- Use `timeout 8 python main.py --paper` to test startup without hanging

## Integration Testing with Synthetic Data
Key constructor signatures to remember:
- `RiskManager(config.risk, config.instrument.lot_size)` — second arg is `int`, NOT `config.orders`
- `Dashboard(config.monitoring, risk_manager)` — takes MonitoringConfig and RiskManager
- `ScalpingStrategy(config.strategy)` — takes StrategyConfig
- `PaperTrader(initial_capital=float)` — standalone, no config dependency

To test the full pipeline without DHAN credentials:
1. Create synthetic OHLCV DataFrame with numpy (50+ candles, clear trend)
2. Call `ScalpingStrategy.evaluate(df, spot_price)` → returns TradeSignal or None
3. Call `RiskManager.can_take_new_trade()` → (bool, str)
4. Call `PaperTrader.place_order(...)` for BUY
5. Call `RiskManager.register_trade(option_type, strike, entry_price, lots, security_id)`
6. Call `RiskManager.check_exit_conditions(trade, current_price)` at various prices
7. Call `PaperTrader.place_order(...)` for SELL
8. Call `RiskManager.close_trade(trade, exit_price, reason)` → returns P&L float
9. Verify with `Dashboard.render()` and `Dashboard.log_trade()`

## Key Config Values (config.yaml defaults)
- `lot_size`: 25 (on `config.instrument`, not `config.orders`)
- `max_lots_per_day`: 2
- `stop_loss_percent`: 20%
- `profit_target_percent`: 30%
- `trailing_stop_percent`: 15%
- `max_daily_loss`: 5000 INR
- `max_daily_profit`: 10000 INR
- `min_signals_for_entry`: 2 out of 4 indicators

## Common Issues
- `dhanhq` package might fail to install without `--pre` flag
- `RiskManager` constructor takes `lot_size: int` not `OrderConfig` — passing the wrong type causes `TypeError: unsupported operand type(s) for *`
- The app clears the terminal on each dashboard refresh (`os.system('clear')`) — use log file `scalper.log` to review output
- Market hours check uses IST timezone — testing outside 09:20-15:15 IST will cause the app to sleep-loop
