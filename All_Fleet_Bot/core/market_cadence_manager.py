"""
=====================================================================
UNIFIED QUANT FLEET — MARKET CADENCE & SESSION HOLIDAY MANAGER
Institutional 24/7 Resilient Runtime:
1. Distinguishes 24/7 Crypto markets (BTC) from weekend-closing markets (FX, Gold, Indices).
2. Detects Weekend Trading Halts (Friday 21:00 UTC -> Sunday 21:00 UTC).
3. Detects Global Market Holidays (New Year, Christmas, Easter/Good Friday).
4. Checks real-time broker trade modes via MetaTrader 5 API.
5. Dynamically regulates CPU/RAM polling interval (15s active -> 60s idle).
=====================================================================
"""

from datetime import datetime, timezone
from typing import Tuple, Dict, Any, Optional, List

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    mt5 = None
    MT5_AVAILABLE = False

from core.audit_logger import get_logger

logger = get_logger()

# Known major global market holidays (Month, Day) where FX/Metals are universally closed
FIXED_MARKET_HOLIDAYS = {
    (1, 1): "New Year's Day",
    (12, 25): "Christmas Day",
}

# Easter Good Friday dates for multi-year coverage
GOOD_FRIDAY_DATES = {
    (2025, 4, 18): "Good Friday 2025",
    (2026, 4, 3): "Good Friday 2026",
    (2027, 3, 26): "Good Friday 2027",
}


class MarketCadenceManager:
    """
    Manages trading sessions, holiday calendars, and weekend market closures.
    Prevents error spam during non-trading hours and cools down system resource usage.
    """

    def __init__(self, weekend_close_hour_utc: int = 21, weekend_open_hour_utc: int = 21):
        self.weekend_close_hour = weekend_close_hour_utc  # Friday 21:00 UTC
        self.weekend_open_hour = weekend_open_hour_utc    # Sunday 21:00 UTC
        logger.info("📅 Market Cadence & Holiday Manager INITIALIZED (24/7 Crypto + FX/Metals Session Guard).")

    @staticmethod
    def is_crypto_symbol(symbol: str) -> bool:
        """Return True if symbol is a 24/7 cryptocurrency."""
        sym = (symbol or "").upper()
        return any(c in sym for c in ["BTC", "ETH", "SOL", "XRP", "CRYPTO"])

    def is_weekend_market_closed(self, dt_utc: Optional[datetime] = None) -> Tuple[bool, str]:
        """
        Check if traditional markets are closed for the weekend.
        Weekend window: Friday 21:00 UTC -> Sunday 21:00 UTC.
        """
        now = dt_utc or datetime.now(timezone.utc)
        weekday = now.weekday()  # Monday=0, ..., Friday=4, Saturday=5, Sunday=6
        hour = now.hour

        # Saturday: Closed 24 hours
        if weekday == 5:
            return True, "Weekend: Saturday (Global markets closed)"

        # Friday night after 21:00 UTC: NY session close
        if weekday == 4 and hour >= self.weekend_close_hour:
            return True, f"Weekend: Friday close after {self.weekend_close_hour}:00 UTC"

        # Sunday before 21:00 UTC: Before Sydney/Tokyo open
        if weekday == 6 and hour < self.weekend_open_hour:
            return True, f"Weekend: Sunday before {self.weekend_open_hour}:00 UTC open"

        return False, "Market hours active"

    @staticmethod
    def is_market_holiday(dt_utc: Optional[datetime] = None) -> Tuple[bool, str]:
        """Check if date falls on a major global market holiday."""
        now = dt_utc or datetime.now(timezone.utc)
        key_md = (now.month, now.day)
        key_ymd = (now.year, now.month, now.day)

        if key_md in FIXED_MARKET_HOLIDAYS:
            return True, f"Holiday: {FIXED_MARKET_HOLIDAYS[key_md]}"

        if key_ymd in GOOD_FRIDAY_DATES:
            return True, f"Holiday: {GOOD_FRIDAY_DATES[key_ymd]}"

        # Christmas Eve early close (Dec 24 after 18:00 UTC)
        if now.month == 12 and now.day == 24 and now.hour >= 18:
            return True, "Holiday: Christmas Eve Early Close"

        # New Year's Eve early close (Dec 31 after 18:00 UTC)
        if now.month == 12 and now.day == 31 and now.hour >= 18:
            return True, "Holiday: New Year's Eve Early Close"

        return False, "Not a market holiday"

    def is_symbol_trading_open(
        self,
        symbol: str,
        dt_utc: Optional[datetime] = None,
        mt5_mgr: Optional[Any] = None
    ) -> Tuple[bool, str]:
        """
        Determine if trading is currently permitted for a specific instrument.
        - Crypto: Always open (24/7/365).
        - FX / Metals / Indices: Closed on weekends, holidays, or broker disablement.
        """
        # 1. Crypto is always open 24/7
        if self.is_crypto_symbol(symbol):
            return True, "24/7 Crypto trading active"

        now = dt_utc or datetime.now(timezone.utc)

        # 2. Weekend calendar filter
        is_wknd, wknd_reason = self.is_weekend_market_closed(now)
        if is_wknd:
            return False, wknd_reason

        # 3. Holiday calendar filter
        is_hday, hday_reason = self.is_market_holiday(now)
        if is_hday:
            return False, hday_reason

        # 4. Broker MT5 API status verification (if connected)
        if mt5_mgr is not None and mt5_mgr.connected and MT5_AVAILABLE:
            try:
                resolved = mt5_mgr.resolve_symbol(symbol) or symbol
                sym_info = mt5.symbol_info(resolved)
                if sym_info is not None:
                    # Check trade mode (0 = DISABLED, 3 = CLOSEONLY)
                    if sym_info.trade_mode == mt5.SYMBOL_TRADE_MODE_DISABLED:
                        return False, "Broker trade mode: DISABLED"
                    if sym_info.trade_mode == mt5.SYMBOL_TRADE_MODE_CLOSEONLY:
                        return False, "Broker trade mode: CLOSE_ONLY"
            except Exception:
                pass

        return True, "Market trading open"

    def is_strategy_active_in_session(
        self,
        strategy_name: str,
        dt_utc: Optional[datetime] = None,
        mt5_mgr: Optional[Any] = None
    ) -> Tuple[bool, str]:
        """
        Check if a given strategy's market is active.
        BTC_8H -> Always active.
        FX_D1 -> Depends on EURUSDm.
        GOLD_SESS -> Depends on XAUUSDm.
        """
        name_lower = strategy_name.lower()
        if "btc" in name_lower or "crypto" in name_lower:
            return True, "24/7 Crypto active"

        if "gold" in name_lower or "xau" in name_lower:
            return self.is_symbol_trading_open("XAUUSDm", dt_utc, mt5_mgr)

        if "fx" in name_lower:
            return self.is_symbol_trading_open("EURUSDm", dt_utc, mt5_mgr)

        if "usidx" in name_lower:
            return self.is_symbol_trading_open("US500m", dt_utc, mt5_mgr)

        # Default fallback
        return self.is_symbol_trading_open("EURUSDm", dt_utc, mt5_mgr)

    def get_adaptive_loop_interval(
        self,
        dt_utc: Optional[datetime] = None,
        base_interval: int = 15,
        idle_interval: int = 60
    ) -> int:
        """
        Cool down polling interval during weekends or holidays when traditional markets are closed.
        Returns:
            base_interval (e.g. 15s) during normal market hours.
            idle_interval (e.g. 60s) during weekends or major holidays.
        """
        now = dt_utc or datetime.now(timezone.utc)
        is_wknd, _ = self.is_weekend_market_closed(now)
        if is_wknd:
            return idle_interval

        is_hday, _ = self.is_market_holiday(now)
        if is_hday:
            return idle_interval

        return base_interval
