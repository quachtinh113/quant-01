"""
Unified Quant Fleet — Bot 1: Bitcoin 8-Hour Strategy (exness_btc_8h)
Magic: 260904 | Symbol: BTCUSDm | Cadence: 8-Hour (00:00, 08:00, 16:00 UTC) | 24/7 Resilient Runtime v1.1
"""

from datetime import datetime, timezone
from typing import Dict, Any
import numpy as np
import pandas as pd

from strategies.base_strategy import BaseStrategy
from core.mt5_connection_manager import MT5ConnectionManager
from core.fleet_risk_guard import FleetRiskGuard
from core.audit_logger import get_logger

logger = get_logger()


def calculate_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-9)
    return 100.0 - (100.0 / (1.0 + rs))


def calculate_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    up_move = df['high'] - df['high'].shift()
    down_move = df['low'].shift() - df['low']
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    high_low = df['high'] - df['low']
    high_close = (df['high'] - df['close'].shift()).abs()
    low_close = (df['low'] - df['close'].shift()).abs()
    tr = np.maximum(high_low, np.maximum(high_close, low_close))

    tr_smooth = pd.Series(tr, index=df.index).ewm(com=period - 1, min_periods=period).mean()
    plus_di = 100.0 * (pd.Series(plus_dm, index=df.index).ewm(com=period - 1, min_periods=period).mean() / tr_smooth.replace(0, 1e-9))
    minus_di = 100.0 * (pd.Series(minus_dm, index=df.index).ewm(com=period - 1, min_periods=period).mean() / tr_smooth.replace(0, 1e-9))

    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1e-9)
    return dx.ewm(com=period - 1, min_periods=period).mean()


class Btc8hStrategy(BaseStrategy):
    def __init__(self, config: Dict[str, Any]):
        super().__init__(name="BTC_8H", magic=config.get("magic", 260904), config=config)
        self.symbol = config.get("symbol", "BTCUSDm")
        self.decision_hours = config.get("decision_utc_hours", [0, 8, 16])
        self.sizing_mode = config.get("sizing_mode", "dynamic_risk")
        self.risk_per_trade_pct = config.get("risk_per_trade_pct", 0.50)
        self.base_lot = config.get("base_lot", 0.01)
        self.min_lot = config.get("min_lot", 0.01)
        self.max_lot = config.get("max_lot", 0.04)
        self.sl_atr_mult = config.get("sl_atr_multiplier", 2.0)
        self.tp_atr_mult = config.get("tp_atr_multiplier", 3.0)
        self.spread_limit = config.get("spread_limit_points", 2000)
        self._last_processed_hour = -1

    def should_run_cycle(self, now_utc: datetime) -> bool:
        if not self.enabled:
            return False

        # Run within 5 minutes past the decision hours (00:01-00:05, 08:01-08:05, 16:01-16:05 UTC)
        if now_utc.hour in self.decision_hours and 0 <= now_utc.minute <= 5:
            if self._last_processed_hour != now_utc.hour:
                return True
        return False

    def evaluate_cycle(
        self,
        now_utc: datetime,
        mt5_mgr: MT5ConnectionManager,
        risk_guard: FleetRiskGuard,
        account_info: Dict[str, Any]
    ) -> None:
        self._last_processed_hour = now_utc.hour
        self.last_decision_time = now_utc
        logger.info(f"⚡ [{self.name}] Running 8-Hour Decision Cycle for {self.symbol} @ {now_utc.strftime('%H:%M')} UTC")

        self.sync_positions(mt5_mgr)

        # Check spread
        tick = mt5_mgr.get_tick(self.symbol)
        spec = mt5_mgr.get_symbol_spec(self.symbol)
        if not tick or not spec:
            self.last_action = "SKIP: No tick/spec"
            return

        spread_points = int(round((tick["ask"] - tick["bid"]) / spec["point"]))
        if spread_points > self.spread_limit:
            logger.warning(f"⚠️ [{self.name}] Spread {spread_points} > limit {self.spread_limit}. Skipping.")
            self.last_action = f"SKIP: High Spread ({spread_points} pts)"
            return

        # Fetch H4 bars (8H folded from H4)
        df = mt5_mgr.get_rates(self.symbol, "H4", count=60)
        if df is None or len(df) < 30:
            self.last_action = "SKIP: Insufficient data"
            return

        # Technical Indicators: EMA20, EMA50, ATR14, RSI14, ADX14
        df['ema20'] = df['close'].ewm(span=20, adjust=False).mean()
        df['ema50'] = df['close'].ewm(span=50, adjust=False).mean()
        df['rsi'] = calculate_rsi(df['close'], 14)
        df['adx'] = calculate_adx(df, 14)

        high_low = df['high'] - df['low']
        high_close = (df['high'] - df['close'].shift()).abs()
        low_close = (df['low'] - df['close'].shift()).abs()
        df['tr'] = np.maximum(high_low, np.maximum(high_close, low_close))
        df['atr'] = df['tr'].rolling(14).mean()

        last_row = df.iloc[-2]  # Closed bar
        current_close = last_row['close']
        ema20 = last_row['ema20']
        ema50 = last_row['ema50']
        atr = last_row['atr']
        rsi = last_row['rsi'] if not pd.isna(last_row['rsi']) else 50.0
        adx = last_row['adx'] if not pd.isna(last_row['adx']) else 20.0

        raw_signal = None
        if ema20 > ema50 and current_close > ema20:
            raw_signal = "BUY"
        elif ema20 < ema50 and current_close < ema20:
            raw_signal = "SELL"

        signal = None
        if raw_signal == "BUY":
            # Do NOT buy if already overbought (RSI >= 68) or trend is dead (ADX < 18)
            if rsi < 68.0 and adx >= 18.0:
                signal = "BUY"
            else:
                logger.info(f"ℹ️ [{self.name}] BUY signal filtered: RSI={rsi:.1f} (limit <68), ADX={adx:.1f} (limit >=18)")
        elif raw_signal == "SELL":
            # Do NOT sell into support / oversold squeeze zone (RSI <= 35) or trend is dead (ADX < 18)
            if rsi > 35.0 and adx >= 18.0:
                signal = "SELL"
            else:
                logger.info(f"ℹ️ [{self.name}] SELL signal filtered: RSI={rsi:.1f} (limit >35), ADX={adx:.1f} (limit >=18)")

        logger.info(
            f"📊 [{self.name}] Indicator State: Close={current_close:.2f}, EMA20={ema20:.2f}, EMA50={ema50:.2f}, "
            f"RSI={rsi:.1f}, ADX={adx:.1f}, ATR={atr:.2f} => Signal: {signal} (Raw: {raw_signal})"
        )

        # Position Management
        if len(self.open_positions) > 0:
            # Check for reversal exit
            for pos in self.open_positions:
                if (pos["type"] == "BUY" and signal == "SELL") or (pos["type"] == "SELL" and signal == "BUY"):
                    logger.info(f"🔄 [{self.name}] Reversal detected. Closing position #{pos['ticket']}")
                    mt5_mgr.close_position(pos["ticket"])
            self.sync_positions(mt5_mgr)

        # Open New Position if no existing position
        if len(self.open_positions) == 0 and signal is not None:
            can_open, reason = risk_guard.can_open_position(
                account_info=account_info,
                current_position_count=len(mt5_mgr.get_positions()),
                open_positions=mt5_mgr.get_positions(),
                symbol=self.symbol,
                signal=signal
            )
            if not can_open:
                logger.warning(f"⛔ [{self.name}] Risk guard vetoed entry: {reason}")
                self.last_action = f"VETO: {reason}"
                return

            price = tick["ask"] if signal == "BUY" else tick["bid"]
            sl_dist = atr * self.sl_atr_mult
            tp_dist = atr * self.tp_atr_mult
            sl = price - sl_dist if signal == "BUY" else price + sl_dist
            tp = price + tp_dist if signal == "BUY" else price - tp_dist

            contract_size = spec.get("contract_size", 1.0) if spec else 1.0
            order_vol = self.calculate_order_volume(
                base_volume=self.base_lot,
                risk_guard=risk_guard,
                account_info=account_info,
                sl_distance=sl_dist,
                contract_size=contract_size,
                risk_per_trade_pct=self.risk_per_trade_pct,
                half_kelly_multiplier=1.0,
                sizing_mode=self.sizing_mode,
                min_lot=self.min_lot,
                max_lot=self.max_lot,
                symbol=self.symbol,
            )
            res = mt5_mgr.open_market_order(
                symbol=self.symbol,
                direction=signal,
                volume=order_vol,
                sl=sl,
                tp=tp,
                magic=self.magic,
                comment="BTC_8H Fleet"
            )
            if res.get("success"):
                self.last_action = f"OPEN {signal} #{res['ticket']}"
            else:
                self.last_action = f"ERROR: {res.get('error')}"
        else:
            self.last_action = f"HOLD ({len(self.open_positions)} active)"

        # Memory hygiene: release DataFrame
        del df
