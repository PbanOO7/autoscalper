"""Main orchestrator that ties together all components of the autoscalper.

Manages the trading loop: fetching data, generating signals, placing orders,
monitoring positions, and enforcing risk management.
"""

import logging
import signal
import sys
import time
from datetime import datetime, timedelta

import pytz

from scalper.config import ScalperConfig, load_config, validate_config
from scalper.dhan_client import DhanClient, DhanAPIError
from scalper.dashboard import Dashboard
from scalper.paper_trader import PaperTrader
from scalper.risk_manager import ExitReason, RiskManager
from scalper.strategy import ScalpingStrategy

logger = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")


def _parse_time(time_str: str) -> datetime:
    """Parse HH:MM time string to today's datetime in IST."""
    now = datetime.now(IST)
    hour, minute = map(int, time_str.split(":"))
    return now.replace(hour=hour, minute=minute, second=0, microsecond=0)


def setup_logging(config: ScalperConfig) -> None:
    """Configure logging for the application.

    Args:
        config: ScalperConfig with monitoring settings.
    """
    log_level = getattr(logging, config.monitoring.log_level.upper(), logging.INFO)

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # File handler
    file_handler = logging.FileHandler(config.monitoring.log_file)
    file_handler.setLevel(log_level)
    file_handler.setFormatter(formatter)

    # Console handler (less verbose)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.WARNING)
    console_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)


class Orchestrator:
    """Main trading loop orchestrator.

    Coordinates between the DHAN API client, strategy engine,
    risk manager, and dashboard to run the autoscalper.
    """

    def __init__(self, config: ScalperConfig):
        self.config = config
        self._running = False
        self._current_expiry: str = ""

        # Initialize components
        self.dhan_client = DhanClient(
            config.dhan, config.instrument, config.orders
        )
        self.strategy = ScalpingStrategy(config.strategy)
        self.risk_manager = RiskManager(config.risk, config.instrument.lot_size)
        self.dashboard = Dashboard(config.monitoring, self.risk_manager)
        self.paper_trader: PaperTrader | None = None

        if config.paper_trading.enabled:
            self.paper_trader = PaperTrader(config.paper_trading.initial_capital)
            self.dashboard.set_paper_mode(True)
            logger.info("Paper trading mode enabled with capital: %.2f",
                        config.paper_trading.initial_capital)
        else:
            self.dashboard.set_paper_mode(False)
            logger.info("LIVE trading mode enabled")

    def _is_market_hours(self) -> bool:
        """Check if current time is within trading hours.

        Returns:
            True if within market hours, False otherwise.
        """
        now = datetime.now(IST)
        market_open = _parse_time(self.config.orders.market_open_time)
        auto_close = _parse_time(self.config.orders.auto_square_off_time)

        # Skip weekends
        if now.weekday() >= 5:
            return False

        return market_open <= now <= auto_close

    def _can_place_new_trade(self) -> bool:
        """Check if new trades are allowed based on time and risk rules.

        Returns:
            True if new trades can be placed.
        """
        now = datetime.now(IST)
        no_new_after = _parse_time(self.config.orders.no_new_trade_after)
        if now >= no_new_after:
            return False

        allowed, reason = self.risk_manager.can_take_new_trade()
        if not allowed:
            logger.debug("Cannot take new trade: %s", reason)
        return allowed

    def _execute_entry(
        self, option_type: str, strike: float, security_id: int, ltp: float
    ) -> bool:
        """Execute a trade entry (buy).

        Args:
            option_type: 'CE' or 'PE'.
            strike: Strike price.
            security_id: DHAN security ID.
            ltp: Last traded price of the option.

        Returns:
            True if order was placed successfully.
        """
        quantity = self.config.instrument.lot_size  # 1 lot

        try:
            if self.paper_trader:
                order = self.paper_trader.place_order(
                    security_id=security_id,
                    transaction_type="BUY",
                    quantity=quantity,
                    price=ltp,
                    option_type=option_type,
                    strike=strike,
                )
            else:
                order = self.dhan_client.place_order(
                    security_id=security_id,
                    transaction_type="BUY",
                    quantity=quantity,
                    price=ltp,
                )

            if order:
                trade = self.risk_manager.register_trade(
                    option_type=option_type,
                    strike=strike,
                    security_id=security_id,
                    entry_price=ltp,
                    lots=1,
                )
                self.dashboard.notify_trade(
                    f"ENTRY: BUY {option_type} {strike} @ {ltp:.2f} x {quantity} "
                    f"| SL={self.risk_manager.get_stop_loss_price(trade):.2f} "
                    f"| Target={self.risk_manager.get_target_price(trade):.2f}"
                )
                return True

        except DhanAPIError as e:
            logger.error("Entry order failed: %s", e)
        except Exception as e:
            logger.error("Unexpected error during entry: %s", e)

        return False

    def _execute_exit(
        self, trade, current_price: float, reason: ExitReason
    ) -> bool:
        """Execute a trade exit (sell).

        Args:
            trade: TradeRecord to exit.
            current_price: Current option premium.
            reason: Reason for exit.

        Returns:
            True if exit was successful.
        """
        try:
            if self.paper_trader:
                order = self.paper_trader.place_order(
                    security_id=trade.security_id,
                    transaction_type="SELL",
                    quantity=trade.quantity,
                    price=current_price,
                    option_type=trade.option_type,
                    strike=trade.strike,
                )
            else:
                order = self.dhan_client.place_order(
                    security_id=trade.security_id,
                    transaction_type="SELL",
                    quantity=trade.quantity,
                    price=current_price,
                )

            if order:
                pnl = self.risk_manager.close_trade(trade, current_price, reason)
                self.dashboard.log_trade(trade)
                self.dashboard.notify_trade(
                    f"EXIT: SELL {trade.option_type} {trade.strike} @ {current_price:.2f} "
                    f"| Reason={reason.value} | P&L={pnl:+.2f}"
                )
                return True

        except DhanAPIError as e:
            logger.error("Exit order failed: %s", e)
        except Exception as e:
            logger.error("Unexpected error during exit: %s", e)

        return False

    def _monitor_open_positions(self, option_chain: dict, candle_data) -> None:
        """Monitor open positions and check exit conditions.

        Args:
            option_chain: Current option chain data.
            candle_data: Current candle data for reversal detection.
        """
        open_trades = self.risk_manager.get_open_trades()

        for trade in open_trades:
            # Get current option price
            current_price = self.dhan_client.get_option_ltp(
                option_chain, trade.strike, trade.option_type
            )
            if current_price is None:
                logger.warning(
                    "Cannot get price for %s %s, skipping monitoring",
                    trade.option_type, trade.strike,
                )
                continue

            trade.update_peak(current_price)

            # Check risk-based exit conditions
            should_exit, reason = self.risk_manager.check_exit_conditions(
                trade, current_price
            )
            if should_exit and reason:
                self._execute_exit(trade, current_price, reason)
                continue

            # Check strategy reversal
            if candle_data is not None and not candle_data.empty:
                if self.strategy.should_exit_on_reversal(candle_data, trade.option_type):
                    self._execute_exit(trade, current_price, ExitReason.MANUAL)

    def _auto_square_off(self) -> None:
        """Square off all open positions at auto square-off time."""
        open_trades = self.risk_manager.get_open_trades()
        if not open_trades:
            return

        logger.info("Auto square-off triggered - closing %d positions", len(open_trades))

        for trade in open_trades:
            try:
                # For square-off, use last known price
                exit_price = trade.peak_price if trade.peak_price > 0 else trade.entry_price
                self._execute_exit(trade, exit_price, ExitReason.AUTO_SQUARE_OFF)
            except Exception as e:
                logger.error("Failed to square off %s: %s", trade.trade_id, e)

    def run(self) -> None:
        """Start the main trading loop.

        This is the main entry point that runs continuously during market hours.
        """
        logger.info("Starting Nifty50 Options AutoScalper...")
        self._running = True

        # Register signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)

        # Initialize DHAN client (skip for paper trading without credentials)
        if not self.config.paper_trading.enabled:
            try:
                self.dhan_client.initialize()
            except DhanAPIError as e:
                logger.critical("Failed to initialize DHAN client: %s", e)
                sys.exit(1)
        else:
            # In paper mode, still initialize if credentials are available
            if self.config.dhan.client_id and self.config.dhan.access_token:
                try:
                    self.dhan_client.initialize()
                except Exception:
                    logger.info("DHAN SDK not initialized - paper mode will use simulated data")

        # Fetch nearest expiry
        try:
            if self.config.dhan.client_id and self.config.dhan.access_token:
                self._current_expiry = self.dhan_client.get_nearest_expiry()
                logger.info("Trading expiry: %s", self._current_expiry)
            else:
                # Default to next Thursday for paper trading
                now = datetime.now(IST)
                days_ahead = 3 - now.weekday()  # Thursday = 3
                if days_ahead <= 0:
                    days_ahead += 7
                next_thursday = now + timedelta(days=days_ahead)
                self._current_expiry = next_thursday.strftime("%Y-%m-%d")
                logger.info("Paper mode expiry (estimated): %s", self._current_expiry)
        except Exception as e:
            logger.error("Failed to get expiry: %s", e)

        logger.info("AutoScalper initialized. Entering main loop...")
        self._main_loop()

    def _main_loop(self) -> None:
        """Main trading loop - runs continuously during market hours."""
        candle_data = None
        option_chain: dict = {}

        while self._running:
            try:
                now = datetime.now(IST)

                # Check market hours
                if not self._is_market_hours():
                    if now >= _parse_time(self.config.orders.auto_square_off_time):
                        # After market - square off and stop
                        self._auto_square_off()
                        summary = self.risk_manager.get_daily_summary()
                        logger.info("Market closed. Daily Summary: %s", summary)
                        self.dashboard.print_dashboard()
                        logger.info("Waiting for next trading day...")
                        # Sleep until next market open
                        time.sleep(60)
                        continue
                    else:
                        # Before market open
                        logger.debug("Waiting for market to open...")
                        time.sleep(30)
                        continue

                # Reset daily stats if new day
                self.risk_manager.reset_daily()

                # Fetch market data
                try:
                    if self.config.dhan.client_id and self.config.dhan.access_token:
                        # Fetch option chain
                        option_chain = self.dhan_client.get_option_chain(
                            self._current_expiry
                        )
                        spot_price = self.dhan_client.get_spot_price(option_chain)

                        # Fetch intraday candle data
                        candle_data = self.dhan_client.get_intraday_data(
                            security_id=self.config.instrument.underlying_security_id,
                            exchange_segment=self.config.instrument.underlying_segment,
                            interval=str(self.config.strategy.candle_timeframe),
                        )
                    else:
                        # Paper mode without API - log warning
                        logger.warning(
                            "No DHAN credentials - provide client_id and access_token "
                            "in config.yaml or environment variables for live data"
                        )
                        time.sleep(self.config.monitoring.dashboard_refresh_seconds)
                        continue

                except DhanAPIError as e:
                    logger.error("Data fetch error: %s", e)
                    time.sleep(5)
                    continue

                self.dashboard.update_spot(spot_price)

                # Monitor existing positions
                if self.risk_manager.get_open_trades():
                    self._monitor_open_positions(option_chain, candle_data)

                # Check for new signals
                if self._can_place_new_trade() and candle_data is not None:
                    trade_signal = self.strategy.evaluate(candle_data, spot_price)

                    if trade_signal:
                        # Select strike
                        strike, _ = self.dhan_client.select_strike(
                            spot_price,
                            trade_signal.option_type,
                            self.config.strategy.strike_mode,
                            self.config.strategy.strike_offset,
                        )

                        # Get security ID and LTP
                        security_id = self.dhan_client.find_option_security_id(
                            option_chain, strike, trade_signal.option_type
                        )
                        option_ltp = self.dhan_client.get_option_ltp(
                            option_chain, strike, trade_signal.option_type
                        )

                        if security_id and option_ltp and option_ltp > 0:
                            logger.info(
                                "Executing signal: %s %s @ strike=%s LTP=%.2f",
                                trade_signal.direction.value,
                                trade_signal.option_type,
                                strike,
                                option_ltp,
                            )
                            self._execute_entry(
                                trade_signal.option_type,
                                strike,
                                security_id,
                                option_ltp,
                            )
                        else:
                            logger.warning(
                                "Could not find option data for %s %s",
                                trade_signal.option_type, strike,
                            )

                # Update dashboard
                self.dashboard.update_strategy_status(self.strategy.get_status())
                self.dashboard.print_dashboard()

                # Check auto square-off time
                auto_close = _parse_time(self.config.orders.auto_square_off_time)
                if now >= auto_close:
                    self._auto_square_off()

                # Wait before next iteration
                time.sleep(self.config.monitoring.dashboard_refresh_seconds)

            except KeyboardInterrupt:
                self._handle_shutdown(None, None)
                break
            except Exception as e:
                logger.error("Error in main loop: %s", e, exc_info=True)
                time.sleep(5)

    def _handle_shutdown(self, signum, frame) -> None:
        """Handle graceful shutdown on SIGINT/SIGTERM."""
        logger.info("Shutdown signal received - squaring off positions...")
        self._running = False
        self._auto_square_off()

        summary = self.risk_manager.get_daily_summary()
        logger.info("Final Daily Summary: %s", summary)
        self.dashboard.print_dashboard()

        if self.paper_trader:
            portfolio = self.paper_trader.get_portfolio_value()
            logger.info("Paper Portfolio: %s", portfolio)

        logger.info("AutoScalper shut down gracefully.")


def main(config_path: str | None = None) -> None:
    """Main entry point for the autoscalper.

    Args:
        config_path: Optional path to config.yaml.
    """
    # Load configuration
    config = load_config(config_path)

    # Setup logging
    setup_logging(config)

    # Validate configuration
    issues = validate_config(config)
    if issues:
        for issue in issues:
            logger.error("Config error: %s", issue)
        if not config.paper_trading.enabled:
            logger.critical("Cannot start in live mode with config errors")
            sys.exit(1)
        else:
            logger.warning("Starting in paper mode despite config issues")

    # Print startup info
    logger.info("=" * 60)
    logger.info("Nifty50 Options AutoScalper v1.0.0")
    logger.info("Mode: %s", "PAPER" if config.paper_trading.enabled else "LIVE")
    logger.info("Max Lots/Day: %d", config.risk.max_lots_per_day)
    logger.info("Stop Loss: %.1f%%", config.risk.stop_loss_percent)
    logger.info("Profit Target: %.1f%%", config.risk.profit_target_percent)
    logger.info("Strategy: EMA(%d/%d) + RSI(%d) + VWAP + SuperTrend(%d/%.1f)",
                config.strategy.ema_fast_period,
                config.strategy.ema_slow_period,
                config.strategy.rsi_period,
                config.strategy.supertrend_period,
                config.strategy.supertrend_multiplier)
    logger.info("Min Signals: %d/4", config.strategy.min_signals_for_entry)
    logger.info("Strike Mode: %s (offset=%d)",
                config.strategy.strike_mode, config.strategy.strike_offset)
    logger.info("=" * 60)

    # Create and run orchestrator
    orchestrator = Orchestrator(config)
    orchestrator.run()
