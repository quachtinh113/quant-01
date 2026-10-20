"""
=====================================================================
UNIFIED QUANT FLEET — MASTER ORCHESTRATOR
1 PC running 5 Quant Bot Strategies simultaneously on MetaTrader 5:
  1. exness_btc_8h       (Magic: 260904) - Bitcoin 8-Hour
  2. exness_fx_d1        (Magic: 260901) - FX Majors Daily Close
  3. exness_gold_sess    (Magic: 260903) - Gold & Silver Session (London/NY)
  4. exness_usidx_sess   (Magic: 260902) - US Indices Session (US500/USTEC)
  5. nowtrading_mtf      (Magic: 260905) - NowTrading 2.1 Multi-Timeframe DCA
=====================================================================
"""

import os
import sys
import time
import gc
import signal
import threading
from pathlib import Path
from datetime import datetime, timezone
import yaml
from dotenv import load_dotenv

# Ensure root is in sys.path
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

from core.audit_logger import setup_logger, get_logger
from core.mt5_connection_manager import MT5ConnectionManager, MAX_ALLOWED_SPREAD_POINTS
from core.fleet_risk_guard import FleetRiskGuard
from core.correlation_risk_manager import CorrelationRiskManager
from core.dynamic_covariance_risk import DynamicCovarianceRiskManager
from core.market_cadence_manager import MarketCadenceManager
from strategies.bot1_btc_8h import Btc8hStrategy
from strategies.bot2_fx_d1 import FxD1Strategy
from strategies.bot3_gold_sess import GoldSessStrategy
from strategies.bot4_usidx_sess import UsidxSessStrategy
from strategies.bot5_nowtrading_mtf import NowTradingStrategy

# Load environment & config
load_dotenv(_ROOT / ".env")
CONFIG_PATH = _ROOT / "config" / "fleet_config.yaml"

with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    config = yaml.safe_load(f)

system_cfg = config.get("system", {})
risk_cfg = config.get("account_risk_guard", {})
bots_cfg = config.get("bots", {})

logger = setup_logger(
    log_dir=str(_ROOT / system_cfg.get("log_dir", "logs")),
    log_level=system_cfg.get("log_level", "INFO")
)

import ctypes

# Graceful termination
_running = True


def prevent_windows_sleep():
    """Prevent Windows from entering sleep or hibernation while the bot is active."""
    try:
        if os.name == 'nt' and hasattr(ctypes, 'windll'):
            ES_CONTINUOUS = 0x80000000
            ES_SYSTEM_REQUIRED = 0x00000001
            ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
            logger.info("🛡️ Power Management: Windows Sleep Prevention ACTIVE (System will stay awake).")
    except Exception as e:
        logger.warning(f"Could not enable sleep prevention: {e}")


def restore_windows_sleep():
    """Restore normal Windows power management on clean exit."""
    try:
        if os.name == 'nt' and hasattr(ctypes, 'windll'):
            ES_CONTINUOUS = 0x80000000
            ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
    except Exception:
        pass


def optimize_memory():
    """Trigger Python garbage collection and trim Windows working set to minimize RAM footprint."""
    try:
        gc.collect()
        if os.name == 'nt' and hasattr(ctypes, 'windll') and hasattr(ctypes.windll, 'psapi'):
            ctypes.windll.psapi.EmptyWorkingSet(ctypes.windll.kernel32.GetCurrentProcess())
    except Exception:
        pass


def get_codebase_fingerprint(base_dir=None, watch_dirs=["core", "strategies", "config"], extensions=[".py", ".yaml"]) -> float:
    """Get the latest modification timestamp across all watched files in codebase."""
    if base_dir is None:
        base_dir = _ROOT
    latest_mtime = 0.0
    for root_file in ["run_unified_bot.py"]:
        p = Path(base_dir) / root_file
        if p.exists():
            try:
                latest_mtime = max(latest_mtime, p.stat().st_mtime)
            except OSError:
                pass
    for folder in watch_dirs:
        folder_path = Path(base_dir) / folder
        if not folder_path.exists():
            continue
        for ext in extensions:
            for p in folder_path.rglob(f"*{ext}"):
                try:
                    latest_mtime = max(latest_mtime, p.stat().st_mtime)
                except OSError:
                    pass
    return latest_mtime


def handle_shutdown(signum, frame):
    global _running
    logger.info("🛑 Shutdown signal received. Stopping Unified Quant Fleet safely...")
    _running = False
    restore_windows_sleep()


signal.signal(signal.SIGINT, handle_shutdown)
signal.signal(signal.SIGTERM, handle_shutdown)


class Watchdog(threading.Thread):
    """Monitors the main thread and alerts if stalled."""
    def __init__(self, timeout_seconds=180):
        super().__init__(daemon=True)
        self.timeout = timeout_seconds
        self.last_ping = time.time()
        self.running = True

    def ping(self):
        self.last_ping = time.time()

    def run(self):
        last_time = time.time()
        while self.running and _running:
            time.sleep(10)
            now = time.time()
            elapsed = now - last_time
            last_time = now

            # Detect sleep/hibernation
            if elapsed > 30.0:
                logger.info(f"⏰ System wake from sleep detected ({elapsed:.1f}s sleep). Resetting watchdog timer.")
                self.ping()
                continue

            if now - self.last_ping > self.timeout:
                logger.critical(f"💀 WATCHDOG ALERT: Main loop stalled for > {self.timeout}s! Reconnecting...")
                self.ping()


def print_fleet_dashboard(account_info, risk_status, bot_summaries):
    """Print an aesthetic ANSI dashboard to the console."""
    now_utc = datetime.now(timezone.utc)
    now_vn = datetime.now()  # Local Vietnam Time (UTC+7)
    time_str = f"{now_vn.strftime('%Y-%m-%d %H:%M:%S')} VN (UTC+7) | {now_utc.strftime('%H:%M:%S')} UTC"
    lines = [
        "",
        "==========================================================================================",
        f" 🛡️  UNIFIED QUANT FLEET DASHBOARD — 5 BOTS ON 1 PC | {time_str}",
        "==========================================================================================",
        f" Account: {account_info.get('login')} @ {account_info.get('server')} | "
        f"Balance: ${account_info.get('balance', 0):,.2f} | Equity: ${account_info.get('equity', 0):,.2f} | "
        f"Margin Level: {account_info.get('margin_level', 0):.1f}%",
        f" Daily Peak: ${risk_status.get('daily_peak_equity', 0):,.2f} | "
        f"DD: ${risk_status.get('current_drawdown_usd', 0):.2f} / Max ${risk_status.get('drawdown_limit_usd', 0):.2f} | "
        f"Daily PnL: ${risk_status.get('daily_pnl_usd', 0):+.2f} ({risk_status.get('daily_pnl_pct', 0):+.2f}%) | "
        f"Status: {'🔴 HALTED' if risk_status.get('is_halted') else ('🟡 0.5x DERISK/LOCK' if risk_status.get('risk_multiplier') == 0.5 else '🟢 ACTIVE')}",
        "------------------------------------------------------------------------------------------",
        f" {'Bot Name':<16} | {'Magic':<7} | {'Status':<8} | {'Open Pos':<8} | {'Unrealized P/L':<14} | {'Last Action'}",
        "------------------------------------------------------------------------------------------",
    ]

    total_positions = 0
    total_unrealized = 0.0

    for b in bot_summaries:
        status_str = "ENABLED" if b["enabled"] else "DISABLED"
        pnl_str = f"${b['unrealized_pnl']:+.2f}"
        total_positions += b["positions_count"]
        total_unrealized += b["unrealized_pnl"]
        lines.append(
            f" {b['name']:<16} | {b['magic']:<7} | {status_str:<8} | {b['positions_count']:<8} | {pnl_str:<14} | {b['last_action']}"
        )

    lines.append("------------------------------------------------------------------------------------------")
    lines.append(f" TOTAL OPEN POSITIONS: {total_positions} | TOTAL UNREALIZED P/L: ${total_unrealized:+.2f}")
    lines.append("==========================================================================================")
    lines.append("")

    print("\n".join(lines), flush=True)


def main():
    logger.info("=================================================================")
    logger.info("🚀 INITIALIZING UNIFIED QUANT FLEET (5 BOTS ON 1 PC)...")
    logger.info("=================================================================")

    # Prevent Windows from sleeping while bot is running
    prevent_windows_sleep()

    # 1. Connect MT5
    mt5_mgr = MT5ConnectionManager()
    if not mt5_mgr.connect():
        logger.critical("❌ Failed initial MT5 connection. Please ensure MT5 terminal is open/accessible.")
        # We still proceed into the loop to let auto-reconnect retry

    # 2. Risk Guard
    carver_cfg = config.get("carver_portfolio", {})
    risk_guard = FleetRiskGuard(
        max_daily_drawdown_usd=float(risk_cfg.get("max_daily_loss_usd", risk_cfg.get("max_daily_drawdown_usd", 150.0))),
        max_daily_drawdown_pct=float(risk_cfg.get("max_daily_loss_pct", 1.5)) / 100.0 if float(risk_cfg.get("max_daily_loss_pct", 1.5)) > 0.05 else float(risk_cfg.get("max_daily_loss_pct", 0.015)),
        max_daily_loss_usd=float(risk_cfg.get("max_daily_loss_usd", risk_cfg.get("max_daily_drawdown_usd", 150.0))),
        max_daily_loss_pct=float(risk_cfg.get("max_daily_loss_pct", 1.5)) / 100.0 if float(risk_cfg.get("max_daily_loss_pct", 1.5)) > 0.05 else float(risk_cfg.get("max_daily_loss_pct", 0.015)),
        daily_profit_target_pct=float(risk_cfg.get("daily_profit_target_pct", 1.5)) / 100.0 if float(risk_cfg.get("daily_profit_target_pct", 1.5)) > 0.05 else float(risk_cfg.get("daily_profit_target_pct", 0.015)),
        daily_profit_target_usd=float(risk_cfg.get("daily_profit_target_usd", 150.0)),
        max_total_open_risk_pct=float(risk_cfg.get("max_total_open_risk_pct", 2.0)) / 100.0 if float(risk_cfg.get("max_total_open_risk_pct", 2.0)) > 0.05 else float(risk_cfg.get("max_total_open_risk_pct", 0.02)),
        min_margin_level_percent=float(risk_cfg.get("min_margin_level_percent", 200.0)),
        max_total_positions=int(risk_cfg.get("max_total_open_positions", risk_cfg.get("max_total_positions", 10))),
        halt_file=str(_ROOT / "HALT_FLEET"),
        annual_target_vol_pct=float(carver_cfg.get("annual_target_vol_pct", 16.0)),
        idm=float(carver_cfg.get("idm", 1.42)),
        max_lot_gold=float(risk_cfg.get("max_lot_gold", 0.04)),
        max_lot_btc=float(risk_cfg.get("max_lot_btc", 0.04)),
        max_lot_fx=float(risk_cfg.get("max_lot_fx", 0.06)),
    )

    # 2b. Correlation & Exposure Gatekeeper (Max Net USD Exposure)
    max_net_usd = int(risk_cfg.get("max_net_usd_units", 2))
    correlation_manager = CorrelationRiskManager(max_net_usd_units=max_net_usd)
    risk_guard.set_correlation_manager(correlation_manager)
    mt5_mgr.set_correlation_manager(correlation_manager)

    # 2c. Dynamic Ledoit-Wolf Covariance Gatekeeper (Regime-Aware Risk)
    dyn_cov_cfg = risk_cfg.get("dynamic_covariance", {})
    if dyn_cov_cfg.get("enabled", True):
        dyn_cov_manager = DynamicCovarianceRiskManager(
            symbols=dyn_cov_cfg.get("symbols", None),
            timeframe=dyn_cov_cfg.get("timeframe", "H1"),
            lookback=int(dyn_cov_cfg.get("lookback", 90)),
            max_portfolio_vol=float(dyn_cov_cfg.get("max_portfolio_vol", 0.035)),
            max_pairwise_corr=float(dyn_cov_cfg.get("max_pairwise_corr", 0.75)),
            mt5_mgr=mt5_mgr
        )
        dyn_cov_manager.update_covariance_matrix()
        risk_guard.set_dynamic_covariance_manager(dyn_cov_manager)

    # 3. Instantiate the 5 Strategies
    strategies = [
        Btc8hStrategy(bots_cfg.get("btc_8h", {})),
        FxD1Strategy(bots_cfg.get("fx_d1", {})),
        GoldSessStrategy(bots_cfg.get("gold_sess", {})),
        UsidxSessStrategy(bots_cfg.get("usidx_sess", {})),
        NowTradingStrategy(bots_cfg.get("nowtrading_mtf", {})),
    ]

    logger.info(f"✨ Successfully loaded {len(strategies)} strategy engines:")
    for s in strategies:
        logger.info(f"   - {s.name:<16} (Magic: {s.magic}, Enabled: {s.enabled})")
    logger.info(f"🛡️ Execution Layer: Max Spread Filter ACTIVE ({len(MAX_ALLOWED_SPREAD_POINTS)} rules configured).")

    # 4. Market Cadence & Session Manager
    cadence_mgr = MarketCadenceManager()

    # 4b. Watchdog Thread & Code Watcher
    watchdog = Watchdog(timeout_seconds=180)
    watchdog.start()

    initial_fingerprint = get_codebase_fingerprint()
    last_watcher_check = time.time()
    logger.info("👀 Code Watcher ACTIVE: Auto-reload on file edits (core/, strategies/, config/).")

    loop_interval = system_cfg.get("loop_interval_seconds", 15)
    last_dashboard_time = 0.0
    last_gc_time = 0.0

    logger.info(f"🔄 Starting Master Execution Loop (interval = {loop_interval}s)...")

    # 5. Master Execution Loop
    while _running:
        try:
            watchdog.ping()
            now_utc = datetime.now(timezone.utc)
            now_ts = time.time()

            # Hot-Reload Watcher: Check if any code files were updated
            if now_ts - last_watcher_check >= 15.0:
                last_watcher_check = now_ts
                current_fp = get_codebase_fingerprint()
                if current_fp > initial_fingerprint:
                    logger.info("⚡ [WATCHDOG] Code modification detected! Initiating graceful reload (exit code 42)...")
                    watchdog.running = False
                    mt5_mgr.shutdown()
                    restore_windows_sleep()
                    sys.exit(42)

            # Ensure MT5 is online
            if not mt5_mgr.ensure_connected():
                logger.warning("⏳ Waiting for MT5 connection...")
                time.sleep(5)
                continue

            account_info = mt5_mgr.get_account_info()
            if not account_info:
                time.sleep(5)
                continue

            # Update Risk Engine
            risk_guard.update_account_state(account_info)
            risk_status = risk_guard.get_status(account_info)

            # Evaluate each bot
            cycle_executed = False
            for strat in strategies:
                if not strat.enabled:
                    continue

                # Session & Holiday Cadence Check:
                # BTC_8H runs 24/7/365; Forex / Metals idle when market is closed
                is_open, open_reason = cadence_mgr.is_strategy_active_in_session(strat.name, now_utc, mt5_mgr)
                if not is_open:
                    strat.last_action = f"IDLE_MARKET_CLOSED ({open_reason})"
                    continue

                # 1. Real-time tick hook (Take profit, time decay, basket checks)
                strat.on_tick_update(now_utc, mt5_mgr, risk_guard, account_info)

                # 2. Scheduled cycle check (Bar close / session window)
                if strat.should_run_cycle(now_utc):
                    strat.evaluate_cycle(now_utc, mt5_mgr, risk_guard, account_info)
                    cycle_executed = True
                else:
                    # Sync open positions periodically
                    strat.sync_positions(mt5_mgr)

            now_ts = time.time()

            # Dashboard refresh every 60 seconds
            if now_ts - last_dashboard_time >= 60.0:
                last_dashboard_time = now_ts
                bot_summaries = [s.get_summary() for s in strategies]
                print_fleet_dashboard(account_info, risk_status, bot_summaries)

            # RAM & Performance Hygiene: Clean garbage & trim working set after cycles or every 60s
            if cycle_executed or (now_ts - last_gc_time >= 60.0):
                last_gc_time = now_ts
                optimize_memory()

            # Adaptive sleep: 15s active, 60s idle when traditional markets are closed
            adaptive_sleep = cadence_mgr.get_adaptive_loop_interval(
                dt_utc=now_utc,
                base_interval=loop_interval,
                idle_interval=int(system_cfg.get("weekend_loop_interval_seconds", 60))
            )
            time.sleep(adaptive_sleep)

        except Exception as e:
            logger.error(f"⚠️ Unhandled error in Master Loop: {e}", exc_info=True)
            time.sleep(5)

    # Clean shutdown
    logger.info("🔌 Shutting down Unified Quant Fleet...")
    watchdog.running = False
    mt5_mgr.shutdown()
    restore_windows_sleep()
    logger.info("👋 Unified Quant Fleet stopped cleanly. Goodbye!")


if __name__ == "__main__":
    main()
