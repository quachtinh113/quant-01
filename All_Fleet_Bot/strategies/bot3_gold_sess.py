"""
Unified Quant Fleet — Bot 3: Gold Session Strategy (exness_gold_sess)
Magic: 260903 | Symbols: XAUUSDm, XAGUSDm | Cadence: London & NY Open Sessions
"""

from datetime import datetime
from typing import Dict, Any, List
import numpy as np

from strategies.base_strategy import BaseStrategy
from core.mt5_connection_manager import MT5ConnectionManager
from core.fleet_risk_guard import FleetRiskGuard
from core.audit_logger import get_logger

logger = get_logger()


class GoldSessStrategy(BaseStrategy):
    def __init__(self, config: Dict[str, Any]):
        super().__init__(name="GOLD_SESS", magic=config.get("magic", 260903), config=config)
        self.symbols = config.get("symbols", ["XAUUSDm", "XAGUSDm"])
        self.session_hours = [8, 13]  # 08:00 UTC (London), 13:00 UTC (New York)
        self.sizing_mode = config.get("sizing_mode", "dynamic_risk")
        self.risk_per_trade_pct = config.get("risk_per_trade_pct", 0.005)
        self.half_kelly_mult = config.get("half_kelly_multiplier", 0.50)
        self.base_lot = config.get("base_lot", 0.05)
        self.min_lot = config.get("min_lot", 0.01)
        self.max_lot = config.get("max_lot", 2.0)
        self.sl_atr_mult = config.get("sl_atr_multiplier", 2.0)
        self.tp_atr_mult = config.get("tp_atr_multiplier", 2.5)
        self.spread_limit = config.get("spread_limit_points", 60)
        self.hourly_mode = config.get("hourly_mode", True)
        self._last_processed_hour = -1
        self._last_processed_session = -1

    def should_run_cycle(self, now_utc: datetime) -> bool:
        if not self.enabled:
            return False

        # Only trade Mon-Fri
        if now_utc.weekday() >= 5:
            return False

        if self.hourly_mode:
            # Check on every hour open (0 <= minute <= 5)
            if 0 <= now_utc.minute <= 5 and self._last_processed_hour != now_utc.hour:
                return True
        else:
            if now_utc.hour in self.session_hours and 0 <= now_utc.minute <= 5:
                if self._last_processed_session != now_utc.hour:
                    return True
        return False

    def evaluate_cycle(
        self,
        now_utc: datetime,
        mt5_mgr: MT5ConnectionManager,
        risk_guard: FleetRiskGuard,
        account_info: Dict[str, Any]
    ) -> None:
        self._last_processed_session = now_utc.hour
        self._last_processed_hour = now_utc.hour
        self.last_decision_time = now_utc
        sess_name = "Hourly" if self.hourly_mode else ("London" if now_utc.hour == 8 else "New York")
        logger.info(f"⚡ [{self.name}] Running {sess_name} Cycle @ {now_utc.strftime('%H:%M')} UTC")

        self.sync_positions(mt5_mgr)
        actions = []

        for symbol in self.symbols:
            spec = mt5_mgr.get_symbol_spec(symbol)
            tick = mt5_mgr.get_tick(symbol)
            if not spec or not tick:
                continue

            spread_points = int(round((tick["ask"] - tick["bid"]) / spec["point"]))
            if spread_points > self.spread_limit:
                logger.warning(f"⚠️ [{self.name}] {symbol} Spread {spread_points} > limit {self.spread_limit}. Skipping.")
                continue

            # Fetch H1 bars
            df = mt5_mgr.get_rates(symbol, "H1", count=40)
            if df is None or len(df) < 20:
                continue

            # Compute Asian Range (00:00 to 07:00 UTC)
            df['tr'] = np.maximum(
                df['high'] - df['low'],
                np.maximum((df['high'] - df['close'].shift()).abs(), (df['low'] - df['close'].shift()).abs())
            )
            df['atr'] = df['tr'].rolling(14).mean()

            last_bar = df.iloc[-2]
            current_price = tick["bid"]
            atr = last_bar['atr']

            # Session momentum check: EMA9 vs EMA21 on H1
            ema9 = df['close'].ewm(span=9, adjust=False).mean().iloc[-2]
            ema21 = df['close'].ewm(span=21, adjust=False).mean().iloc[-2]

            existing = [p for p in self.open_positions if p["symbol"] == symbol]

            # Relaxed signal: EMA9 vs EMA21 trend direction
            signal = None
            if ema9 > ema21:
                signal = "BUY"
            elif ema9 < ema21:
                signal = "SELL"
            else:
                logger.info(
                    f"ℹ️ [{self.name}] {symbol} No Signal (HOLD): "
                    f"Price={current_price:.3f}, EMA9={ema9:.3f}, EMA21={ema21:.3f}"
                )

            if len(existing) > 0:
                logger.info(f"ℹ️ [{self.name}] {symbol} already has {len(existing)} open position(s). Skipping entry.")

            if len(existing) == 0 and signal is not None:
                can_open, reason = risk_guard.can_open_position(account_info, len(mt5_mgr.get_positions()))
                if not can_open:
                    logger.warning(f"⛔ [{self.name}] Risk veto for {symbol}: {reason}")
                    continue

                price = tick["ask"] if signal == "BUY" else tick["bid"]
                sl_dist = atr * self.sl_atr_mult
                tp_dist = atr * self.tp_atr_mult
                sl = price - sl_dist if signal == "BUY" else price + sl_dist
                tp = price + tp_dist if signal == "BUY" else price - tp_dist

                contract_size = spec.get("contract_size", 100.0) if spec else 100.0
                order_vol = self.calculate_order_volume(
                    base_volume=self.base_lot,
                    risk_guard=risk_guard,
                    account_info=account_info,
                    sl_distance=sl_dist,
                    contract_size=contract_size,
                    risk_per_trade_pct=self.risk_per_trade_pct,
                    half_kelly_multiplier=self.half_kelly_mult,
                    sizing_mode=self.sizing_mode,
                    min_lot=self.min_lot,
                    max_lot=self.max_lot,
                )
                res = mt5_mgr.open_market_order(
                    symbol=symbol,
                    direction=signal,
                    volume=order_vol,
                    sl=sl,
                    tp=tp,
                    magic=self.magic,
                    comment=f"GOLD_{sess_name}"
                )
                if res.get("success"):
                    actions.append(f"OPEN {symbol} {signal} {order_vol}L")

        self.last_action = ", ".join(actions) if actions else "HOLD"

    def on_tick_update(
        self,
        now_utc: datetime,
        mt5_mgr: MT5ConnectionManager,
        risk_guard: FleetRiskGuard,
        account_info: Dict[str, Any]
    ) -> None:
        """
        Active intraday position management on every tick:
        1. Breakeven Lock: When profit reaches >= +1.5 * ATR, move SL to (Open Price + Spread Buffer).
        2. Dynamic Trailing Stop: When profit extends >= +2.0 * ATR, trail SL at (Current Price - 1.0 * ATR).
        """
        if not self.enabled:
            return

        self.sync_positions(mt5_mgr)
        if not self.open_positions:
            return

        for pos in self.open_positions:
            symbol = pos["symbol"]
            ticket = pos["ticket"]
            pos_type = pos["type"]
            open_price = float(pos["price_open"])
            current_price = float(pos["price_current"])
            current_sl = float(pos["sl"])
            current_tp = float(pos["tp"])

            spec = mt5_mgr.get_symbol_spec(symbol)
            if not spec:
                continue
            point = spec["point"]

            # Fetch latest H1 ATR
            df = mt5_mgr.get_rates(symbol, "H1", count=25)
            if df is None or len(df) < 15:
                continue

            df['tr'] = np.maximum(
                df['high'] - df['low'],
                np.maximum((df['high'] - df['close'].shift()).abs(), (df['low'] - df['close'].shift()).abs())
            )
            atr = df['tr'].rolling(14).mean().iloc[-2]
            if atr <= 0:
                continue

            be_threshold = 1.5 * atr
            trail_threshold = 2.0 * atr
            buffer_points = 10 * point  # Buffer covering spread/slippage

            if pos_type == "BUY":
                gain = current_price - open_price
                # 1. Breakeven Check
                if gain >= be_threshold and (current_sl < open_price or current_sl == 0):
                    new_sl = open_price + buffer_points
                    logger.info(
                        f"🛡️ [{self.name}] Breakeven triggered for {symbol} BUY #{ticket}: "
                        f"Gain +${gain:.2f} >= +${be_threshold:.2f} (1.5x ATR). Moving SL to {new_sl:.3f}"
                    )
                    mt5_mgr.modify_position(ticket=ticket, sl=new_sl, tp=current_tp)
                    current_sl = new_sl

                # 2. Dynamic Trailing Check
                if gain >= trail_threshold:
                    target_trail_sl = current_price - (1.0 * atr)
                    if target_trail_sl > current_sl + (2.0 * point):
                        logger.info(
                            f"🏃 [{self.name}] Trailing Stop updated for {symbol} BUY #{ticket}: "
                            f"Moving SL from {current_sl:.3f} to {target_trail_sl:.3f} (+${gain:.2f} gain)"
                        )
                        mt5_mgr.modify_position(ticket=ticket, sl=target_trail_sl, tp=current_tp)

            elif pos_type == "SELL":
                gain = open_price - current_price
                # 1. Breakeven Check
                if gain >= be_threshold and (current_sl > open_price or current_sl == 0):
                    new_sl = open_price - buffer_points
                    logger.info(
                        f"🛡️ [{self.name}] Breakeven triggered for {symbol} SELL #{ticket}: "
                        f"Gain +${gain:.2f} >= +${be_threshold:.2f} (1.5x ATR). Moving SL to {new_sl:.3f}"
                    )
                    mt5_mgr.modify_position(ticket=ticket, sl=new_sl, tp=current_tp)
                    current_sl = new_sl

                # 2. Dynamic Trailing Check
                if gain >= trail_threshold:
                    target_trail_sl = current_price + (1.0 * atr)
                    if current_sl == 0 or target_trail_sl < current_sl - (2.0 * point):
                        logger.info(
                            f"🏃 [{self.name}] Trailing Stop updated for {symbol} SELL #{ticket}: "
                            f"Moving SL from {current_sl:.3f} to {target_trail_sl:.3f} (+${gain:.2f} gain)"
                        )
                        mt5_mgr.modify_position(ticket=ticket, sl=target_trail_sl, tp=current_tp)
