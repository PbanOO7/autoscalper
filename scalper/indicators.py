"""Technical indicators for scalping strategy signals.

Provides EMA, RSI, VWAP, and SuperTrend calculations on OHLCV candle data
represented as pandas DataFrames.
"""

from enum import Enum

import numpy as np
import pandas as pd


class Signal(Enum):
    """Trading signal direction."""
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


def compute_ema(series: pd.Series, period: int) -> pd.Series:
    """Compute Exponential Moving Average.

    Args:
        series: Price series (typically close prices).
        period: EMA lookback period.

    Returns:
        EMA values as a pandas Series.
    """
    return series.ewm(span=period, adjust=False).mean()


def compute_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Compute Relative Strength Index.

    Args:
        series: Price series (typically close prices).
        period: RSI lookback period.

    Returns:
        RSI values (0-100) as a pandas Series.
    """
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)

    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi.fillna(50.0)


def compute_vwap(df: pd.DataFrame) -> pd.Series:
    """Compute Volume Weighted Average Price.

    Resets daily. Expects columns: high, low, close, volume
    and a DatetimeIndex or a 'timestamp' column.

    Args:
        df: OHLCV DataFrame with high, low, close, volume columns.

    Returns:
        VWAP values as a pandas Series.
    """
    typical_price = (df["high"] + df["low"] + df["close"]) / 3.0
    tp_volume = typical_price * df["volume"]

    # Group by date for daily reset
    if isinstance(df.index, pd.DatetimeIndex):
        date_groups = df.index.date
    elif "timestamp" in df.columns:
        date_groups = pd.to_datetime(df["timestamp"]).dt.date
    else:
        # No date info - compute cumulative without reset
        cum_tp_vol = tp_volume.cumsum()
        cum_vol = df["volume"].cumsum()
        return cum_tp_vol / cum_vol.replace(0, np.nan)

    cum_tp_vol = tp_volume.groupby(date_groups).cumsum()
    cum_vol = df["volume"].groupby(date_groups).cumsum()
    return cum_tp_vol / cum_vol.replace(0, np.nan)


def compute_supertrend(
    df: pd.DataFrame, period: int = 10, multiplier: float = 3.0
) -> tuple[pd.Series, pd.Series]:
    """Compute SuperTrend indicator.

    Args:
        df: OHLCV DataFrame with high, low, close columns.
        period: ATR lookback period.
        multiplier: ATR multiplier for band width.

    Returns:
        Tuple of (supertrend_line, direction) where direction is
        1 for uptrend (bullish) and -1 for downtrend (bearish).
    """
    high = df["high"]
    low = df["low"]
    close = df["close"]

    # ATR calculation
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.ewm(span=period, adjust=False).mean()

    # Basic bands
    hl2 = (high + low) / 2.0
    upper_basic = hl2 + (multiplier * atr)
    lower_basic = hl2 - (multiplier * atr)

    n = len(df)
    supertrend = pd.Series(np.zeros(n), index=df.index)
    direction = pd.Series(np.ones(n), index=df.index)

    upper_band = upper_basic.copy()
    lower_band = lower_basic.copy()

    for i in range(1, n):
        # Upper band
        if upper_basic.iloc[i] < upper_band.iloc[i - 1] or close.iloc[i - 1] > upper_band.iloc[i - 1]:
            upper_band.iloc[i] = upper_basic.iloc[i]
        else:
            upper_band.iloc[i] = upper_band.iloc[i - 1]

        # Lower band
        if lower_basic.iloc[i] > lower_band.iloc[i - 1] or close.iloc[i - 1] < lower_band.iloc[i - 1]:
            lower_band.iloc[i] = lower_basic.iloc[i]
        else:
            lower_band.iloc[i] = lower_band.iloc[i - 1]

        # Direction and SuperTrend value
        if supertrend.iloc[i - 1] == upper_band.iloc[i - 1]:
            if close.iloc[i] <= upper_band.iloc[i]:
                supertrend.iloc[i] = upper_band.iloc[i]
                direction.iloc[i] = -1
            else:
                supertrend.iloc[i] = lower_band.iloc[i]
                direction.iloc[i] = 1
        else:
            if close.iloc[i] >= lower_band.iloc[i]:
                supertrend.iloc[i] = lower_band.iloc[i]
                direction.iloc[i] = 1
            else:
                supertrend.iloc[i] = upper_band.iloc[i]
                direction.iloc[i] = -1

    return supertrend, direction


def get_ema_signal(
    close: pd.Series, fast_period: int, slow_period: int
) -> Signal:
    """Generate signal from EMA crossover.

    Args:
        close: Close price series.
        fast_period: Fast EMA period.
        slow_period: Slow EMA period.

    Returns:
        Signal.BULLISH if fast EMA crosses above slow EMA,
        Signal.BEARISH if fast EMA crosses below slow EMA,
        Signal.NEUTRAL otherwise.
    """
    if len(close) < slow_period + 2:
        return Signal.NEUTRAL

    ema_fast = compute_ema(close, fast_period)
    ema_slow = compute_ema(close, slow_period)

    current_fast = ema_fast.iloc[-1]
    current_slow = ema_slow.iloc[-1]
    prev_fast = ema_fast.iloc[-2]
    prev_slow = ema_slow.iloc[-2]

    # Crossover detection
    if prev_fast <= prev_slow and current_fast > current_slow:
        return Signal.BULLISH
    elif prev_fast >= prev_slow and current_fast < current_slow:
        return Signal.BEARISH

    # Trend continuation
    if current_fast > current_slow:
        return Signal.BULLISH
    elif current_fast < current_slow:
        return Signal.BEARISH

    return Signal.NEUTRAL


def get_rsi_signal(
    close: pd.Series, period: int, overbought: float, oversold: float
) -> Signal:
    """Generate signal from RSI levels.

    Args:
        close: Close price series.
        period: RSI period.
        overbought: Overbought threshold (e.g., 70).
        oversold: Oversold threshold (e.g., 30).

    Returns:
        Signal.BULLISH if RSI is in oversold zone,
        Signal.BEARISH if RSI is in overbought zone,
        Signal.NEUTRAL otherwise.
    """
    if len(close) < period + 2:
        return Signal.NEUTRAL

    rsi = compute_rsi(close, period)
    current_rsi = rsi.iloc[-1]

    if current_rsi <= oversold:
        return Signal.BULLISH
    elif current_rsi >= overbought:
        return Signal.BEARISH

    return Signal.NEUTRAL


def get_vwap_signal(df: pd.DataFrame) -> Signal:
    """Generate signal from VWAP crossover.

    Args:
        df: OHLCV DataFrame.

    Returns:
        Signal.BULLISH if price crosses above VWAP,
        Signal.BEARISH if price crosses below VWAP,
        Signal.NEUTRAL otherwise.
    """
    if len(df) < 3:
        return Signal.NEUTRAL

    vwap = compute_vwap(df)
    close = df["close"]

    current_close = close.iloc[-1]
    prev_close = close.iloc[-2]
    current_vwap = vwap.iloc[-1]
    prev_vwap = vwap.iloc[-2]

    if pd.isna(current_vwap) or pd.isna(prev_vwap):
        return Signal.NEUTRAL

    if prev_close <= prev_vwap and current_close > current_vwap:
        return Signal.BULLISH
    elif prev_close >= prev_vwap and current_close < current_vwap:
        return Signal.BEARISH

    # Current position relative to VWAP
    if current_close > current_vwap:
        return Signal.BULLISH
    elif current_close < current_vwap:
        return Signal.BEARISH

    return Signal.NEUTRAL


def get_supertrend_signal(df: pd.DataFrame, period: int, multiplier: float) -> Signal:
    """Generate signal from SuperTrend direction.

    Args:
        df: OHLCV DataFrame.
        period: SuperTrend ATR period.
        multiplier: SuperTrend ATR multiplier.

    Returns:
        Signal.BULLISH if SuperTrend direction is up,
        Signal.BEARISH if SuperTrend direction is down,
        Signal.NEUTRAL otherwise.
    """
    if len(df) < period + 2:
        return Signal.NEUTRAL

    _, direction = compute_supertrend(df, period, multiplier)

    if direction.iloc[-1] == 1:
        return Signal.BULLISH
    elif direction.iloc[-1] == -1:
        return Signal.BEARISH

    return Signal.NEUTRAL


def aggregate_signals(signals: list[Signal], min_required: int) -> Signal:
    """Aggregate multiple signals into a final trading signal.

    Args:
        signals: List of individual indicator signals.
        min_required: Minimum number of agreeing signals for a trade.

    Returns:
        Signal.BULLISH if enough signals are bullish,
        Signal.BEARISH if enough signals are bearish,
        Signal.NEUTRAL otherwise.
    """
    bullish_count = sum(1 for s in signals if s == Signal.BULLISH)
    bearish_count = sum(1 for s in signals if s == Signal.BEARISH)

    if bullish_count >= min_required:
        return Signal.BULLISH
    elif bearish_count >= min_required:
        return Signal.BEARISH

    return Signal.NEUTRAL
