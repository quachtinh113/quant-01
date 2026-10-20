"""
Unified Quant Fleet — MT5 Connection Manager
Central singleton connector for MetaTrader 5 on 1 PC.
Handles connection health, auto-reconnection, data feeds, and order routing.
"""

import os
import time
import math
import logging
import threading
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime, timezone
import pandas as pd

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    mt5 = None
    MT5_AVAILABLE = False

from core.audit_logger import get_logger

logger = get_logger()

# Timeframe mapping
TIMEFRAME_MAP = {
    "M1": mt5.TIMEFRAME_M1 if MT5_AVAILABLE else 1,
    "M5": mt5.TIMEFRAME_M5 if MT5_AVAILABLE else 5,
    "M15": mt5.TIMEFRAME_M15 if MT5_AVAILABLE else 15,
    "M30": mt5.TIMEFRAME_M30 if MT5_AVAILABLE else 30,
    "H1": mt5.TIMEFRAME_H1 if MT5_AVAILABLE else 16385,
    "H4": mt5.TIMEFRAME_H4 if MT5_AVAILABLE else 16388,
    "D1": mt5.TIMEFRAME_D1 if MT5_AVAILABLE else 16408,
}

# =====================================================================
# MAX ALLOWED SPREAD LIMITS (Execution Layer Guard - Points)
# Protects against spread expansion during rollovers / high-impact news
# =====================================================================
MAX_ALLOWED_SPREAD_POINTS = {
    "BTCUSD": 5000,    # Tuỳ theo quy cách contract của Exness
    "BTCUSDm": 5000,
    "XAUUSD": 60,      # ~60 points = 6 pips
    "XAUUSDm": 60,
    "EURUSD": 30,      # 3 pips
    "EURUSDm": 30,
    "GBPUSD": 35,
    "GBPUSDm": 35,
    "USDJPY": 30,
    "USDJPYm": 30,
    "DEFAULT": 50
}


class MT5ConnectionManager:
    _instance = None
    _lock = threading.RLock()

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(MT5ConnectionManager, cls).__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self, terminal_path: str = "", account: int = 0, password: str = "", server: str = ""):
        if self._initialized:
            return

        self.terminal_path = terminal_path or os.getenv("MT5_PATH", "")
        self.account = account or int(os.getenv("MT5_ACCOUNT", "0"))
        self.password = password or os.getenv("MT5_PASSWORD", "")
        self.server = server or os.getenv("MT5_SERVER", "")
        self.connected = False
        self._last_health_check = 0.0
        self.correlation_manager = None
        self._initialized = True

    def set_correlation_manager(self, manager) -> None:
        """Attach CorrelationRiskManager to Execution Layer."""
        self.correlation_manager = manager

    def connect(self) -> bool:
        """Initialize and login to MetaTrader 5."""
        if not MT5_AVAILABLE:
            logger.error("❌ MetaTrader5 Python library is not installed.")
            return False

        with self._lock:
            init_kwargs = {"timeout": 5000}
            if self.terminal_path and os.path.exists(self.terminal_path):
                init_kwargs["path"] = self.terminal_path
            if self.account:
                init_kwargs["login"] = int(self.account)
            if self.password:
                init_kwargs["password"] = str(self.password)
            if self.server:
                init_kwargs["server"] = str(self.server)

            if not mt5.initialize(**init_kwargs):
                err = mt5.last_error()
                logger.error(f"❌ MT5 initialize() failed: {err}")
                self.connected = False
                return False

            if self.account and self.password and self.server:
                login_ok = mt5.login(login=self.account, password=self.password, server=self.server)
                if not login_ok:
                    err = mt5.last_error()
                    logger.error(f"❌ MT5 login({self.account} @ {self.server}) failed: {err}")
                    self.connected = False
                    return False

            term_info = mt5.terminal_info()
            acct_info = mt5.account_info()
            if not term_info or not acct_info:
                logger.warning("⚠️ MT5 initialized but terminal_info / account_info unavailable.")
                self.connected = False
                return False

            self.connected = True
            logger.info(
                f"✅ MT5 Connected: Account {acct_info.login} ({acct_info.server}), "
                f"Balance: ${acct_info.balance:.2f}, Equity: ${acct_info.equity:.2f}, Leverage: 1:{acct_info.leverage}"
            )
            return True

    def ensure_connected(self) -> bool:
        """Check connection health and auto-reconnect if needed."""
        now = time.time()
        if now - self._last_health_check < 5.0 and self.connected:
            return True

        self._last_health_check = now
        if not MT5_AVAILABLE:
            return False

        try:
            term_info = mt5.terminal_info()
            if term_info and term_info.connected:
                self.connected = True
                return True
        except Exception:
            pass

        logger.warning("⚠️ MT5 connection dropped. Attempting reconnect...")
        return self.connect()

    def get_account_info(self) -> Optional[Dict[str, Any]]:
        """Retrieve latest account snapshot."""
        if not self.ensure_connected():
            return None

        with self._lock:
            info = mt5.account_info()
            if not info:
                return None
            return {
                "login": info.login,
                "server": info.server,
                "balance": float(info.balance),
                "equity": float(info.equity),
                "margin": float(info.margin),
                "free_margin": float(info.margin_free),
                "margin_level": float(info.margin_level) if info.margin_level else 0.0,
                "profit": float(info.profit),
                "currency": info.currency,
                "leverage": info.leverage,
            }

    def resolve_symbol(self, symbol: str) -> Optional[str]:
        """Auto-detect valid broker symbol variation (e.g. XAUUSDm, XAUUSD, XAUUSD.pro, XAUUSDc)."""
        if not self.ensure_connected():
            return None

        with self._lock:
            info = mt5.symbol_info(symbol)
            if info is not None:
                if not info.visible:
                    mt5.symbol_select(symbol, True)
                return symbol

            # Generate candidate variations
            candidates = []
            if symbol.endswith('m'):
                candidates.append(symbol[:-1])
            else:
                candidates.append(symbol + 'm')

            base = symbol.rstrip('mc_')
            for suffix in ["", "m", "c", ".pro", "_i", ".r"]:
                cand = f"{base}{suffix}"
                if cand not in candidates and cand != symbol:
                    candidates.append(cand)

            for cand in candidates:
                cand_info = mt5.symbol_info(cand)
                if cand_info is not None:
                    if not cand_info.visible:
                        mt5.symbol_select(cand, True)
                    return cand

            logger.error(f"❌ Symbol '{symbol}' could not be resolved across candidates in Market Watch.")
            return None

    def get_rates(self, symbol: str, timeframe_str: str, count: int = 100) -> Optional[pd.DataFrame]:
        """Fetch latest candles for a symbol. Returns DataFrame with UTC datetime index."""
        if not self.ensure_connected():
            return None

        resolved = self.resolve_symbol(symbol)
        if not resolved:
            return None
        symbol = resolved

        tf = TIMEFRAME_MAP.get(timeframe_str.upper(), mt5.TIMEFRAME_H1)

        with self._lock:
            rates = mt5.copy_rates_from_pos(symbol, tf, 0, count)
            if rates is None or len(rates) == 0:
                logger.error(f"❌ Failed to fetch rates for {symbol} ({timeframe_str}): {mt5.last_error()}")
                return None

            df = pd.DataFrame(rates)
            df['datetime'] = pd.to_datetime(df['time'], unit='s', utc=True)
            df.set_index('datetime', inplace=True)
            return df

    def get_tick(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Fetch latest tick for a symbol."""
        if not self.ensure_connected():
            return None

        resolved = self.resolve_symbol(symbol)
        if not resolved:
            return None
        symbol = resolved

        with self._lock:
            tick = mt5.symbol_info_tick(symbol)
            if not tick:
                return None
            return {
                "bid": float(tick.bid),
                "ask": float(tick.ask),
                "spread": float(tick.ask - tick.bid),
                "time": tick.time,
            }

    def get_symbol_spec(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Retrieve instrument specifications (point, digits, contract_size, lot steps)."""
        if not self.ensure_connected():
            return None

        resolved = self.resolve_symbol(symbol)
        if not resolved:
            return None
        symbol = resolved

        with self._lock:
            info = mt5.symbol_info(symbol)
            if not info:
                return None
            return {
                "point": float(info.point),
                "digits": int(info.digits),
                "contract_size": float(info.trade_contract_size),
                "volume_min": float(info.volume_min),
                "volume_max": float(info.volume_max),
                "volume_step": float(info.volume_step),
                "spread_points": int(info.spread),
            }

    def normalize_lot(self, symbol: str, lot: float) -> float:
        """Round volume down to the instrument's volume_step and clamp between min and max."""
        spec = self.get_symbol_spec(symbol)
        if not spec:
            return round(lot, 2)

        vol_min = spec["volume_min"]
        vol_max = spec["volume_max"]
        vol_step = spec["volume_step"]

        steps = math.floor((lot - vol_min) / vol_step)
        norm = vol_min + (steps * vol_step)
        norm = max(vol_min, min(norm, vol_max))

        # Precision from step
        decimals = 2
        if vol_step >= 1:
            decimals = 0
        elif vol_step >= 0.1:
            decimals = 1
        elif vol_step >= 0.01:
            decimals = 2
        elif vol_step >= 0.001:
            decimals = 3

        return round(norm, decimals)

    def get_positions(self, magic: Optional[int] = None, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Retrieve active open positions filtered by magic and/or symbol."""
        if not self.ensure_connected():
            return []

        with self._lock:
            raw = mt5.positions_get()
            if raw is None:
                return []

            positions = []
            for p in raw:
                if magic is not None and p.magic != magic:
                    continue
                if symbol is not None and p.symbol != symbol:
                    continue
                positions.append({
                    "ticket": p.ticket,
                    "symbol": p.symbol,
                    "type": "BUY" if p.type == mt5.ORDER_TYPE_BUY else "SELL",
                    "volume": float(p.volume),
                    "price_open": float(p.price_open),
                    "price_current": float(p.price_current),
                    "sl": float(p.sl),
                    "tp": float(p.tp),
                    "profit": float(p.profit),
                    "magic": int(p.magic),
                    "comment": str(p.comment),
                    "time": datetime.fromtimestamp(p.time, tz=timezone.utc),
                })
            return positions

    def get_filling_mode(self, symbol: str) -> int:
        """Dynamically detect supported filling mode for symbol (FOK, IOC, RETURN)."""
        if not MT5_AVAILABLE:
            return 1
        sym_info = mt5.symbol_info(symbol)
        if not sym_info:
            return mt5.ORDER_FILLING_IOC
        fill_modes = getattr(sym_info, "filling_mode", 0)
        if fill_modes & 2:
            return mt5.ORDER_FILLING_IOC
        elif fill_modes & 1:
            return mt5.ORDER_FILLING_FOK
        elif fill_modes & 4:
            return mt5.ORDER_FILLING_RETURN
        return mt5.ORDER_FILLING_IOC

    def is_spread_acceptable(self, symbol: str) -> Tuple[bool, float, float]:
        """
        Check if the current spread is acceptable under MAX_ALLOWED_SPREAD_POINTS.
        Returns: (is_acceptable, spread_points, max_limit)
        """
        spec = self.get_symbol_spec(symbol)
        tick = self.get_tick(symbol)
        if not spec or not tick:
            return False, 999999.0, 0.0

        point = spec.get("point", 0.00001)
        spread_points = (tick["ask"] - tick["bid"]) / point if point > 0 else 999999.0

        canonical = symbol
        for suffix in ["m", ".pro", "c", "_i", ".a"]:
            if canonical.endswith(suffix):
                canonical = canonical[:-len(suffix)]
                break

        limit = MAX_ALLOWED_SPREAD_POINTS.get(
            symbol,
            MAX_ALLOWED_SPREAD_POINTS.get(canonical, MAX_ALLOWED_SPREAD_POINTS.get("DEFAULT", 50))
        )
        return (spread_points <= limit), spread_points, float(limit)

    def open_market_order(
        self,
        symbol: str,
        direction: str,  # "BUY" or "SELL"
        volume: float,
        sl: float = 0.0,
        tp: float = 0.0,
        magic: int = 0,
        comment: str = ""
    ) -> Dict[str, Any]:
        """Send an IOC market order to MT5."""
        if not self.ensure_connected():
            return {"success": False, "error": "Not connected to MT5"}

        spec = self.get_symbol_spec(symbol)
        if not spec:
            return {"success": False, "error": f"Invalid symbol {symbol}"}

        lot = self.normalize_lot(symbol, volume)
        tick = self.get_tick(symbol)
        if not tick:
            return {"success": False, "error": f"Failed to get tick for {symbol}"}

        # Spread Protection Filter (Execution Layer Guard)
        point = spec.get("point", 0.00001)
        spread_points = (tick["ask"] - tick["bid"]) / point if point > 0 else 999999.0

        canonical = symbol
        for suffix in ["m", ".pro", "c", "_i", ".a"]:
            if canonical.endswith(suffix):
                canonical = canonical[:-len(suffix)]
                break

        limit = MAX_ALLOWED_SPREAD_POINTS.get(
            symbol,
            MAX_ALLOWED_SPREAD_POINTS.get(canonical, MAX_ALLOWED_SPREAD_POINTS.get("DEFAULT", 50))
        )

        if spread_points > limit:
            logger.warning(
                f"⚠️ [SPREAD FILTER] {symbol} spread too wide: {spread_points:.1f} pts > max {limit} pts. Order rejected."
            )
            return {
                "success": False,
                "error": f"[SPREAD FILTER] {symbol} spread too wide: {spread_points:.1f} pts > max {limit} pts",
                "spread_points": spread_points,
                "max_limit": limit,
            }

        # Correlation & Exposure Gatekeeper (Execution Layer Defense-in-depth)
        if self.correlation_manager is not None:
            open_pos = self.get_positions()
            allowed, reason = self.correlation_manager.can_execute_order(symbol, direction, open_pos)
            if not allowed:
                return {
                    "success": False,
                    "error": f"[RISK GATEKEEPER] {reason}"
                }

        order_type = mt5.ORDER_TYPE_BUY if direction.upper() == "BUY" else mt5.ORDER_TYPE_SELL
        price = tick["ask"] if direction.upper() == "BUY" else tick["bid"]

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": lot,
            "type": order_type,
            "price": price,
            "sl": round(sl, spec["digits"]) if sl > 0 else 0.0,
            "tp": round(tp, spec["digits"]) if tp > 0 else 0.0,
            "deviation": 20,
            "magic": magic,
            "comment": comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self.get_filling_mode(symbol),
        }

        with self._lock:
            result = mt5.order_send(request)
            if not result or result.retcode != mt5.TRADE_RETCODE_DONE:
                err_msg = result.comment if result else str(mt5.last_error())
                retcode = result.retcode if result else -1
                logger.error(f"❌ Order Send Failed ({symbol} {direction} {lot}L, magic={magic}): {err_msg} (retcode={retcode})")
                return {"success": False, "error": err_msg, "retcode": retcode}

            logger.info(
                f"🚀 ORDER EXECUTED: Ticket #{result.order} | {symbol} {direction} {lot}L @ {result.price} | Magic: {magic}"
            )
            return {
                "success": True,
                "ticket": result.order,
                "price": result.price,
                "volume": result.volume,
                "magic": magic
            }

    def close_position(self, ticket: int) -> Dict[str, Any]:
        """Close an open position by ticket using an opposite deal."""
        if not self.ensure_connected():
            return {"success": False, "error": "Not connected to MT5"}

        with self._lock:
            positions = mt5.positions_get(ticket=ticket)
            if not positions or len(positions) == 0:
                return {"success": False, "error": f"Position #{ticket} not found"}

            pos = positions[0]
            close_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
            tick = mt5.symbol_info_tick(pos.symbol)
            if not tick:
                return {"success": False, "error": f"Failed to get tick for {pos.symbol}"}

            price = tick.bid if pos.type == mt5.ORDER_TYPE_BUY else tick.ask

            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "position": pos.ticket,
                "symbol": pos.symbol,
                "volume": pos.volume,
                "type": close_type,
                "price": price,
                "deviation": 20,
                "magic": pos.magic,
                "comment": "Fleet Close",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": self.get_filling_mode(pos.symbol),
            }

            result = mt5.order_send(request)
            if not result or result.retcode != mt5.TRADE_RETCODE_DONE:
                err_msg = result.comment if result else str(mt5.last_error())
                logger.error(f"❌ Close Failed #{ticket}: {err_msg}")
                return {"success": False, "error": err_msg}

            logger.info(f"✅ POSITION CLOSED: Ticket #{ticket} ({pos.symbol} {pos.volume}L, Profit: ${pos.profit:.2f})")
            return {"success": True, "ticket": ticket, "profit": pos.profit}

    def modify_position(
        self,
        ticket: int,
        sl: Optional[float] = None,
        tp: Optional[float] = None
    ) -> Dict[str, Any]:
        """Modify Stop Loss and/or Take Profit of an open position using mt5.TRADE_ACTION_SLTP."""
        if not self.ensure_connected():
            return {"success": False, "error": "Not connected to MT5"}

        with self._lock:
            positions = mt5.positions_get(ticket=ticket)
            if not positions or len(positions) == 0:
                return {"success": False, "error": f"Position #{ticket} not found"}

            pos = positions[0]
            spec = self.get_symbol_spec(pos.symbol)
            digits = spec["digits"] if spec else 2

            new_sl = round(sl, digits) if sl is not None else float(pos.sl)
            new_tp = round(tp, digits) if tp is not None else float(pos.tp)

            # Avoid redundant modifications if SL and TP haven't changed
            if abs(new_sl - float(pos.sl)) < 1e-5 and abs(new_tp - float(pos.tp)) < 1e-5:
                return {"success": True, "ticket": ticket, "unchanged": True}

            request = {
                "action": mt5.TRADE_ACTION_SLTP,
                "position": pos.ticket,
                "symbol": pos.symbol,
                "sl": new_sl,
                "tp": new_tp,
            }

            result = mt5.order_send(request)
            if not result or result.retcode != mt5.TRADE_RETCODE_DONE:
                err_msg = result.comment if result else str(mt5.last_error())
                retcode = result.retcode if result else -1
                logger.error(
                    f"❌ Modify Position Failed #{ticket} (SL={new_sl}, TP={new_tp}): {err_msg} (retcode={retcode})"
                )
                return {"success": False, "error": err_msg, "retcode": retcode}

            logger.info(
                f"🛡️ POSITION MODIFIED: Ticket #{ticket} | Symbol: {pos.symbol} | New SL: {new_sl} | New TP: {new_tp}"
            )
            return {"success": True, "ticket": ticket, "sl": new_sl, "tp": new_tp}

    def shutdown(self):
        """Cleanly shutdown MT5 connection."""
        with self._lock:
            if MT5_AVAILABLE and self.connected:
                mt5.shutdown()
                self.connected = False
                logger.info("🔌 MT5 connection shut down cleanly.")
