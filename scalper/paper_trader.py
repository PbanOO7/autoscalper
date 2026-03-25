"""Paper trading engine that simulates order execution without real money.

Provides the same interface as the DHAN client for order placement
but executes everything locally with simulated fills.
"""

import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


class PaperTrader:
    """Simulated trading engine for paper trading mode.

    Maintains a virtual portfolio, tracks orders, and simulates fills
    at the last traded price (no slippage simulation).
    """

    def __init__(self, initial_capital: float = 100000.0):
        self.initial_capital = initial_capital
        self.capital = initial_capital
        self.positions: list[dict] = []
        self.order_book: list[dict] = []
        self.trade_book: list[dict] = []
        self._order_counter = 0

    def place_order(
        self,
        security_id: int,
        transaction_type: str,
        quantity: int,
        price: float,
        option_type: str = "",
        strike: float = 0.0,
    ) -> dict:
        """Simulate placing an order.

        Args:
            security_id: Security ID (for record keeping).
            transaction_type: 'BUY' or 'SELL'.
            quantity: Number of shares.
            price: Execution price (simulated at this price).
            option_type: 'CE' or 'PE' (for display).
            strike: Strike price (for display).

        Returns:
            Simulated order response dict.
        """
        self._order_counter += 1
        order_id = f"PAPER-{self._order_counter:06d}"
        timestamp = datetime.now()

        order = {
            "orderId": order_id,
            "status": "TRADED",
            "securityId": security_id,
            "transactionType": transaction_type,
            "quantity": quantity,
            "price": price,
            "tradedPrice": price,
            "tradedQuantity": quantity,
            "optionType": option_type,
            "strike": strike,
            "timestamp": timestamp.isoformat(),
            "exchangeSegment": "NSE_FNO",
            "productType": "INTRADAY",
        }

        self.order_book.append(order)

        # Record trade
        trade = {
            "orderId": order_id,
            "tradedPrice": price,
            "tradedQuantity": quantity,
            "transactionType": transaction_type,
            "timestamp": timestamp.isoformat(),
        }
        self.trade_book.append(trade)

        # Update position
        if transaction_type == "BUY":
            cost = price * quantity
            self.capital -= cost
            self.positions.append({
                "securityId": security_id,
                "quantity": quantity,
                "averagePrice": price,
                "optionType": option_type,
                "strike": strike,
            })
            logger.info(
                "[PAPER] BUY %s %s @ %.2f x %d | Cost=%.2f | Capital=%.2f",
                option_type, strike, price, quantity, cost, self.capital,
            )
        elif transaction_type == "SELL":
            proceeds = price * quantity
            self.capital += proceeds
            # Remove position
            self.positions = [
                p for p in self.positions
                if not (p["securityId"] == security_id and p["quantity"] == quantity)
            ]
            logger.info(
                "[PAPER] SELL %s %s @ %.2f x %d | Proceeds=%.2f | Capital=%.2f",
                option_type, strike, price, quantity, proceeds, self.capital,
            )

        return order

    def get_positions(self) -> list[dict]:
        """Get current paper trading positions.

        Returns:
            List of position dicts.
        """
        return self.positions.copy()

    def get_order_book(self) -> list[dict]:
        """Get paper trading order book.

        Returns:
            List of order dicts.
        """
        return self.order_book.copy()

    def get_trade_book(self) -> list[dict]:
        """Get paper trading trade book.

        Returns:
            List of trade dicts.
        """
        return self.trade_book.copy()

    def get_portfolio_value(self, current_prices: Optional[dict] = None) -> dict:
        """Calculate current portfolio value.

        Args:
            current_prices: Dict mapping security_id to current price.

        Returns:
            Portfolio summary dict.
        """
        positions_value = 0.0
        if current_prices:
            for pos in self.positions:
                sec_id = pos["securityId"]
                if sec_id in current_prices:
                    positions_value += current_prices[sec_id] * pos["quantity"]
                else:
                    positions_value += pos["averagePrice"] * pos["quantity"]
        else:
            for pos in self.positions:
                positions_value += pos["averagePrice"] * pos["quantity"]

        total_value = self.capital + positions_value
        pnl = total_value - self.initial_capital

        return {
            "initial_capital": self.initial_capital,
            "available_capital": round(self.capital, 2),
            "positions_value": round(positions_value, 2),
            "total_value": round(total_value, 2),
            "pnl": round(pnl, 2),
            "pnl_percent": round((pnl / self.initial_capital) * 100, 2),
            "total_orders": len(self.order_book),
        }
