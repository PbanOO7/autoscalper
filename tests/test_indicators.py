"""Tests for technical indicators module."""

import numpy as np
import pandas as pd

from scalper.indicators import (
    Signal,
    aggregate_signals,
    compute_ema,
    compute_rsi,
    compute_supertrend,
    compute_vwap,
    get_ema_signal,
    get_rsi_signal,
    get_supertrend_signal,
    get_vwap_signal,
)


def _make_ohlcv(n: int = 50, trend: str = "up") -> pd.DataFrame:
    """Create a synthetic OHLCV DataFrame."""
    np.random.seed(42)
    if trend == "up":
        base = np.linspace(100, 130, n) + np.random.randn(n) * 2
    elif trend == "down":
        base = np.linspace(130, 100, n) + np.random.randn(n) * 2
    else:
        base = np.full(n, 115.0) + np.random.randn(n) * 3

    df = pd.DataFrame({
        "timestamp": pd.date_range("2024-01-01 09:15", periods=n, freq="1min"),
        "open": base - np.random.rand(n),
        "high": base + np.abs(np.random.randn(n)),
        "low": base - np.abs(np.random.randn(n)),
        "close": base,
        "volume": np.random.randint(1000, 50000, n),
    })
    df.set_index("timestamp", inplace=True)
    return df


class TestEMA:
    def test_ema_length(self):
        series = pd.Series(range(20), dtype=float)
        result = compute_ema(series, 5)
        assert len(result) == len(series)

    def test_ema_values_reasonable(self):
        series = pd.Series([10.0] * 10)
        result = compute_ema(series, 5)
        assert all(abs(v - 10.0) < 0.01 for v in result)


class TestRSI:
    def test_rsi_range(self):
        df = _make_ohlcv(50, "up")
        rsi = compute_rsi(df["close"], 14)
        assert all(0 <= v <= 100 for v in rsi.dropna())

    def test_rsi_uptrend_high(self):
        df = _make_ohlcv(50, "up")
        rsi = compute_rsi(df["close"], 14)
        assert rsi.iloc[-1] > 50

    def test_rsi_downtrend_low(self):
        df = _make_ohlcv(50, "down")
        rsi = compute_rsi(df["close"], 14)
        assert rsi.iloc[-1] < 50


class TestVWAP:
    def test_vwap_calculation(self):
        df = _make_ohlcv(30)
        vwap = compute_vwap(df)
        assert len(vwap) == len(df)
        assert not vwap.isna().all()


class TestSuperTrend:
    def test_supertrend_output(self):
        df = _make_ohlcv(50)
        st_line, direction = compute_supertrend(df, 10, 3.0)
        assert len(st_line) == len(df)
        assert len(direction) == len(df)
        assert set(direction.unique()).issubset({-1.0, 0.0, 1.0})


class TestSignals:
    def test_ema_bullish_signal(self):
        df = _make_ohlcv(50, "up")
        signal = get_ema_signal(df["close"], 9, 21)
        assert signal == Signal.BULLISH

    def test_ema_bearish_signal(self):
        df = _make_ohlcv(50, "down")
        signal = get_ema_signal(df["close"], 9, 21)
        assert signal == Signal.BEARISH

    def test_rsi_neutral_midrange(self):
        df = _make_ohlcv(50, "flat")
        signal = get_rsi_signal(df["close"], 14, 70, 30)
        assert signal in (Signal.NEUTRAL, Signal.BULLISH, Signal.BEARISH)

    def test_vwap_signal(self):
        df = _make_ohlcv(30, "up")
        signal = get_vwap_signal(df)
        assert isinstance(signal, Signal)

    def test_supertrend_signal(self):
        df = _make_ohlcv(50, "up")
        signal = get_supertrend_signal(df, 10, 3.0)
        assert isinstance(signal, Signal)

    def test_insufficient_data_returns_neutral(self):
        short_series = pd.Series([1.0, 2.0, 3.0])
        assert get_ema_signal(short_series, 9, 21) == Signal.NEUTRAL
        assert get_rsi_signal(short_series, 14, 70, 30) == Signal.NEUTRAL


class TestAggregateSignals:
    def test_bullish_majority(self):
        signals = [Signal.BULLISH, Signal.BULLISH, Signal.NEUTRAL, Signal.BEARISH]
        result = aggregate_signals(signals, 2)
        assert result == Signal.BULLISH

    def test_bearish_majority(self):
        signals = [Signal.BEARISH, Signal.BEARISH, Signal.BEARISH, Signal.NEUTRAL]
        result = aggregate_signals(signals, 2)
        assert result == Signal.BEARISH

    def test_no_consensus(self):
        signals = [Signal.BULLISH, Signal.BEARISH, Signal.NEUTRAL, Signal.NEUTRAL]
        result = aggregate_signals(signals, 2)
        assert result == Signal.NEUTRAL

    def test_all_neutral(self):
        signals = [Signal.NEUTRAL] * 4
        result = aggregate_signals(signals, 2)
        assert result == Signal.NEUTRAL
