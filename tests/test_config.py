"""Tests for configuration module."""

import os
import tempfile

import yaml

from scalper.config import (
    ScalperConfig,
    load_config,
    validate_config,
)


class TestLoadConfig:
    def test_load_defaults(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump({}, f)
            f.flush()
            config = load_config(f.name)

        assert config.instrument.underlying == "NIFTY"
        assert config.risk.max_lots_per_day == 2
        assert config.paper_trading.enabled is True
        os.unlink(f.name)

    def test_load_custom_values(self):
        custom = {
            "risk": {"stop_loss_percent": 15.0, "max_lots_per_day": 3},
            "strategy": {"ema_fast_period": 5},
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(custom, f)
            f.flush()
            config = load_config(f.name)

        assert config.risk.stop_loss_percent == 15.0
        assert config.risk.max_lots_per_day == 3
        assert config.strategy.ema_fast_period == 5
        os.unlink(f.name)

    def test_env_override(self):
        os.environ["DHAN_CLIENT_ID"] = "test_id"
        os.environ["DHAN_ACCESS_TOKEN"] = "test_token"
        try:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
                yaml.dump({}, f)
                f.flush()
                config = load_config(f.name)

            assert config.dhan.client_id == "test_id"
            assert config.dhan.access_token == "test_token"
            os.unlink(f.name)
        finally:
            del os.environ["DHAN_CLIENT_ID"]
            del os.environ["DHAN_ACCESS_TOKEN"]

    def test_missing_file_uses_defaults(self):
        config = load_config("/nonexistent/path/config.yaml")
        assert config.instrument.underlying == "NIFTY"


class TestValidateConfig:
    def test_valid_config(self):
        config = ScalperConfig()
        issues = validate_config(config)
        assert len(issues) == 0

    def test_invalid_stop_loss(self):
        config = ScalperConfig()
        config.risk.stop_loss_percent = -5.0
        issues = validate_config(config)
        assert any("stop_loss_percent" in i for i in issues)

    def test_invalid_ema_periods(self):
        config = ScalperConfig()
        config.strategy.ema_fast_period = 25
        config.strategy.ema_slow_period = 10
        issues = validate_config(config)
        assert any("ema_fast_period" in i for i in issues)

    def test_live_mode_needs_credentials(self):
        config = ScalperConfig()
        config.paper_trading.enabled = False
        issues = validate_config(config)
        assert any("client_id" in i for i in issues)
        assert any("access_token" in i for i in issues)

    def test_invalid_strike_mode(self):
        config = ScalperConfig()
        config.strategy.strike_mode = "INVALID"
        issues = validate_config(config)
        assert any("strike_mode" in i for i in issues)
