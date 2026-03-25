"""Tests for risk management module."""

import pytest

from scalper.config import RiskConfig
from scalper.risk_manager import ExitReason, RiskManager, TradeRecord


@pytest.fixture
def risk_config():
    return RiskConfig(
        stop_loss_percent=20.0,
        profit_target_percent=30.0,
        trailing_stop_enabled=True,
        trailing_stop_percent=15.0,
        max_lots_per_day=2,
        max_daily_loss=5000.0,
        max_daily_profit=10000.0,
        max_open_positions=1,
    )


@pytest.fixture
def risk_manager(risk_config):
    return RiskManager(risk_config, lot_size=25)


class TestRiskManager:
    def test_can_take_first_trade(self, risk_manager):
        allowed, reason = risk_manager.can_take_new_trade()
        assert allowed
        assert reason == "OK"

    def test_register_trade(self, risk_manager):
        trade = risk_manager.register_trade(
            option_type="CE",
            strike=23500.0,
            security_id=12345,
            entry_price=150.0,
            lots=1,
        )
        assert trade.is_open
        assert trade.quantity == 25
        assert trade.entry_price == 150.0

    def test_max_lots_limit(self, risk_manager):
        risk_manager.register_trade("CE", 23500.0, 12345, 150.0, lots=1)
        risk_manager.trades[-1].is_open = False  # close it
        risk_manager.register_trade("PE", 23400.0, 12346, 100.0, lots=1)
        risk_manager.trades[-1].is_open = False

        allowed, reason = risk_manager.can_take_new_trade()
        assert not allowed
        assert "lot limit" in reason.lower()

    def test_max_open_positions(self, risk_manager):
        risk_manager.register_trade("CE", 23500.0, 12345, 150.0, lots=1)
        # Position is still open
        allowed, reason = risk_manager.can_take_new_trade()
        assert not allowed
        assert "open positions" in reason.lower()

    def test_stop_loss_trigger(self, risk_manager):
        trade = risk_manager.register_trade("CE", 23500.0, 12345, 100.0, lots=1)
        # Price drops 25% -> should trigger 20% SL
        should_exit, reason = risk_manager.check_exit_conditions(trade, 75.0)
        assert should_exit
        assert reason == ExitReason.STOP_LOSS

    def test_profit_target_trigger(self, risk_manager):
        trade = risk_manager.register_trade("CE", 23500.0, 12345, 100.0, lots=1)
        # Price rises 35% -> should trigger 30% target
        should_exit, reason = risk_manager.check_exit_conditions(trade, 135.0)
        assert should_exit
        assert reason == ExitReason.PROFIT_TARGET

    def test_trailing_stop(self, risk_manager):
        trade = risk_manager.register_trade("CE", 23500.0, 12345, 100.0, lots=1)
        # Price goes up first
        trade.update_peak(120.0)
        # Then drops - trailing SL at 120 * 0.85 = 102
        should_exit, reason = risk_manager.check_exit_conditions(trade, 101.0)
        assert should_exit
        assert reason == ExitReason.TRAILING_STOP

    def test_no_exit_normal_price(self, risk_manager):
        trade = risk_manager.register_trade("CE", 23500.0, 12345, 100.0, lots=1)
        should_exit, reason = risk_manager.check_exit_conditions(trade, 110.0)
        assert not should_exit
        assert reason is None

    def test_close_trade_updates_stats(self, risk_manager):
        trade = risk_manager.register_trade("CE", 23500.0, 12345, 100.0, lots=1)
        pnl = risk_manager.close_trade(trade, 130.0, ExitReason.PROFIT_TARGET)
        assert pnl == (130.0 - 100.0) * 25
        assert not trade.is_open
        assert risk_manager.daily_stats.winning_trades == 1

    def test_daily_summary(self, risk_manager):
        trade = risk_manager.register_trade("CE", 23500.0, 12345, 100.0, lots=1)
        risk_manager.close_trade(trade, 130.0, ExitReason.PROFIT_TARGET)

        summary = risk_manager.get_daily_summary()
        assert summary["total_trades"] == 1
        assert summary["realized_pnl"] == 750.0
        assert summary["winning_trades"] == 1


class TestTradeRecord:
    def test_calculate_pnl(self):
        trade = TradeRecord(
            trade_id="test-001",
            entry_time=None,
            option_type="CE",
            strike=23500.0,
            security_id=12345,
            entry_price=100.0,
            quantity=25,
            lots=1,
        )
        assert trade.calculate_pnl(120.0) == 500.0
        assert trade.calculate_pnl(80.0) == -500.0

    def test_update_peak(self):
        trade = TradeRecord(
            trade_id="test-002",
            entry_time=None,
            option_type="PE",
            strike=23400.0,
            security_id=12346,
            entry_price=80.0,
            quantity=25,
            lots=1,
            peak_price=80.0,
        )
        trade.update_peak(100.0)
        assert trade.peak_price == 100.0
        trade.update_peak(90.0)
        assert trade.peak_price == 100.0  # Should not decrease
