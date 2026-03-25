"""Risk management module for the autoscalper.

Enforces per-trade stop-loss/profit targets, trailing stops,
daily P&L limits, and maximum lot constraints.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional

from scalper.config import RiskConfig

logger = logging.getLogger(__name__)


class ExitReason(Enum):
    """Reason for exiting a trade."""
    STOP_LOSS = "STOP_LOSS"
    PROFIT_TARGET = "PROFIT_TARGET"
    TRAILING_STOP = "TRAILING_STOP"
    DAILY_LOSS_LIMIT = "DAILY_LOSS_LIMIT"
    DAILY_PROFIT_LIMIT = "DAILY_PROFIT_LIMIT"
    AUTO_SQUARE_OFF = "AUTO_SQUARE_OFF"
    MANUAL = "MANUAL"


@dataclass
class TradeRecord:
    """Record of a single trade."""
    trade_id: str
    entry_time: datetime
    option_type: str            # CE or PE
    strike: float
    security_id: int
    entry_price: float
    quantity: int
    lots: int
    exit_time: Optional[datetime] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[ExitReason] = None
    pnl: float = 0.0
    peak_price: float = 0.0     # Highest price since entry (for trailing stop)
    is_open: bool = True

    def update_peak(self, current_price: float) -> None:
        """Update the peak price for trailing stop calculation."""
        if current_price > self.peak_price:
            self.peak_price = current_price

    def calculate_pnl(self, current_price: float) -> float:
        """Calculate unrealized P&L at a given price.

        Args:
            current_price: Current option premium.

        Returns:
            Unrealized P&L in INR.
        """
        return (current_price - self.entry_price) * self.quantity

    def close(self, exit_price: float, reason: ExitReason) -> None:
        """Close the trade with exit details.

        Args:
            exit_price: Price at which the trade was exited.
            reason: Reason for exit.
        """
        self.exit_time = datetime.now()
        self.exit_price = exit_price
        self.exit_reason = reason
        self.pnl = (exit_price - self.entry_price) * self.quantity
        self.is_open = False


@dataclass
class DailyStats:
    """Daily trading statistics."""
    date: str
    total_lots_used: int = 0
    total_trades: int = 0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    winning_trades: int = 0
    losing_trades: int = 0
    max_drawdown: float = 0.0
    peak_pnl: float = 0.0
    is_killed: bool = False       # Kill switch activated
    kill_reason: str = ""


class RiskManager:
    """Manages risk for all trades and enforces daily limits.

    Tracks open positions, calculates stop-loss and profit targets,
    manages trailing stops, and enforces daily P&L limits.
    """

    def __init__(self, config: RiskConfig, lot_size: int):
        self.config = config
        self.lot_size = lot_size
        self.trades: list[TradeRecord] = []
        self.daily_stats = DailyStats(date=datetime.now().strftime("%Y-%m-%d"))
        self._trade_counter = 0

    def reset_daily(self) -> None:
        """Reset daily statistics for a new trading day."""
        today = datetime.now().strftime("%Y-%m-%d")
        if self.daily_stats.date != today:
            logger.info(
                "New trading day. Previous day stats: PnL=%.2f, Trades=%d",
                self.daily_stats.realized_pnl,
                self.daily_stats.total_trades,
            )
            self.daily_stats = DailyStats(date=today)
            self.trades = [t for t in self.trades if t.is_open]
            self._trade_counter = 0

    def can_take_new_trade(self) -> tuple[bool, str]:
        """Check if a new trade is allowed based on risk rules.

        Returns:
            Tuple of (allowed: bool, reason: str).
        """
        self.reset_daily()

        # Kill switch check
        if self.daily_stats.is_killed:
            return False, f"Kill switch active: {self.daily_stats.kill_reason}"

        # Max lots per day
        if self.daily_stats.total_lots_used >= self.config.max_lots_per_day:
            return False, (
                f"Daily lot limit reached: {self.daily_stats.total_lots_used}"
                f"/{self.config.max_lots_per_day}"
            )

        # Max open positions
        open_positions = self.get_open_trades()
        if len(open_positions) >= self.config.max_open_positions:
            return False, (
                f"Max open positions reached: {len(open_positions)}"
                f"/{self.config.max_open_positions}"
            )

        # Daily loss limit
        total_pnl = self.daily_stats.realized_pnl + self._get_total_unrealized_pnl()
        if total_pnl <= -self.config.max_daily_loss:
            self._activate_kill_switch("Daily loss limit hit")
            return False, f"Daily loss limit hit: {total_pnl:.2f}"

        # Daily profit limit
        if self.daily_stats.realized_pnl >= self.config.max_daily_profit:
            return False, f"Daily profit target reached: {self.daily_stats.realized_pnl:.2f}"

        return True, "OK"

    def register_trade(
        self,
        option_type: str,
        strike: float,
        security_id: int,
        entry_price: float,
        lots: int = 1,
    ) -> TradeRecord:
        """Register a new trade entry.

        Args:
            option_type: 'CE' or 'PE'.
            strike: Strike price.
            security_id: DHAN security ID.
            entry_price: Entry premium price.
            lots: Number of lots.

        Returns:
            TradeRecord for the new trade.
        """
        self._trade_counter += 1
        trade_id = f"{self.daily_stats.date}-{self._trade_counter:04d}"
        quantity = lots * self.lot_size

        trade = TradeRecord(
            trade_id=trade_id,
            entry_time=datetime.now(),
            option_type=option_type,
            strike=strike,
            security_id=security_id,
            entry_price=entry_price,
            quantity=quantity,
            lots=lots,
            peak_price=entry_price,
        )
        self.trades.append(trade)
        self.daily_stats.total_lots_used += lots
        self.daily_stats.total_trades += 1

        logger.info(
            "Trade registered: %s | %s %s @ %.2f | Qty=%d | Lots=%d",
            trade_id, option_type, strike, entry_price, quantity, lots,
        )
        return trade

    def check_exit_conditions(
        self, trade: TradeRecord, current_price: float
    ) -> tuple[bool, Optional[ExitReason]]:
        """Check if a trade should be exited based on risk rules.

        Args:
            trade: The open trade to check.
            current_price: Current option premium.

        Returns:
            Tuple of (should_exit: bool, reason: ExitReason or None).
        """
        if not trade.is_open:
            return False, None

        trade.update_peak(current_price)

        # Stop-loss check
        sl_price = trade.entry_price * (1 - self.config.stop_loss_percent / 100.0)
        if current_price <= sl_price:
            logger.warning(
                "STOP LOSS triggered for %s: price=%.2f <= SL=%.2f",
                trade.trade_id, current_price, sl_price,
            )
            return True, ExitReason.STOP_LOSS

        # Profit target check
        target_price = trade.entry_price * (1 + self.config.profit_target_percent / 100.0)
        if current_price >= target_price:
            logger.info(
                "PROFIT TARGET reached for %s: price=%.2f >= target=%.2f",
                trade.trade_id, current_price, target_price,
            )
            return True, ExitReason.PROFIT_TARGET

        # Trailing stop check
        if self.config.trailing_stop_enabled and trade.peak_price > trade.entry_price:
            trailing_sl = trade.peak_price * (1 - self.config.trailing_stop_percent / 100.0)
            if current_price <= trailing_sl and trailing_sl > sl_price:
                logger.info(
                    "TRAILING STOP triggered for %s: price=%.2f <= trail=%.2f (peak=%.2f)",
                    trade.trade_id, current_price, trailing_sl, trade.peak_price,
                )
                return True, ExitReason.TRAILING_STOP

        # Daily loss limit (across all positions)
        total_pnl = self.daily_stats.realized_pnl + self._get_total_unrealized_pnl()
        if total_pnl <= -self.config.max_daily_loss:
            self._activate_kill_switch("Daily loss limit hit during trade")
            return True, ExitReason.DAILY_LOSS_LIMIT

        return False, None

    def close_trade(
        self, trade: TradeRecord, exit_price: float, reason: ExitReason
    ) -> float:
        """Close a trade and update daily statistics.

        Args:
            trade: The trade to close.
            exit_price: Exit premium price.
            reason: Reason for exit.

        Returns:
            Realized P&L for the trade.
        """
        trade.close(exit_price, reason)
        self.daily_stats.realized_pnl += trade.pnl

        if trade.pnl > 0:
            self.daily_stats.winning_trades += 1
        else:
            self.daily_stats.losing_trades += 1

        # Update peak and drawdown
        if self.daily_stats.realized_pnl > self.daily_stats.peak_pnl:
            self.daily_stats.peak_pnl = self.daily_stats.realized_pnl
        drawdown = self.daily_stats.peak_pnl - self.daily_stats.realized_pnl
        if drawdown > self.daily_stats.max_drawdown:
            self.daily_stats.max_drawdown = drawdown

        logger.info(
            "Trade closed: %s | Reason=%s | PnL=%.2f | Daily PnL=%.2f",
            trade.trade_id, reason.value, trade.pnl, self.daily_stats.realized_pnl,
        )
        return trade.pnl

    def get_open_trades(self) -> list[TradeRecord]:
        """Get all currently open trades.

        Returns:
            List of open TradeRecord instances.
        """
        return [t for t in self.trades if t.is_open]

    def get_stop_loss_price(self, trade: TradeRecord) -> float:
        """Calculate current stop-loss price for a trade.

        Args:
            trade: The trade to calculate SL for.

        Returns:
            Stop-loss price.
        """
        base_sl = trade.entry_price * (1 - self.config.stop_loss_percent / 100.0)

        if self.config.trailing_stop_enabled and trade.peak_price > trade.entry_price:
            trailing_sl = trade.peak_price * (1 - self.config.trailing_stop_percent / 100.0)
            return max(base_sl, trailing_sl)

        return base_sl

    def get_target_price(self, trade: TradeRecord) -> float:
        """Calculate profit target price for a trade.

        Args:
            trade: The trade to calculate target for.

        Returns:
            Target price.
        """
        return trade.entry_price * (1 + self.config.profit_target_percent / 100.0)

    def _get_total_unrealized_pnl(self) -> float:
        """Calculate total unrealized P&L across open positions."""
        return sum(t.pnl for t in self.trades if t.is_open)

    def _activate_kill_switch(self, reason: str) -> None:
        """Activate the daily kill switch."""
        self.daily_stats.is_killed = True
        self.daily_stats.kill_reason = reason
        logger.critical("KILL SWITCH ACTIVATED: %s", reason)

    def get_daily_summary(self) -> dict:
        """Get a summary of daily trading activity.

        Returns:
            Dict with daily statistics.
        """
        open_trades = self.get_open_trades()
        unrealized = sum(
            t.calculate_pnl(t.peak_price) for t in open_trades
        )

        return {
            "date": self.daily_stats.date,
            "total_trades": self.daily_stats.total_trades,
            "open_positions": len(open_trades),
            "lots_used": f"{self.daily_stats.total_lots_used}/{self.config.max_lots_per_day}",
            "realized_pnl": round(self.daily_stats.realized_pnl, 2),
            "unrealized_pnl": round(unrealized, 2),
            "total_pnl": round(self.daily_stats.realized_pnl + unrealized, 2),
            "winning_trades": self.daily_stats.winning_trades,
            "losing_trades": self.daily_stats.losing_trades,
            "max_drawdown": round(self.daily_stats.max_drawdown, 2),
            "kill_switch": self.daily_stats.is_killed,
        }
