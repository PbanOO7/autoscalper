"""Configuration loader and validator for the autoscalper."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


@dataclass
class DhanConfig:
    """DHAN API credentials."""
    client_id: str = ""
    access_token: str = ""


@dataclass
class InstrumentConfig:
    """Instrument configuration for Nifty50."""
    underlying: str = "NIFTY"
    underlying_security_id: int = 13
    underlying_segment: str = "IDX_I"
    exchange_segment: str = "NSE_FNO"
    lot_size: int = 25


@dataclass
class StrategyConfig:
    """Scalping strategy parameters."""
    candle_timeframe: int = 1
    ema_fast_period: int = 9
    ema_slow_period: int = 21
    rsi_period: int = 14
    rsi_overbought: float = 70.0
    rsi_oversold: float = 30.0
    use_vwap: bool = True
    supertrend_period: int = 10
    supertrend_multiplier: float = 3.0
    min_signals_for_entry: int = 2
    strike_mode: str = "ATM"
    strike_offset: int = 0


@dataclass
class RiskConfig:
    """Risk management parameters."""
    stop_loss_percent: float = 20.0
    profit_target_percent: float = 30.0
    trailing_stop_enabled: bool = True
    trailing_stop_percent: float = 15.0
    max_lots_per_day: int = 2
    max_daily_loss: float = 5000.0
    max_daily_profit: float = 10000.0
    max_open_positions: int = 1


@dataclass
class OrderConfig:
    """Order placement configuration."""
    product_type: str = "INTRADAY"
    order_type: str = "MARKET"
    limit_buffer_percent: float = 0.5
    auto_square_off_time: str = "15:15"
    no_new_trade_after: str = "15:00"
    market_open_time: str = "09:20"


@dataclass
class MonitoringConfig:
    """Monitoring and logging configuration."""
    log_level: str = "INFO"
    log_file: str = "scalper.log"
    console_dashboard: bool = True
    dashboard_refresh_seconds: int = 2
    trade_log_file: str = "trades.csv"
    notify_on_trade: bool = True


@dataclass
class PaperTradingConfig:
    """Paper trading mode configuration."""
    enabled: bool = True
    initial_capital: float = 100000.0


@dataclass
class ScalperConfig:
    """Root configuration for the autoscalper."""
    dhan: DhanConfig = field(default_factory=DhanConfig)
    instrument: InstrumentConfig = field(default_factory=InstrumentConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    orders: OrderConfig = field(default_factory=OrderConfig)
    monitoring: MonitoringConfig = field(default_factory=MonitoringConfig)
    paper_trading: PaperTradingConfig = field(default_factory=PaperTradingConfig)


def _build_dataclass(cls: type, data: dict) -> object:
    """Build a dataclass from a dict, ignoring unknown keys."""
    if data is None:
        return cls()
    valid_keys = {f.name for f in cls.__dataclass_fields__.values()}
    filtered = {k: v for k, v in data.items() if k in valid_keys}
    return cls(**filtered)


def load_config(config_path: Optional[str] = None) -> ScalperConfig:
    """Load configuration from YAML file with environment variable overrides.

    Priority: ENV vars > config file > defaults

    Args:
        config_path: Path to the YAML configuration file.

    Returns:
        ScalperConfig instance with all parameters loaded.
    """
    if config_path is None:
        config_path = os.environ.get(
            "SCALPER_CONFIG_PATH",
            str(Path(__file__).parent.parent / "config.yaml"),
        )

    raw: dict = {}
    config_file = Path(config_path)
    if config_file.exists():
        with open(config_file, "r") as f:
            raw = yaml.safe_load(f) or {}

    dhan_cfg = _build_dataclass(DhanConfig, raw.get("dhan"))
    instrument_cfg = _build_dataclass(InstrumentConfig, raw.get("instrument"))
    strategy_cfg = _build_dataclass(StrategyConfig, raw.get("strategy"))
    risk_cfg = _build_dataclass(RiskConfig, raw.get("risk"))
    order_cfg = _build_dataclass(OrderConfig, raw.get("orders"))
    monitoring_cfg = _build_dataclass(MonitoringConfig, raw.get("monitoring"))
    paper_cfg = _build_dataclass(PaperTradingConfig, raw.get("paper_trading"))

    # Environment variable overrides for sensitive data
    if os.environ.get("DHAN_CLIENT_ID"):
        dhan_cfg.client_id = os.environ["DHAN_CLIENT_ID"]
    if os.environ.get("DHAN_ACCESS_TOKEN"):
        dhan_cfg.access_token = os.environ["DHAN_ACCESS_TOKEN"]

    # Allow env override for paper trading mode
    if os.environ.get("SCALPER_LIVE_MODE", "").lower() == "true":
        paper_cfg.enabled = False

    return ScalperConfig(
        dhan=dhan_cfg,
        instrument=instrument_cfg,
        strategy=strategy_cfg,
        risk=risk_cfg,
        orders=order_cfg,
        monitoring=monitoring_cfg,
        paper_trading=paper_cfg,
    )


def validate_config(config: ScalperConfig) -> list[str]:
    """Validate configuration and return list of issues.

    Args:
        config: The ScalperConfig to validate.

    Returns:
        List of validation error messages. Empty list means valid.
    """
    issues: list[str] = []

    if not config.paper_trading.enabled:
        if not config.dhan.client_id:
            issues.append("DHAN client_id is required for live trading")
        if not config.dhan.access_token:
            issues.append("DHAN access_token is required for live trading")

    if config.risk.max_lots_per_day < 1:
        issues.append("max_lots_per_day must be at least 1")

    if config.risk.stop_loss_percent <= 0:
        issues.append("stop_loss_percent must be positive")

    if config.risk.profit_target_percent <= 0:
        issues.append("profit_target_percent must be positive")

    if config.strategy.min_signals_for_entry < 1:
        issues.append("min_signals_for_entry must be at least 1")

    if config.strategy.ema_fast_period >= config.strategy.ema_slow_period:
        issues.append("ema_fast_period must be less than ema_slow_period")

    if config.instrument.lot_size < 1:
        issues.append("lot_size must be at least 1")

    valid_strikes = {"ATM", "ITM1", "ITM2", "OTM1", "OTM2"}
    if config.strategy.strike_mode not in valid_strikes:
        issues.append(f"strike_mode must be one of {valid_strikes}")

    return issues
