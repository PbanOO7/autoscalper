"""Real-time terminal dashboard for monitoring the autoscalper.

Displays live P&L, open positions, signals, and trade history
in a formatted terminal output.
"""

import csv
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from scalper.config import MonitoringConfig
from scalper.risk_manager import RiskManager, TradeRecord

logger = logging.getLogger(__name__)

# ANSI color codes
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"


def _color_pnl(value: float) -> str:
    """Color a P&L value green (positive) or red (negative)."""
    if value > 0:
        return f"{GREEN}+{value:,.2f}{RESET}"
    elif value < 0:
        return f"{RED}{value:,.2f}{RESET}"
    return f"{value:,.2f}"


def _format_time(dt: Optional[datetime]) -> str:
    """Format datetime to HH:MM:SS string."""
    if dt is None:
        return "--:--:--"
    return dt.strftime("%H:%M:%S")


class Dashboard:
    """Terminal-based real-time monitoring dashboard.

    Displays trading status, open positions, P&L tracking,
    and recent signals in a clean terminal layout.
    """

    def __init__(self, config: MonitoringConfig, risk_manager: RiskManager):
        self.config = config
        self.risk_manager = risk_manager
        self._trade_log_initialized = False
        self._last_strategy_status: dict = {}
        self._spot_price: float = 0.0
        self._is_paper: bool = True

    def set_paper_mode(self, is_paper: bool) -> None:
        """Set whether we're in paper trading mode."""
        self._is_paper = is_paper

    def update_spot(self, price: float) -> None:
        """Update the current spot price for display."""
        self._spot_price = price

    def update_strategy_status(self, status: dict) -> None:
        """Update strategy status for display."""
        self._last_strategy_status = status

    def render(self) -> str:
        """Render the full dashboard as a string.

        Returns:
            Formatted dashboard string for terminal output.
        """
        summary = self.risk_manager.get_daily_summary()
        open_trades = self.risk_manager.get_open_trades()
        closed_trades = [t for t in self.risk_manager.trades if not t.is_open]

        lines = []
        width = 72

        # Header
        mode_label = f"{YELLOW}PAPER{RESET}" if self._is_paper else f"{RED}LIVE{RESET}"
        lines.append("")
        lines.append(f"{BOLD}{'=' * width}{RESET}")
        lines.append(
            f"{BOLD}  NIFTY50 OPTIONS AUTOSCALPER  [{mode_label}{BOLD}]"
            f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} IST{RESET}"
        )
        lines.append(f"{BOLD}{'=' * width}{RESET}")

        # Spot Price
        lines.append(f"  {CYAN}NIFTY Spot:{RESET} {BOLD}{self._spot_price:,.2f}{RESET}")
        lines.append("")

        # Daily Summary
        lines.append(f"  {BOLD}--- Daily Summary ---{RESET}")
        lines.append(
            f"  Trades: {summary['total_trades']}  |  "
            f"Lots: {summary['lots_used']}  |  "
            f"Win: {GREEN}{summary['winning_trades']}{RESET}  "
            f"Loss: {RED}{summary['losing_trades']}{RESET}"
        )
        lines.append(
            f"  Realized P&L: {_color_pnl(summary['realized_pnl'])}  |  "
            f"Unrealized: {_color_pnl(summary['unrealized_pnl'])}  |  "
            f"Total: {_color_pnl(summary['total_pnl'])}"
        )
        if summary["kill_switch"]:
            lines.append(f"  {RED}{BOLD}!! KILL SWITCH ACTIVE !!{RESET}")
        lines.append(f"  Max Drawdown: {RED}{summary['max_drawdown']:,.2f}{RESET}")
        lines.append("")

        # Open Positions
        lines.append(f"  {BOLD}--- Open Positions ---{RESET}")
        if open_trades:
            lines.append(
                f"  {'ID':<16} {'Type':<5} {'Strike':<10} "
                f"{'Entry':>8} {'Current':>8} {'P&L':>10} {'SL':>8} {'Tgt':>8}"
            )
            lines.append(f"  {'-' * 68}")
            for trade in open_trades:
                sl = self.risk_manager.get_stop_loss_price(trade)
                tgt = self.risk_manager.get_target_price(trade)
                unrealized = trade.calculate_pnl(trade.peak_price)
                lines.append(
                    f"  {trade.trade_id:<16} {trade.option_type:<5} "
                    f"{trade.strike:<10.0f} {trade.entry_price:>8.2f} "
                    f"{trade.peak_price:>8.2f} {_color_pnl(unrealized):>18} "
                    f"{sl:>8.2f} {tgt:>8.2f}"
                )
        else:
            lines.append(f"  {DIM}No open positions{RESET}")
        lines.append("")

        # Recent Closed Trades
        lines.append(f"  {BOLD}--- Recent Trades ---{RESET}")
        recent = closed_trades[-5:] if closed_trades else []
        if recent:
            lines.append(
                f"  {'ID':<16} {'Type':<5} {'Strike':<10} "
                f"{'Entry':>8} {'Exit':>8} {'P&L':>10} {'Reason':<15}"
            )
            lines.append(f"  {'-' * 68}")
            for trade in reversed(recent):
                exit_p = trade.exit_price if trade.exit_price else 0
                reason = trade.exit_reason.value if trade.exit_reason else "N/A"
                lines.append(
                    f"  {trade.trade_id:<16} {trade.option_type:<5} "
                    f"{trade.strike:<10.0f} {trade.entry_price:>8.2f} "
                    f"{exit_p:>8.2f} {_color_pnl(trade.pnl):>18} {reason:<15}"
                )
        else:
            lines.append(f"  {DIM}No closed trades yet{RESET}")
        lines.append("")

        # Strategy Status
        lines.append(f"  {BOLD}--- Strategy Status ---{RESET}")
        if self._last_strategy_status:
            last_sig = self._last_strategy_status.get("last_signal")
            if last_sig:
                sig_color = GREEN if last_sig["direction"] == "BULLISH" else RED
                lines.append(
                    f"  Last Signal: {sig_color}{last_sig['direction']}{RESET} "
                    f"({last_sig['option_type']}) | "
                    f"Strength: {last_sig['strength']}/4 | "
                    f"Time: {last_sig['time']}"
                )
                lines.append(f"  Reason: {last_sig['reason']}")
            else:
                lines.append(f"  {DIM}Waiting for signal...{RESET}")
            cooldown = self._last_strategy_status.get("cooldown_remaining", 0)
            if cooldown > 0:
                lines.append(f"  Cooldown: {cooldown} candles remaining")
        else:
            lines.append(f"  {DIM}Strategy not initialized{RESET}")

        lines.append(f"{BOLD}{'=' * width}{RESET}")
        lines.append("")

        return "\n".join(lines)

    def print_dashboard(self) -> None:
        """Clear screen and print the dashboard."""
        if not self.config.console_dashboard:
            return
        # Clear screen
        os.system("clear" if os.name != "nt" else "cls")
        print(self.render())

    def log_trade(self, trade: TradeRecord) -> None:
        """Log a trade to the CSV trade log file.

        Args:
            trade: Completed trade to log.
        """
        log_path = Path(self.config.trade_log_file)
        file_exists = log_path.exists()

        try:
            with open(log_path, "a", newline="") as f:
                writer = csv.writer(f)
                if not file_exists:
                    writer.writerow([
                        "trade_id", "entry_time", "exit_time", "option_type",
                        "strike", "entry_price", "exit_price", "quantity",
                        "lots", "pnl", "exit_reason",
                    ])
                writer.writerow([
                    trade.trade_id,
                    _format_time(trade.entry_time),
                    _format_time(trade.exit_time),
                    trade.option_type,
                    trade.strike,
                    trade.entry_price,
                    trade.exit_price or 0,
                    trade.quantity,
                    trade.lots,
                    round(trade.pnl, 2),
                    trade.exit_reason.value if trade.exit_reason else "",
                ])
        except OSError as e:
            logger.error("Failed to write trade log: %s", e)

    def notify_trade(self, message: str) -> None:
        """Print a trade notification to the console.

        Args:
            message: Notification message.
        """
        if self.config.notify_on_trade:
            timestamp = datetime.now().strftime("%H:%M:%S")
            print(f"\n  {BOLD}[{timestamp}] {YELLOW}>> {message}{RESET}\n")
