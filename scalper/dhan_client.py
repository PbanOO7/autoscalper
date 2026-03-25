"""DHAN API client wrapper for market data, options chain, and order management.

Wraps the official dhanhq Python SDK and provides methods specific to
options scalping on Nifty50.
"""

import logging
import time
from datetime import date
from typing import Optional

import pandas as pd
import requests

from scalper.config import DhanConfig, InstrumentConfig, OrderConfig

logger = logging.getLogger(__name__)

# DHAN API base URL
DHAN_API_BASE = "https://api.dhan.co/v2"

# Nifty options strike interval
NIFTY_STRIKE_INTERVAL = 50


class DhanAPIError(Exception):
    """Custom exception for DHAN API errors."""
    pass


class DhanClient:
    """Client for interacting with DHAN trading APIs.

    Handles authentication, market data retrieval, option chain fetching,
    and order placement/management.
    """

    def __init__(
        self,
        dhan_config: DhanConfig,
        instrument_config: InstrumentConfig,
        order_config: OrderConfig,
    ):
        self.client_id = dhan_config.client_id
        self.access_token = dhan_config.access_token
        self.instrument = instrument_config
        self.order_config = order_config
        self._session = requests.Session()
        self._session.headers.update({
            "Content-Type": "application/json",
            "access-token": self.access_token,
            "client-id": self.client_id,
        })
        self._dhan = None
        self._last_option_chain_fetch: float = 0.0

    def initialize(self) -> None:
        """Initialize the DHAN SDK client for order management."""
        try:
            from dhanhq import DhanContext, DhanSDK
            DhanContext(self.client_id, self.access_token)
            self._dhan = DhanSDK()
            logger.info("DHAN SDK initialized successfully for client: %s", self.client_id)
        except ImportError:
            logger.warning(
                "dhanhq SDK not available, falling back to REST API. "
                "Install with: pip install --pre dhanhq"
            )
        except Exception as e:
            logger.error("Failed to initialize DHAN SDK: %s", e)
            raise DhanAPIError(f"DHAN SDK initialization failed: {e}") from e

    def _api_request(
        self, method: str, endpoint: str, json_data: Optional[dict] = None
    ) -> dict:
        """Make an authenticated API request to DHAN.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE).
            endpoint: API endpoint path (e.g., '/orders').
            json_data: Optional JSON request body.

        Returns:
            Parsed JSON response as dict.

        Raises:
            DhanAPIError: If the API request fails.
        """
        url = f"{DHAN_API_BASE}{endpoint}"
        try:
            response = self._session.request(method, url, json=json_data, timeout=10)
            if response.status_code not in (200, 201):
                raise DhanAPIError(
                    f"API request failed: {response.status_code} - {response.text}"
                )
            return response.json()
        except requests.RequestException as e:
            raise DhanAPIError(f"Network error: {e}") from e

    def get_expiry_list(self) -> list[str]:
        """Fetch available expiry dates for Nifty options.

        Returns:
            List of expiry date strings in YYYY-MM-DD format.
        """
        data = {
            "UnderlyingScrip": self.instrument.underlying_security_id,
            "UnderlyingSeg": self.instrument.underlying_segment,
        }
        response = self._api_request("POST", "/optionchain/expirylist", data)
        expiry_list = response.get("data", [])
        logger.info("Available expiries: %s", expiry_list)
        return sorted(expiry_list)

    def get_nearest_expiry(self) -> str:
        """Get the nearest (weekly) expiry date.

        Returns:
            Nearest expiry date string in YYYY-MM-DD format.
        """
        expiries = self.get_expiry_list()
        today = date.today().isoformat()
        future_expiries = [e for e in expiries if e >= today]
        if not future_expiries:
            raise DhanAPIError("No future expiry dates available")
        return future_expiries[0]

    def get_option_chain(self, expiry: str) -> dict:
        """Fetch the full option chain for Nifty for a given expiry.

        Respects rate limit of 1 request per 3 seconds.

        Args:
            expiry: Expiry date string in YYYY-MM-DD format.

        Returns:
            Option chain data dict with strike prices as keys.
        """
        # Rate limiting
        elapsed = time.time() - self._last_option_chain_fetch
        if elapsed < 3.0:
            time.sleep(3.0 - elapsed)

        data = {
            "UnderlyingScrip": self.instrument.underlying_security_id,
            "UnderlyingSeg": self.instrument.underlying_segment,
            "Expiry": expiry,
        }
        response = self._api_request("POST", "/optionchain", data)
        self._last_option_chain_fetch = time.time()

        result = response.get("data", {})
        logger.debug("Option chain fetched for expiry %s, spot: %s", expiry, result.get("last_price"))
        return result

    def get_spot_price(self, option_chain_data: Optional[dict] = None) -> float:
        """Get current Nifty spot price from option chain or LTP API.

        Args:
            option_chain_data: Pre-fetched option chain data (optional).

        Returns:
            Current Nifty spot price.
        """
        if option_chain_data and "last_price" in option_chain_data:
            return float(option_chain_data["last_price"])

        # Fallback to market quote API
        data = {
            "NSE_EQ": [self.instrument.underlying_security_id],
        }
        try:
            response = self._api_request("POST", "/marketfeed/ltp", data)
            return float(response.get("data", {}).get("NSE_EQ", {}).get(
                str(self.instrument.underlying_security_id), {}).get("last_price", 0))
        except Exception as e:
            logger.error("Failed to get spot price: %s", e)
            raise

    def select_strike(
        self,
        spot_price: float,
        option_type: str,
        mode: str = "ATM",
        offset: int = 0,
    ) -> tuple[float, dict]:
        """Select the appropriate strike price based on mode.

        Args:
            spot_price: Current spot price.
            option_type: 'CE' for call, 'PE' for put.
            mode: Strike selection mode (ATM, ITM1, ITM2, OTM1, OTM2).
            offset: Additional offset in number of strike intervals.

        Returns:
            Tuple of (strike_price, option_data_dict).
        """
        interval = NIFTY_STRIKE_INTERVAL
        atm_strike = round(spot_price / interval) * interval

        mode_offsets = {
            "ATM": 0,
            "ITM1": 1,
            "ITM2": 2,
            "OTM1": -1,
            "OTM2": -2,
        }
        mode_offset = mode_offsets.get(mode, 0)

        if option_type == "CE":
            # For calls: ITM = lower strike, OTM = higher strike
            strike = atm_strike - (mode_offset * interval) + (offset * interval)
        else:
            # For puts: ITM = higher strike, OTM = lower strike
            strike = atm_strike + (mode_offset * interval) + (offset * interval)

        return float(strike), {}

    def find_option_security_id(
        self, option_chain: dict, strike: float, option_type: str
    ) -> Optional[int]:
        """Find the security ID for a specific option from the option chain.

        Args:
            option_chain: Full option chain data.
            strike: Strike price.
            option_type: 'CE' or 'PE'.

        Returns:
            Security ID if found, None otherwise.
        """
        oc_data = option_chain.get("oc", {})
        strike_key = f"{strike:.6f}"

        # Try with and without trailing zeros
        for key in [strike_key, f"{strike:.1f}", str(int(strike)), f"{strike:g}"]:
            if key in oc_data:
                option_key = "ce" if option_type == "CE" else "pe"
                option_data = oc_data[key].get(option_key, {})
                sec_id = option_data.get("security_id")
                if sec_id:
                    return int(sec_id)

        # Search through all strikes
        for key, strikes_data in oc_data.items():
            try:
                if abs(float(key) - strike) < 1.0:
                    option_key = "ce" if option_type == "CE" else "pe"
                    option_data = strikes_data.get(option_key, {})
                    sec_id = option_data.get("security_id")
                    if sec_id:
                        return int(sec_id)
            except (ValueError, TypeError):
                continue

        logger.warning("Could not find security ID for %s %s", strike, option_type)
        return None

    def get_option_ltp(
        self, option_chain: dict, strike: float, option_type: str
    ) -> Optional[float]:
        """Get the last traded price for a specific option.

        Args:
            option_chain: Full option chain data.
            strike: Strike price.
            option_type: 'CE' or 'PE'.

        Returns:
            Last traded price if found, None otherwise.
        """
        oc_data = option_chain.get("oc", {})
        for key, strikes_data in oc_data.items():
            try:
                if abs(float(key) - strike) < 1.0:
                    option_key = "ce" if option_type == "CE" else "pe"
                    option_data = strikes_data.get(option_key, {})
                    ltp = option_data.get("last_price")
                    if ltp is not None:
                        return float(ltp)
            except (ValueError, TypeError):
                continue
        return None

    def place_order(
        self,
        security_id: int,
        transaction_type: str,
        quantity: int,
        price: float = 0.0,
        trigger_price: float = 0.0,
    ) -> dict:
        """Place an order via DHAN API.

        Args:
            security_id: Security ID of the option instrument.
            transaction_type: 'BUY' or 'SELL'.
            quantity: Number of shares (lot_size * num_lots).
            price: Limit price (0 for market orders).
            trigger_price: Trigger price for SL orders.

        Returns:
            Order response dict with orderId and status.
        """
        order_data = {
            "dhanClientId": self.client_id,
            "transactionType": transaction_type,
            "exchangeSegment": self.instrument.exchange_segment,
            "productType": self.order_config.product_type,
            "orderType": self.order_config.order_type,
            "validity": "DAY",
            "securityId": str(security_id),
            "quantity": quantity,
            "disclosedQuantity": 0,
            "price": price if self.order_config.order_type == "LIMIT" else 0,
            "triggerPrice": trigger_price,
            "afterMarketOrder": False,
        }

        logger.info(
            "Placing %s order: security=%s qty=%s type=%s",
            transaction_type, security_id, quantity, self.order_config.order_type,
        )

        try:
            response = self._api_request("POST", "/orders", order_data)
            order_id = response.get("orderId", response.get("data", {}).get("orderId"))
            logger.info("Order placed successfully: %s", order_id)
            return response
        except DhanAPIError as e:
            logger.error("Order placement failed: %s", e)
            raise

    def get_order_status(self, order_id: str) -> dict:
        """Get the status of an order.

        Args:
            order_id: The order ID to check.

        Returns:
            Order status dict.
        """
        return self._api_request("GET", f"/orders/{order_id}")

    def get_positions(self) -> list[dict]:
        """Get all current positions.

        Returns:
            List of position dicts.
        """
        response = self._api_request("GET", "/positions")
        return response.get("data", response) if isinstance(response, dict) else response

    def get_order_book(self) -> list[dict]:
        """Get today's order book.

        Returns:
            List of order dicts.
        """
        response = self._api_request("GET", "/orders")
        return response.get("data", response) if isinstance(response, dict) else response

    def get_trade_book(self) -> list[dict]:
        """Get today's trade book.

        Returns:
            List of trade dicts.
        """
        response = self._api_request("GET", "/trades")
        return response.get("data", response) if isinstance(response, dict) else response

    def cancel_order(self, order_id: str) -> dict:
        """Cancel a pending order.

        Args:
            order_id: The order ID to cancel.

        Returns:
            Cancellation response dict.
        """
        return self._api_request("DELETE", f"/orders/{order_id}")

    def get_intraday_data(
        self,
        security_id: int,
        exchange_segment: str,
        interval: str = "1",
    ) -> pd.DataFrame:
        """Fetch intraday OHLCV candle data.

        Args:
            security_id: Security ID of the instrument.
            exchange_segment: Exchange segment (e.g., 'NSE_EQ', 'IDX_I').
            interval: Candle interval in minutes ('1', '5', '15', '25', '60').

        Returns:
            DataFrame with columns: timestamp, open, high, low, close, volume.
        """
        data = {
            "securityId": str(security_id),
            "exchangeSegment": exchange_segment,
            "instrument": "INDEX",
        }

        try:
            response = self._api_request("POST", "/charts/intraday", data)
        except DhanAPIError as e:
            logger.error("Failed to fetch intraday data: %s", e)
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

        candles = response.get("data", response)
        if not candles:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

        # Parse DHAN candle format
        if isinstance(candles, dict):
            timestamps = candles.get("timestamp", candles.get("start_Time", []))
            opens = candles.get("open", [])
            highs = candles.get("high", [])
            lows = candles.get("low", [])
            closes = candles.get("close", [])
            volumes = candles.get("volume", [])

            df = pd.DataFrame({
                "timestamp": pd.to_datetime(timestamps, unit="s", utc=True),
                "open": opens,
                "high": highs,
                "low": lows,
                "close": closes,
                "volume": volumes,
            })
        elif isinstance(candles, list):
            df = pd.DataFrame(candles)
            if "timestamp" in df.columns:
                df["timestamp"] = pd.to_datetime(df["timestamp"])
        else:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

        if not df.empty and "timestamp" in df.columns:
            df = df.sort_values("timestamp").reset_index(drop=True)
            df.set_index("timestamp", inplace=True)

        return df

    def get_historical_data(
        self,
        security_id: int,
        exchange_segment: str,
        from_date: str,
        to_date: str,
    ) -> pd.DataFrame:
        """Fetch historical daily OHLCV data.

        Args:
            security_id: Security ID.
            exchange_segment: Exchange segment.
            from_date: Start date YYYY-MM-DD.
            to_date: End date YYYY-MM-DD.

        Returns:
            DataFrame with OHLCV data.
        """
        data = {
            "securityId": str(security_id),
            "exchangeSegment": exchange_segment,
            "instrument": "INDEX",
            "fromDate": from_date,
            "toDate": to_date,
            "expiryCode": 0,
        }
        try:
            response = self._api_request("POST", "/charts/historical", data)
        except DhanAPIError:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

        candles = response.get("data", response)
        if isinstance(candles, dict):
            timestamps = candles.get("timestamp", candles.get("start_Time", []))
            df = pd.DataFrame({
                "timestamp": pd.to_datetime(timestamps, unit="s", utc=True),
                "open": candles.get("open", []),
                "high": candles.get("high", []),
                "low": candles.get("low", []),
                "close": candles.get("close", []),
                "volume": candles.get("volume", []),
            })
        elif isinstance(candles, list):
            df = pd.DataFrame(candles)
        else:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

        if not df.empty and "timestamp" in df.columns:
            df = df.sort_values("timestamp").reset_index(drop=True)
            df.set_index("timestamp", inplace=True)

        return df
