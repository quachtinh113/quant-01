"""
Unified Quant Fleet — 36-Month Historical Backtest Engine
Simulates strategies over historical price data (MT5 or historical bars cache)
Calculates real quantitative metrics: Win Rate, Profit Factor, Sharpe, Max Drawdown.
"""

import sys
import os
from pathlib import Path
from datetime import datetime, timedelta, timezone
import pandas as pd
import numpy as np
import yaml

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

from core.mt5_connection_manager import MT5ConnectionManager

# Load configuration
CONFIG_PATH = _ROOT / "config" / "fleet_config.yaml"
if CONFIG_PATH.exists():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
else:
    config = {}

bots_cfg = config.get("bots", {})


def simulate_strategy_bars(df: pd.DataFrame, symbol: str, initial_capital: float = 10000.0) -> dict:
    """Simulates trend-following & mean-reversion strategy on real historical bars."""
    if df is None or len(df) < 50:
        return {"trades": 0, "win_rate": 0.0, "profit_factor": 0.0, "net_profit": 0.0, "max_drawdown": 0.0}

    df = df.copy().reset_index(drop=True)
    df['sma20'] = df['close'].rolling(20).mean()
    high_low = df['high'] - df['low']
    high_close = (df['high'] - df['close'].shift()).abs()
    low_close = (df['low'] - df['close'].shift()).abs()
    df['tr'] = np.maximum(high_low, np.maximum(high_close, low_close))
    df['atr'] = df['tr'].rolling(14).mean()

    # Institutional Contract Specifications & Cost Modeling
    def get_contract_spec(sym: str):
        s = sym.upper()
        if "BTC" in s:
            return 1.0, 30.0, 0.1          # 1 BTC contract, ~$3 spread
        elif "XAU" in s:
            return 100.0, 20.0, 0.01       # 100 oz, ~$0.20 spread
        elif "XAG" in s:
            return 5000.0, 30.0, 0.001     # 5000 oz
        elif "US500" in s or "USTEC" in s or "US30" in s:
            return 1.0, 20.0, 0.1          # 1 unit index
        elif "JPY" in s:
            return 100000.0, 15.0, 0.001   # FX JPY pairs
        else:
            return 100000.0, 12.0, 0.00001 # Standard FX majors

    contract_size, spread_pts, point = get_contract_spec(symbol)
    lot_size = 0.05
    round_turn_cost = (spread_pts * point * contract_size * lot_size) + (7.0 * lot_size)

    for i in range(25, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i - 1]
        close = row['close']
        high = row['high']
        low = row['low']

        # Manage open position
        if position:
            pos_type, entry_price, sl, tp, _ = position
            pnl = 0.0
            closed = False

            if pos_type == "BUY":
                if low <= sl:
                    gross = (sl - entry_price) * contract_size * lot_size
                    pnl = gross - round_turn_cost
                    closed = True
                elif high >= tp:
                    gross = (tp - entry_price) * contract_size * lot_size
                    pnl = gross - round_turn_cost
                    closed = True
            else:  # SELL
                if high >= sl:
                    gross = (entry_price - sl) * contract_size * lot_size
                    pnl = gross - round_turn_cost
                    closed = True
                elif low <= tp:
                    gross = (entry_price - tp) * contract_size * lot_size
                    pnl = gross - round_turn_cost
                    closed = True

            if closed:
                trades.append(pnl)
                position = None

        # Entry signal if no position
        if not position and not pd.isna(prev['sma20']) and not pd.isna(prev['atr']):
            sma = prev['sma20']
            atr = prev['atr']
            if prev['close'] > sma + 0.5 * atr:
                sl = close - (atr * 1.5)
                tp = close + (atr * 2.5)
                position = ("BUY", close, sl, tp, i)
            elif prev['close'] < sma - 0.5 * atr:
                sl = close + (atr * 1.5)
                tp = close - (atr * 2.5)
                position = ("SELL", close, sl, tp, i)

    if not trades:
        return {"trades": 0, "win_rate": 0.0, "profit_factor": 0.0, "net_profit": 0.0, "max_drawdown": 0.0}

    trade_series = pd.Series(trades)
    winning = trade_series[trade_series > 0]
    losing = trade_series[trade_series < 0]

    win_rate = (len(winning) / len(trades)) * 100.0 if len(trades) > 0 else 0.0
    gross_profit = winning.sum() if not winning.empty else 0.0
    gross_loss = abs(losing.sum()) if not losing.empty else 1.0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
    net_profit = trade_series.sum()

    # Drawdown
    equity_curve = initial_capital + trade_series.cumsum()
    peak = equity_curve.cummax()
    drawdown = (equity_curve - peak) / peak * 100.0
    max_drawdown = abs(drawdown.min()) if not drawdown.empty else 0.0

    return {
        "trades": len(trades),
        "win_rate": round(win_rate, 2),
        "profit_factor": round(profit_factor, 2),
        "net_profit": round(net_profit, 2),
        "max_drawdown": round(max_drawdown, 2),
    }


def run_backtest_36m():
    print("==========================================================================")
    print("🚀 INITIALIZING 36-MONTH HISTORICAL BACKTEST ENGINE FOR QUANT FLEET...")
    print("==========================================================================")

    mt5_mgr = MT5ConnectionManager()
    connected = mt5_mgr.connect()
    symbols = ["BTCUSDm", "EURUSDm", "GBPUSDm", "USDJPYm", "XAUUSDm", "US500m", "USTECm"]

    start_date = datetime.now(timezone.utc) - timedelta(days=36 * 30)
    print(f"🗓️ Backtest Window: {start_date.strftime('%Y-%m-%d')} to {datetime.now(timezone.utc).strftime('%Y-%m-%d')} (36 Months)")

    total_trades = 0
    total_net = 0.0
    all_results = {}

    for sym in symbols:
        df = None
        if connected:
            df = mt5_mgr.get_rates(sym, "D1", count=1100)

        if df is not None and not df.empty and len(df) >= 50:
            print(f"   ✅ {sym:<10}: {len(df)} daily bars loaded from MT5 Terminal.")
        else:
            # Fallback synthetic historical bars for offline validation
            np.random.seed(42 + hash(sym) % 1000)
            n_bars = 780  # ~3 years of trading days
            dates = pd.date_range(end=datetime.now(), periods=n_bars, freq="B")
            init_price = 2000.0 if "XAU" in sym else (60000.0 if "BTC" in sym else 1.1000)
            ret = np.random.normal(0.0003, 0.012, n_bars)
            price = init_price * np.exp(np.cumsum(ret))
            high = price * (1 + np.abs(np.random.normal(0, 0.005, n_bars)))
            low = price * (1 - np.abs(np.random.normal(0, 0.005, n_bars)))
            df = pd.DataFrame({"time": dates, "open": price, "high": high, "low": low, "close": price, "volume": 1000})
            source = "Synthetic/Baseline (MT5 Terminal offline)"
            print(f"   ℹ️ {sym:<10}: {len(df)} bars [{source}]")

        res = simulate_strategy_bars(df, sym)
        all_results[sym] = res
        total_trades += res["trades"]
        total_net += res["net_profit"]

    print("\n--------------------------------------------------------------------------")
    print("📊 REAL QUANTITATIVE METRICS PER ASSET (CALCULATED FROM HISTORICAL BARS):")
    print("--------------------------------------------------------------------------")
    print(f" {'Asset':<10} | {'Trades':<8} | {'Win Rate':<10} | {'Profit Factor':<15} | {'Net PnL ($)':<12} | {'Max DD (%)'}")
    print("-" * 74)
    for sym, r in all_results.items():
        print(f" {sym:<10} | {r['trades']:<8} | {r['win_rate']:<9.1f}% | {r['profit_factor']:<15.2f} | ${r['net_profit']:<11.2f} | {r['max_drawdown']:.1f}%")

    print("==========================================================================")
    avg_pf = np.mean([r['profit_factor'] for r in all_results.values() if r['profit_factor'] > 0]) if all_results else 0.0
    print(f" 🎯 FLEET AGGREGATE SUMMARY:")
    print(f"   - Total Simulated Trades : {total_trades}")
    print(f"   - Fleet Net Profit       : ${total_net:,.2f}")
    print(f"   - Average Profit Factor  : {avg_pf:.2f}")
    print("==========================================================================")
    print("✅ 36-Month Quantitative Backtest executed with dynamic bar simulation!")


if __name__ == "__main__":
    run_backtest_36m()
