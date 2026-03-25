"""Tests for the scalping strategy engine."""

import numpy as np
import pandas as pd
import pytest

from scalper.config import StrategyConfig
from scalper.strategy import ScalpingStrategy, TradeSignal


def _make_trending_data(n: int = 50, trend: str = "up") -> pd.DataFrame:
    """Create synthetic trending OHLCV data."""
    np.random.seed(42)
    if trend == "up":
        base = np.linspace(100, 140, n) + np.random.randn(n) * 1.5
    elif trend == "down":
        base = np.linspace(140, 100, n) + np.random.randn(n) * 1.5
    else:
        base = np.full(n, 120.0) + np.random.randn(n) * 2

    return pd.DataFrame({
        "timestamp": pd.date_range("2024-01-01 09:15", periods=n, freq="1min"),
        "open": base - 0.5,
        "high": base + np.abs(np.random.randn(n)),
        "low": base - np.abs(np.random.randn(n)),
        "close": base,
        "volume": np.random.randint(5000, 100000, n),
    }).set_index("timestamp")


@pytest.fixture
def strategy():
    config = StrategyConfig(
        ema_fast_period=9,
        ema_slow_period=21,
        rsi_period=14,
        rsi_overbought=70,
        rsi_oversold=30,
        supertrend_period=10,
        supertrend_multiplier=3.0,
        min_signals_for_entry=2,
    )
    return ScalpingStrategy(config)


class TestScalpingStrategy:
    def test_uptrend_generates_bullish_or_neutral(self, strategy):
        data = _make_trending_data(50, "up")
        signal = strategy.evaluate(data, 23500.0)
        if signal is not None:
            assert signal.option_type == "CE"
            assert signal.direction.value == "BULLISH"

    def test_downtrend_generates_bearish_or_neutral(self, strategy):
        data = _make_trending_data(50, "down")
        # Reset cooldown
        strategy._candle_count_since_signal = 999
        signal = strategy.evaluate(data, 23500.0)
        if signal is not None:
            assert signal.option_type == "PE"
            assert signal.direction.value == "BEARISH"

    def test_insufficient_data_returns_none(self, strategy):
        data = _make_trending_data(5)
        signal = strategy.evaluate(data, 23500.0)
        assert signal is None

    def test_cooldown_prevents_rapid_signals(self, strategy):
        data = _make_trending_data(50, "up")
        signal1 = strategy.evaluate(data, 23500.0)

        # Immediately evaluate again - should be in cooldown
        signal2 = strategy.evaluate(data, 23500.0)
        if signal1 is not None:
            assert signal2 is None

    def test_signal_has_required_fields(self, strategy):
        data = _make_trending_data(50, "up")
        signal = strategy.evaluate(data, 23500.0)
        if signal is not None:
            assert isinstance(signal, TradeSignal)
            assert signal.option_type in ("CE", "PE")
            assert signal.strength >= 2
            assert signal.spot_price == 23500.0
            assert signal.reason

    def test_get_status(self, strategy):
        status = strategy.get_status()
        assert "last_signal" in status
        assert "cooldown_remaining" in status

    def test_reversal_detection(self, strategy):
        data = _make_trending_data(50, "down")
        result = strategy.should_exit_on_reversal(data, "CE")
        assert isinstance(result, bool)
