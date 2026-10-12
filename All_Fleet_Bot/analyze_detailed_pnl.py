"""
========================================================================================
🏛️ DETAILED PNL & TRADING PERFORMANCE AUDIT REPORT
Module: analyze_detailed_pnl.py
Path: C:/Word_Quant/05_Quant/05_ALL_Bot/All_Fleet_Bot/analyze_detailed_pnl.py

Calculates comprehensive institutional PnL metrics:
- Trade distribution (Win/Loss, Payoff ratio, Expectancy per trade)
- Monthly & Quarterly PnL breakdown
- Session PnL breakdown (London 08:00 UTC vs NY 13:00 UTC)
- Directional PnL breakdown (BUY vs SELL)
- Equity curve milestones, Max consecutive streaks & Drawdown recovery
========================================================================================
"""

from __future__ import annotations

import os
import sys
import math
from pathlib import Path
from datetime import datetime, timezone
import pandas as pd
import numpy as np

FLEET_DIR = Path(__file__).resolve().parent
DATA_DIR = FLEET_DIR.parent / "continuum" / "data" / "historical"

if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

from rigorous_fleet_audit import backtest_gold_sess, get_friction_model, calc_cost


def run_detailed_pnl_analysis():
    xau_h1_file = DATA_DIR / "XAUUSD_H1.csv"
    if not xau_h1_file.exists():
        print(f"❌ Error: {xau_h1_file} not found!")
        return

    df_xau = pd.read_csv(xau_h1_file)
    trades = backtest_gold_sess(df_xau, lot=0.01)

    if not trades:
        print("❌ No trades generated!")
        return

    df_trades = pd.DataFrame(trades)
    df_trades['entry_time'] = pd.to_datetime(df_trades['entry_time'], utc=True)
    df_trades['exit_time'] = pd.to_datetime(df_trades['exit_time'], utc=True)
    df_trades['year_month'] = df_trades['entry_time'].dt.strftime('%Y-%m')
    df_trades['hour_utc'] = df_trades['entry_time'].dt.hour
    df_trades['session'] = df_trades['hour_utc'].apply(lambda h: "London (08:00 UTC)" if h == 8 else "New York (13:00 UTC)")

    # 1. High-Level Metrics
    initial_capital = 10000.0
    total_trades = len(df_trades)
    wins = df_trades[df_trades['net_pnl'] > 0]
    losses = df_trades[df_trades['net_pnl'] < 0]
    breakeven = df_trades[df_trades['net_pnl'] == 0]

    win_count = len(wins)
    loss_count = len(losses)
    win_rate = (win_count / total_trades) * 100.0

    gross_profit = wins['net_pnl'].sum()
    gross_loss = abs(losses['net_pnl'].sum())
    net_pnl = df_trades['net_pnl'].sum()
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float('inf')

    avg_win = wins['net_pnl'].mean() if not wins.empty else 0.0
    avg_loss = abs(losses['net_pnl'].mean()) if not losses.empty else 0.0
    payoff_ratio = (avg_win / avg_loss) if avg_loss > 0 else 0.0
    expectancy_usd = (win_rate / 100.0 * avg_win) - ((1 - win_rate / 100.0) * avg_loss)

    # 2. Streaks
    is_win = (df_trades['net_pnl'] > 0).astype(int)
    max_consec_wins = 0
    max_consec_losses = 0
    curr_w = 0
    curr_l = 0
    for w in is_win:
        if w == 1:
            curr_w += 1
            curr_l = 0
            if curr_w > max_consec_wins:
                max_consec_wins = curr_w
        else:
            curr_l += 1
            curr_w = 0
            if curr_l > max_consec_losses:
                max_consec_losses = curr_l

    # 3. Drawdown & Recovery
    equity_curve = initial_capital + df_trades['net_pnl'].cumsum()
    peak = equity_curve.cummax()
    dd_usd = peak - equity_curve
    dd_pct = (dd_usd / peak) * 100.0
    max_dd_usd = dd_usd.max()
    max_dd_pct = dd_pct.max()
    recovery_factor = (net_pnl / max_dd_usd) if max_dd_usd > 0 else float('inf')

    # 4. Directional Breakdown (BUY vs SELL)
    buy_trades = df_trades[df_trades['type'] == 'BUY']
    sell_trades = df_trades[df_trades['type'] == 'SELL']

    buy_pnl = buy_trades['net_pnl'].sum()
    buy_win_rate = (len(buy_trades[buy_trades['net_pnl'] > 0]) / len(buy_trades) * 100.0) if len(buy_trades) > 0 else 0.0
    
    sell_pnl = sell_trades['net_pnl'].sum()
    sell_win_rate = (len(sell_trades[sell_trades['net_pnl'] > 0]) / len(sell_trades) * 100.0) if len(sell_trades) > 0 else 0.0

    # 5. Session Breakdown (London vs New York)
    london_trades = df_trades[df_trades['session'].str.contains('London')]
    ny_trades = df_trades[df_trades['session'].str.contains('New York')]

    london_pnl = london_trades['net_pnl'].sum()
    london_win_rate = (len(london_trades[london_trades['net_pnl'] > 0]) / len(london_trades) * 100.0) if len(london_trades) > 0 else 0.0

    ny_pnl = ny_trades['net_pnl'].sum()
    ny_win_rate = (len(ny_trades[ny_trades['net_pnl'] > 0]) / len(ny_trades) * 100.0) if len(ny_trades) > 0 else 0.0

    # 6. Monthly Breakdown Table
    monthly_summary = df_trades.groupby('year_month').agg(
        trades=('net_pnl', 'count'),
        wins=('net_pnl', lambda s: (s > 0).sum()),
        net_pnl=('net_pnl', 'sum'),
    ).reset_index()
    monthly_summary['win_rate'] = (monthly_summary['wins'] / monthly_summary['trades']) * 100.0

    # Print Report
    print("=" * 80)
    print("📊 BÁO CÁO PHÂN TÍCH PnL CHI TIẾT — BOT 3: GOLD_SESS (XAUUSD H1)")
    print("=" * 80)
    print(f"💰 Vốn ban đầu (Initial Capital)   : ${initial_capital:,.2f}")
    print(f"💵 Vốn cuối kỳ (Ending Equity)     : ${equity_curve.iloc[-1]:,.2f}")
    print(f"📈 Lãi ròng (Total Net PnL)        : +${net_pnl:,.2f} (+{(net_pnl/initial_capital)*100.1:.2f}%)")
    print(f"🎯 Tổng số lệnh (Total Trades)     : {total_trades}")
    print(f"✅ Số lệnh thắng (Winning Trades)  : {win_count} ({win_rate:.1f}%)")
    print(f"❌ Số lệnh thua (Losing Trades)    : {loss_count} ({100.0 - win_rate:.1f}%)")
    print("-" * 80)
    print(f"🟢 Tổng lãi gộp (Gross Profit)    : +${gross_profit:,.2f}")
    print(f"🔴 Tổng lỗ gộp (Gross Loss)       : -${gross_loss:,.2f}")
    print(f"⚖️ Profit Factor (PF)              : {profit_factor:.2f}")
    print(f"💵 Lãi trung bình / lệnh thắng    : +${avg_win:.2f}")
    print(f"💸 Lỗ trung bình / lệnh thua      : -${avg_loss:.2f}")
    print(f"📐 Tỷ lệ Thưởng/Rủi ro (Payoff)   : {payoff_ratio:.2f}")
    print(f"🎲 Kỳ vọng toán học (Expectancy)  : +${expectancy_usd:.2f} / mỗi lệnh vào")
    print("-" * 80)
    print(f"🔥 Chuỗi thắng liên tiếp dài nhất : {max_consec_wins} lệnh")
    print(f"🧊 Chuỗi thua liên tiếp dài nhất  : {max_consec_losses} lệnh")
    print(f"🛡️ Sụt giảm tối đa (Max Drawdown) : ${max_dd_usd:.2f} ({max_dd_pct:.2f}%)")
    print(f"🔄 Hệ số phục hồi (Recovery Factor: {recovery_factor:.2f}")
    print("=" * 80)

    print("\n📍 PHÂN TÍCH PnL THEO CHIỀU GIAO DỊCH (BUY VS SELL):")
    print(f"  - Lệnh MUA (BUY)  : {len(buy_trades):<4} lệnh | Win%: {buy_win_rate:.1f}% | Net PnL: +${buy_pnl:,.2f}")
    print(f"  - Lệnh BÁN (SELL) : {len(sell_trades):<4} lệnh | Win%: {sell_win_rate:.1f}% | Net PnL: +${sell_pnl:,.2f}")

    print("\n⏰ PHÂN TÍCH PnL THEO PHIÊN GIAO DỊCH (LONDON VS NEW YORK):")
    print(f"  - Phiên London (08:00 UTC)   : {len(london_trades):<4} lệnh | Win%: {london_win_rate:.1f}% | Net PnL: +${london_pnl:,.2f}")
    print(f"  - Phiên New York (13:00 UTC) : {len(ny_trades):<4} lệnh | Win%: {ny_win_rate:.1f}% | Net PnL: +${ny_pnl:,.2f}")

    print("\n📅 BẢNG PnL THEO TỪNG THÁNG (MONTHLY BREAKDOWN):")
    print(f" {'Tháng':<10} | {'Số Lệnh':<8} | {'Thắng':<6} | {'Win Rate':<10} | {'Lãi Ròng PnL ($)'}")
    print("-" * 55)
    for _, r in monthly_summary.iterrows():
        sign = "+" if r['net_pnl'] >= 0 else ""
        print(f" {r['year_month']:<10} | {int(r['trades']):<8} | {int(r['wins']):<6} | {r['win_rate']:<9.1f}% | {sign}${r['net_pnl']:,.2f}")
    print("-" * 55)

    # Save to file
    out_file = FLEET_DIR / "DETAILED_PNL_ANALYSIS.md"
    with open(out_file, "w", encoding="utf-8") as f:
        f.write("# 📊 BÁO CÁO PHÂN TÍCH PnL CHI TIẾT — BOT 3: GOLD_SESS (XAUUSD)\n\n")
        f.write(f"**Thời gian phân tích:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n\n")
        f.write("## 1. Tóm Tắt Hiệu Quả Sinh Lời & Rủi Ro\n\n")
        f.write(f"- **Vốn ban đầu:** ${initial_capital:,.2f}\n")
        f.write(f"- **Vốn cuối kỳ:** ${equity_curve.iloc[-1]:,.2f}\n")
        f.write(f"- **Tổng Lãi Ròng (Net PnL):** **+${net_pnl:,.2f} (+{(net_pnl/initial_capital)*100.0:.2f}%)**\n")
        f.write(f"- **Tổng số lệnh:** {total_trades} (Lệnh thắng: {win_count} | Lệnh thua: {loss_count})\n")
        f.write(f"- **Tỷ lệ thắng (Win Rate):** **{win_rate:.2f}%**\n")
        f.write(f"- **Hệ số Lợi Nhuận (Profit Factor):** **{profit_factor:.2f}**\n")
        f.write(f"- **Tỷ lệ Thưởng/Rủi ro (Payoff Ratio):** **{payoff_ratio:.2f}** (Lãi TB: +${avg_win:.2f} / Lỗ TB: -${avg_loss:.2f})\n")
        f.write(f"- **Kỳ vọng toán học (Expectancy):** **+${expectancy_usd:.2f}** / mỗi lệnh vào\n")
        f.write(f"- **Sụt giảm tối đa (Max Drawdown):** **${max_dd_usd:.2f} ({max_dd_pct:.2f}%)**\n")
        f.write(f"- **Hệ số phục hồi (Recovery Factor):** **{recovery_factor:.2f}**\n")
        f.write(f"- **Chuỗi thắng/thua dài nhất:** Thắng liên tiếp: {max_consec_wins} lệnh | Thua liên tiếp: {max_consec_losses} lệnh\n\n")
        f.write("## 2. Phân Tích Chi Tiết Theo Phiên & Chiều Lệnh\n\n")
        f.write("| Phân Loại | Số Lệnh | Win Rate | Lãi Ròng (Net PnL) |\n")
        f.write("| :--- | :---: | :---: | :---: |\n")
        f.write(f"| **Phiên London (08:00 UTC)** | {len(london_trades)} | {london_win_rate:.1f}% | **+${london_pnl:,.2f}** |\n")
        f.write(f"| **Phiên New York (13:00 UTC)** | {len(ny_trades)} | {ny_win_rate:.1f}% | **+${ny_pnl:,.2f}** |\n")
        f.write(f"| **Lệnh Mua (BUY)** | {len(buy_trades)} | {buy_win_rate:.1f}% | **+${buy_pnl:,.2f}** |\n")
        f.write(f"| **Lệnh Bán (SELL)** | {len(sell_trades)} | {sell_win_rate:.1f}% | **+${sell_pnl:,.2f}** |\n\n")
        f.write("## 3. Bảng PnL Theo Từng Tháng (Monthly Breakdown)\n\n")
        f.write("| Tháng | Số Lệnh | Thắng | Tỷ Lệ Thắng | Lãi Ròng PnL ($) |\n")
        f.write("| :---: | :---: | :---: | :---: | :---: |\n")
        for _, r in monthly_summary.iterrows():
            sign = "+" if r['net_pnl'] >= 0 else ""
            f.write(f"| {r['year_month']} | {int(r['trades'])} | {int(r['wins'])} | {r['win_rate']:.1f}% | **{sign}${r['net_pnl']:,.2f}** |\n")
    print(f"\n📝 Đã xuất báo cáo chi tiết ra file: {out_file}")


if __name__ == "__main__":
    run_detailed_pnl_analysis()
