"""
Unified Quant Fleet — System Health & Unit Test Suite
Verifies magic isolation, risk guards, config loading, and strategy instantiations.
"""

import os
import sys
from pathlib import Path
from datetime import datetime, timezone
import yaml
import numpy as np
import pandas as pd

if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Evict conflicting config module from sys.modules if imported by another subproject
if "config" in sys.modules and not hasattr(sys.modules["config"], "magic_registry"):
    del sys.modules["config"]

from core.audit_logger import setup_logger
# Direct test suite logs to test_suite.log to prevent polluting fleet.log
setup_logger(log_filename="test_suite.log")

from config.magic_registry import (
    MAGIC_BTC_8H, MAGIC_FX_D1, MAGIC_GOLD_SESS, MAGIC_USIDX_SESS, MAGIC_NOWTRADING_MTF,
    BOT_TO_MAGIC, MAGIC_TO_BOT, get_magic, get_bot_by_magic
)
from core.fleet_risk_guard import FleetRiskGuard
from strategies.bot1_btc_8h import Btc8hStrategy
from strategies.bot2_fx_d1 import FxD1Strategy
from strategies.bot3_gold_sess import GoldSessStrategy
from strategies.bot4_usidx_sess import UsidxSessStrategy
from strategies.bot5_nowtrading_mtf import NowTradingStrategy
from core.mt5_connection_manager import MT5ConnectionManager


def test_magic_registry():
    print("👉 [1/5] Testing Magic Registry Isolation...")
    magics = [MAGIC_BTC_8H, MAGIC_FX_D1, MAGIC_GOLD_SESS, MAGIC_USIDX_SESS, MAGIC_NOWTRADING_MTF]
    assert len(magics) == len(set(magics)), "Duplicate Magic Numbers detected!"
    assert len(BOT_TO_MAGIC) == 5, f"Expected 5 bots in registry, found {len(BOT_TO_MAGIC)}"

    for bot, magic in BOT_TO_MAGIC.items():
        assert get_magic(bot) == magic
        assert get_bot_by_magic(magic) == bot
    print("   ✅ Magic Numbers are strictly unique and mapped correctly.")


def test_risk_guard():
    print("👉 [2/5] Testing Fleet Risk Guard...")
    guard = FleetRiskGuard(max_daily_drawdown_usd=100.0, min_margin_level_percent=200.0, max_total_positions=5)

    account_ok = {"equity": 1000.0, "margin": 100.0, "margin_level": 1000.0}
    guard.update_account_state(account_ok)

    # Normal state
    allowed, reason = guard.can_open_position(account_ok, current_position_count=2)
    assert allowed is True, f"Expected allowed, got: {reason}"

    # Position limit hit
    allowed, reason = guard.can_open_position(account_ok, current_position_count=5)
    assert allowed is False
    assert "Max fleet positions reached" in reason

    # Margin level too low
    account_low_margin = {"equity": 1000.0, "margin": 600.0, "margin_level": 166.0}
    allowed, reason = guard.can_open_position(account_low_margin, current_position_count=2)
    assert allowed is False
    assert "Margin level critical" in reason

    # Test Drawdown Ladder
    # Tier 0 (Normal): DD = 0% -> multiplier = 1.0, tier = NORMAL
    status = guard.get_status(account_ok)
    assert status["drawdown_tier"] == "NORMAL"
    assert status["risk_multiplier"] == 1.0

    # Reset guard for percentage drawdown ladder tests with high USD limit ($10,000)
    pct_guard = FleetRiskGuard(
        max_daily_drawdown_usd=10000.0,
        max_daily_drawdown_pct=0.075,
        warning_drawdown_pct=0.03,
        derisk_drawdown_pct=0.05,
    )
    peak_acc = {"equity": 10000.0, "margin": 100.0, "margin_level": 1000.0}
    pct_guard.update_account_state(peak_acc)

    # DD -3.5%: Tier 1 Warning -> multiplier = 1.0, allowed = True
    acc_tier1 = {"equity": 9650.0, "margin": 100.0, "margin_level": 1000.0}
    pct_guard.update_account_state(acc_tier1)
    status_t1 = pct_guard.get_status(acc_tier1)
    assert status_t1["drawdown_tier"] == "TIER_1_WARNING"
    assert status_t1["risk_multiplier"] == 1.0
    allowed, _ = pct_guard.can_open_position(acc_tier1, 2)
    assert allowed is True

    # DD -5.5%: Tier 2 De-risk -> multiplier = 0.5 (halve position sizes)
    acc_tier2 = {"equity": 9450.0, "margin": 100.0, "margin_level": 1000.0}
    pct_guard.update_account_state(acc_tier2)
    status_t2 = pct_guard.get_status(acc_tier2)
    assert status_t2["drawdown_tier"] == "TIER_2_DERISK"
    assert status_t2["risk_multiplier"] == 0.5

    # DD -8.0%: Tier 3 Kill-switch -> multiplier = 0.0, allowed = False
    acc_tier3 = {"equity": 9200.0, "margin": 100.0, "margin_level": 1000.0}
    pct_guard.update_account_state(acc_tier3)
    status_t3 = pct_guard.get_status(acc_tier3)
    allowed, reason = pct_guard.can_open_position(acc_tier3, 2)
    assert allowed is False
    assert status_t3["risk_multiplier"] == 0.0
    assert "kill limit" in reason.lower() or "drawdown" in reason.lower()

    # --- Test Daily PnL Circuit Breaker & Profit Lock ---
    daily_guard = FleetRiskGuard(
        max_daily_drawdown_usd=150.0,
        max_daily_loss_usd=150.0,
        max_daily_loss_pct=0.015,
        daily_profit_target_usd=150.0,
        daily_profit_target_pct=0.015,
        max_lot_gold=0.04,
        max_lot_btc=0.04,
        max_lot_fx=0.06,
    )
    base_acc = {"equity": 10000.0, "balance": 10000.0, "margin": 0.0, "margin_level": 1000.0}
    daily_guard.update_account_state(base_acc)

    # 1. Normal state ($0 PnL) -> Multiplier 1.0, Can open True
    assert daily_guard.get_risk_multiplier(base_acc) == 1.0
    ok, _ = daily_guard.can_open_position(base_acc, 1)
    assert ok is True

    # 2. Profit target lock: +$160 profit (+1.6% >= +1.5%) -> Multiplier 0.5 (lock gains), Can open True
    profit_acc = {"equity": 10160.0, "balance": 10000.0, "margin": 100.0, "margin_level": 1000.0}
    daily_guard.update_account_state(profit_acc)
    assert daily_guard.get_risk_multiplier(profit_acc) == 0.5
    status_profit = daily_guard.get_status(profit_acc)
    assert status_profit["drawdown_tier"] == "PROFIT_LOCKED_0.5X"

    # 3. Daily loss circuit breaker: -$160 loss (-1.6% <= -1.5%) -> Multiplier 0.0, Can open False
    loss_guard = FleetRiskGuard(
        max_daily_drawdown_usd=150.0,
        max_daily_loss_usd=150.0,
        max_daily_loss_pct=0.015,
        daily_profit_target_usd=150.0,
        daily_profit_target_pct=0.015,
    )
    loss_guard.update_account_state(base_acc)
    loss_acc = {"equity": 9840.0, "balance": 10000.0, "margin": 100.0, "margin_level": 1000.0}
    loss_guard.update_account_state(loss_acc)
    assert loss_guard.get_risk_multiplier(loss_acc) == 0.0
    allowed, veto_reason = loss_guard.can_open_position(loss_acc, 1)
    assert allowed is False
    assert "limit" in veto_reason.lower()

    # 4. Hard lot cap verification
    assert daily_guard.get_max_lot("XAUUSDm") == 0.04
    assert daily_guard.get_max_lot("BTCUSDm") == 0.04
    assert daily_guard.get_max_lot("EURUSDm") == 0.06

    print("   ✅ Risk Guard circuit breakers, Profit Lock (0.5x), and Hard Caps verified on all test vectors.")


def test_config_and_strategies():
    print("👉 [3/5] Testing Configuration & Strategy Instantiation...")
    cfg_path = _ROOT / "config" / "fleet_config.yaml"
    assert cfg_path.exists(), "fleet_config.yaml missing!"

    with open(cfg_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    bots_cfg = config.get("bots", {})
    s1 = Btc8hStrategy(bots_cfg.get("btc_8h", {}))
    s2 = FxD1Strategy(bots_cfg.get("fx_d1", {}))
    s3 = GoldSessStrategy(bots_cfg.get("gold_sess", {}))
    s4 = UsidxSessStrategy(bots_cfg.get("usidx_sess", {}))
    s5 = NowTradingStrategy(bots_cfg.get("nowtrading_mtf", {}))

    assert s1.magic == MAGIC_BTC_8H
    assert s2.magic == MAGIC_FX_D1
    assert s3.magic == MAGIC_GOLD_SESS
    assert s4.magic == MAGIC_USIDX_SESS
    assert s5.magic == MAGIC_NOWTRADING_MTF
    print("   ✅ All 5 strategies instantiated cleanly with assigned configs.")


def test_cadence_logic():
    print("👉 [4/5] Testing Cadence & Session Logic...")
    cfg_path = _ROOT / "config" / "fleet_config.yaml"
    with open(cfg_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    bots_cfg = config.get("bots", {})

    s1 = Btc8hStrategy({**bots_cfg.get("btc_8h", {}), "enabled": True})
    s3 = GoldSessStrategy({**bots_cfg.get("gold_sess", {}), "enabled": True})
    s5 = NowTradingStrategy({**bots_cfg.get("nowtrading_mtf", {}), "enabled": True})

    # At 08:02 UTC on Wednesday:
    test_dt = datetime(2026, 9, 16, 8, 2, tzinfo=timezone.utc)
    assert s1.should_run_cycle(test_dt) is True, "BTC 8H should fire at 08:00 UTC"
    assert s3.should_run_cycle(test_dt) is True, "Gold London Session should fire at 08:00 UTC"
    assert s5.should_run_cycle(test_dt) is True, "NowTrading hourly gate should fire at HH:02 UTC"

    # At 11:35 UTC:
    test_dt_off = datetime(2026, 9, 16, 11, 35, tzinfo=timezone.utc)
    assert s1.should_run_cycle(test_dt_off) is False
    assert s3.should_run_cycle(test_dt_off) is False
    assert s5.should_run_cycle(test_dt_off) is False
    print("   ✅ Strategy schedule gates trigger on exact hours and ignore off-hours.")


def test_carver_portfolio_engine():
    print("👉 [5/6] Testing Carver Systematic Portfolio Engine (pysystemtrade)...")
    from core.carver_portfolio_engine import CarverPortfolioEngine
    import numpy as np

    carver = CarverPortfolioEngine(annual_target_vol_pct=16.0, default_idm=1.40, buffer_fraction=0.10)
    equity = 10000.0

    # 1. Daily cash vol target
    # 16% / 16 = 1% daily = $100 daily vol target on $10k
    # With gold_sess weight 0.25 and IDM 1.40: 100 * 0.25 * 1.40 = $35.0
    cash_vol = carver.get_cash_vol_target(equity, "gold_sess")
    assert abs(cash_vol - 35.0) < 1e-4, f"Expected 35.0 cash vol, got {cash_vol}"

    # 2. Position Sizing: Gold with ATR = 15.0 pts ($15.00/oz), contract_size = 100
    # Dollar risk per unit = 15.0 * 100 = $1500 per lot
    # Target lot = $35.0 / 1500 = 0.0233 -> rounded 0.02 lot
    lot, meta = carver.calculate_position_size(
        equity=equity,
        strategy_name="gold_sess",
        atr_price=15.0,
        contract_size=100.0,
        forecast=10.0,
        min_lot=0.01,
        max_lot=2.0
    )
    assert lot == 0.02, f"Expected 0.02 lot, got {lot}"

    # 3. Position Buffering (pysystemtrade inert trade buffer)
    # If target is 0.021 and current is 0.02, diff is 0.001 <= 0.10 * 0.02 -> no churn, keep 0.02
    buffered = carver.apply_trade_buffer(current_lot=0.02, target_lot=0.021)
    assert buffered == 0.02, f"Buffer should retain current 0.02, got {buffered}"

    # 4. IDM calculation from uncorrelated matrix
    corr = np.eye(5)
    idm = carver.compute_idm_from_correlation(corr)
    # For 5 equal uncorrelated assets: 1 / sqrt(5 * 0.04) = 1 / sqrt(0.2) = sqrt(5) = 2.236
    assert abs(idm - np.sqrt(5)) < 1e-3, f"Expected sqrt(5), got {idm}"

    # 5. BaseStrategy integration test with carver_systematic sizing
    guard = FleetRiskGuard(max_daily_drawdown_usd=300.0, annual_target_vol_pct=16.0, idm=1.40)
    guard.update_account_state({"equity": 10000.0, "margin": 0.0, "margin_level": 1000.0})
    s = GoldSessStrategy({"enabled": True, "sizing_mode": "carver_systematic"})
    order_vol = s.calculate_order_volume(
        base_volume=0.05,
        risk_guard=guard,
        account_info={"equity": 10000.0},
        sl_distance=15.0,
        contract_size=100.0,
        sizing_mode="carver_systematic"
    )
    assert order_vol == 0.02, f"BaseStrategy carver volume expected 0.02, got {order_vol}"
    print("   ✅ Carver Volatility Targeting, IDM, and Buffering verified accurately.")


def test_correlation_risk_manager():
    print("👉 [6/7] Testing Portfolio Correlation Gatekeeper & USD Exposure...")
    from core.correlation_risk_manager import CorrelationRiskManager
    gatekeeper = CorrelationRiskManager(max_net_usd_units=2)

    # 1. Test canonical symbol resolution
    assert gatekeeper.get_canonical_symbol("EURUSDm") == "EURUSD"
    assert gatekeeper.get_canonical_symbol("XAUUSD.pro") == "XAUUSD"

    # 2. Test net USD exposure calculation
    pos = [
        {"symbol": "EURUSDm", "type": "BUY"},   # -1 USD
        {"symbol": "XAUUSDm", "type": "BUY"},   # -1 USD
    ]
    net_usd = gatekeeper.calculate_net_usd_exposure(pos)
    assert net_usd == -2.0, f"Expected net USD -2.0, got {net_usd}"

    # 3. Test blocking over-concentrated order
    # Selling USDJPY is Short USD (-1 USD) -> New net = -3 USD -> VIOLATES max 2
    allowed, reason = gatekeeper.can_execute_order("USDJPYm", "SELL", pos)
    assert allowed is False, "Expected USDJPY SELL to be blocked"
    assert "USD exposure limit exceeded" in reason

    # 4. Test allowing mitigating order
    # Buying USDJPY is Long USD (+1 USD) -> New net = -1 USD -> ALLOWED
    allowed, reason = gatekeeper.can_execute_order("USDJPYm", "BUY", pos)
    assert allowed is True, f"Expected USDJPY BUY to be allowed, got: {reason}"

    # 5. Test independent crypto asset (BTCUSD = 0 impact)
    allowed, _ = gatekeeper.can_execute_order("BTCUSDm", "BUY", pos)
    assert allowed is True, "Expected BTCUSD BUY to be allowed"

    # 6. Test integration into FleetRiskGuard
    guard = FleetRiskGuard()
    guard.set_correlation_manager(gatekeeper)
    acc = {"equity": 10000.0, "margin": 100.0, "margin_level": 1000.0}
    ok, veto_msg = guard.can_open_position(acc, 2, open_positions=pos, symbol="USDJPYm", signal="SELL")
    assert ok is False, "Expected FleetRiskGuard to veto via correlation gatekeeper"
    assert "USD exposure limit exceeded" in veto_msg

    print("   ✅ Portfolio Correlation Gatekeeper & USD Exposure constraints verified.")


def test_dynamic_covariance_risk():
    print("👉 [7/8] Testing Ledoit-Wolf Shrinkage Dynamic Covariance Engine...")
    from core.dynamic_covariance_risk import DynamicCovarianceRiskManager
    mgr = MT5ConnectionManager()
    mgr.connect()
    dyn = DynamicCovarianceRiskManager(mt5_mgr=mgr, max_pairwise_corr=0.75, max_portfolio_vol=0.035)

    # 1. Test clean_symbol
    assert dyn.clean_symbol("XAUUSD") == "XAUUSDm"
    assert dyn.clean_symbol("EURUSD.pro") == "EURUSDm"

    # 2. Test live estimation if MT5 connected
    if mgr.connected:
        ok = dyn.update_covariance_matrix()
        assert ok is True, "Expected Ledoit-Wolf estimation to succeed with live MT5 rates"
        assert dyn.cov_matrix is not None
        assert dyn.shrinkage_intensity >= 0.0
        corr = dyn.get_correlation_matrix()
        assert corr is not None
        assert corr.shape[0] >= 5

    # 3. Test Pairwise Correlation Veto logic with calibrated mock matrix
    mock_syms = ["EURUSDm", "GBPUSDm", "USDJPYm"]
    dyn.cov_matrix = pd.DataFrame(
        [[0.0001, 0.000085, -0.00002],
         [0.000085, 0.0001, -0.00002],
         [-0.00002, -0.00002, 0.0001]],
        index=mock_syms, columns=mock_syms
    )
    dyn.corr_matrix = pd.DataFrame(
        [[1.0, 0.85, -0.20],
         [0.85, 1.0, -0.20],
         [-0.20, -0.20, 1.0]],
        index=mock_syms, columns=mock_syms
    )
    dyn.last_update = datetime.now(timezone.utc)

    # Buying EURUSD when already BUY GBPUSD (corr = +0.85 > 0.75) -> VETOED
    allowed, reason = dyn.can_execute_order("EURUSDm", "BUY", [{"symbol": "GBPUSDm", "type": "BUY"}])
    assert allowed is False, f"Expected pairwise correlation veto, got: {reason}"
    assert "Pairwise High Positive Correlation" in reason

    # Buying EURUSD when holding SELL GBPUSD (opposite directions) -> ALLOWED
    allowed, _ = dyn.can_execute_order("EURUSDm", "BUY", [{"symbol": "GBPUSDm", "type": "SELL"}])
    assert allowed is True

    # 4. Test integration into FleetRiskGuard
    guard = FleetRiskGuard()
    guard.set_dynamic_covariance_manager(dyn)
    acc = {"equity": 10000.0, "margin": 100.0, "margin_level": 1000.0}
    ok, veto_msg = guard.can_open_position(acc, 1, open_positions=[{"symbol": "GBPUSDm", "type": "BUY"}], symbol="EURUSDm", signal="BUY")
    assert ok is False, "Expected FleetRiskGuard to veto via Ledoit-Wolf dynamic correlation"
    assert "Pairwise High Positive Correlation" in veto_msg

    print("   ✅ Dynamic Ledoit-Wolf Covariance, Shrinkage delta*, and Pairwise Veto verified.")


def test_market_cadence_and_watcher():
    print("👉 [8/9] Testing Market Cadence, Holiday Manager, and Hot Reload Watcher...")
    from core.market_cadence_manager import MarketCadenceManager
    from run_unified_bot import get_codebase_fingerprint

    cadence = MarketCadenceManager()

    # 1. Test Crypto 24/7/365
    assert cadence.is_crypto_symbol("BTCUSDm") is True
    assert cadence.is_crypto_symbol("ETHUSDm") is True
    assert cadence.is_crypto_symbol("EURUSDm") is False

    # 2. Test Weekend Detection
    sat = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)
    is_open, reason = cadence.is_symbol_trading_open("EURUSDm", dt_utc=sat)
    assert is_open is False, "Expected EURUSD to be closed on Saturday"
    assert "Saturday" in reason

    btc_open, _ = cadence.is_symbol_trading_open("BTCUSDm", dt_utc=sat)
    assert btc_open is True, "Expected BTCUSD to be open 24/7 on Saturday"

    # 3. Test Holiday Detection
    xmas = datetime(2026, 12, 25, 14, 0, tzinfo=timezone.utc)
    is_open, reason = cadence.is_symbol_trading_open("XAUUSDm", dt_utc=xmas)
    assert is_open is False, "Expected XAUUSD to be closed on Christmas"
    assert "Christmas" in reason

    # 4. Test Strategy Session Cadence
    btc_active, _ = cadence.is_strategy_active_in_session("BTC_8H", dt_utc=sat)
    fx_active, _ = cadence.is_strategy_active_in_session("FX_D1", dt_utc=sat)
    assert btc_active is True, "BTC_8H must stay active on weekends"
    assert fx_active is False, "FX_D1 must idle on weekends"

    # 5. Test Adaptive Sleep Interval
    interval_wknd = cadence.get_adaptive_loop_interval(dt_utc=sat, base_interval=15, idle_interval=60)
    assert interval_wknd == 60, f"Expected 60s idle loop on weekend, got {interval_wknd}"

    # 6. Test Codebase Fingerprint
    fp = get_codebase_fingerprint()
    assert fp > 0.0, "Expected valid codebase modification timestamp"

    print("   ✅ 24/7 Crypto, Weekend/Holiday idle cadence, and Code Watcher verified.")


def test_mt5_status():
    print("👉 [9/9] Testing MT5 Connection Subsystem...")
    mgr = MT5ConnectionManager()
    ok = mgr.connect()
    if ok:
        info = mgr.get_account_info()
        print(f"   ✅ Live MT5 connection verified: Account {info.get('login')} (Equity: ${info.get('equity'):,.2f})")
    else:
        print("   ℹ️ MT5 Terminal is currently not opened or terminal path needs launch. Connector handles offline mode safely.")


if __name__ == "__main__":
    print("\n==================================================")
    print("   UNIFIED QUANT FLEET - AUTOMATED TEST SUITE     ")
    print("==================================================")
    test_magic_registry()
    test_risk_guard()
    test_config_and_strategies()
    test_cadence_logic()
    test_carver_portfolio_engine()
    test_correlation_risk_manager()
    test_dynamic_covariance_risk()
    test_market_cadence_and_watcher()
    test_mt5_status()
    print("==================================================")
    print("🎉 ALL 9 TESTS PASSED SUCCESSFULLY!")
    print("==================================================\n")

