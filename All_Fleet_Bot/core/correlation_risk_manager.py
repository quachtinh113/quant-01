"""
=====================================================================
UNIFIED QUANT FLEET — PORTFOLIO CORRELATION & EXPOSURE GATEKEEPER
Institutional Multi-Pod Quant Architecture:
Controls cross-asset USD directional exposure and position overlap.
Guards against extreme USD Net Short / Net Long amplification across
Forex Majors (EURUSD, GBPUSD, USDJPY) and Commodities (XAUUSD).
=====================================================================
"""

import os
from typing import List, Dict, Any, Optional, Tuple
from core.audit_logger import get_logger

logger = get_logger()

# Base USD directional weight per instrument:
# When BUYING the instrument:
# - Quote USD pairs (EURUSD, XAUUSD): BUYing asset = Selling USD (-1.0 USD exposure)
# - Base USD pairs (USDJPY, USDCAD):  BUYing asset = Buying USD  (+1.0 USD exposure)
# - Independent / Crypto (BTCUSD):    Hệ số USD = 0 (Crypto rủi ro riêng biệt)
USD_EXPOSURE_WEIGHTS: Dict[str, float] = {
    # Base / USD (BUY = Short USD, SELL = Long USD)
    "EURUSD": -1.0,
    "GBPUSD": -1.0,
    "AUDUSD": -1.0,
    "NZDUSD": -1.0,
    "XAUUSD": -1.0,
    "XAGUSD": -1.0,

    # USD / Quote (BUY = Long USD, SELL = Short USD)
    "USDJPY": 1.0,
    "USDCAD": 1.0,
    "USDCHF": 1.0,

    # Crypto / Independent Risk Assets
    "BTCUSD": 0.0,
    "ETHUSD": 0.0,
    "US500": 0.0,
    "USTEC": 0.0,
    "US30": 0.0,
}


class CorrelationRiskManager:
    """
    Portfolio Correlation & Exposure Gatekeeper.
    Enforces cross-pod correlation constraints and caps maximum net USD exposure.
    Prevents simultaneous multi-asset USD concentration (e.g. BUY XAUUSD + BUY EURUSD + SELL USDJPY).
    """

    def __init__(self, max_net_usd_units: int = 2):
        self.max_net_usd_units = max_net_usd_units
        self.weights = dict(USD_EXPOSURE_WEIGHTS)
        logger.info(f"🛡️ Correlation & Exposure Gatekeeper ACTIVE (Max Net USD Units: {self.max_net_usd_units})")

    @staticmethod
    def get_canonical_symbol(symbol: str) -> str:
        """Strip broker suffixes (e.g., 'XAUUSDm' -> 'XAUUSD', 'EURUSD.pro' -> 'EURUSD')."""
        canonical = symbol.strip()
        for suffix in ["m", ".pro", "c", "_i", ".a", ".raw"]:
            if canonical.endswith(suffix):
                canonical = canonical[:-len(suffix)]
                break
        return canonical.upper()

    def get_usd_impact(self, symbol: str, order_type: str) -> float:
        """
        Calculate incremental USD exposure impact of an order.
        BUY  direction multiplier = +1.0
        SELL direction multiplier = -1.0
        Returns: exposure units (-1.0, 0.0, +1.0)
        """
        canonical = self.get_canonical_symbol(symbol)
        base_weight = self.weights.get(canonical, 0.0)

        direction = str(order_type).upper()
        if direction in ["BUY", "ORDER_TYPE_BUY", "0"]:
            dir_mult = 1.0
        elif direction in ["SELL", "ORDER_TYPE_SELL", "1"]:
            dir_mult = -1.0
        else:
            dir_mult = 0.0

        return base_weight * dir_mult

    def calculate_net_usd_exposure(self, open_positions: Optional[List[Any]] = None) -> float:
        """
        Sum aggregate USD exposure units across all currently open positions.
        Supports position dicts, MT5 Position tuples, or custom objects.
        """
        if not open_positions:
            return 0.0

        total_net_usd = 0.0
        for pos in open_positions:
            sym = ""
            pos_type = ""

            if isinstance(pos, dict):
                sym = pos.get("symbol", "")
                pos_type = pos.get("type", "")
            else:
                sym = getattr(pos, "symbol", "")
                raw_type = getattr(pos, "type", None)
                if raw_type == 0:
                    pos_type = "BUY"
                elif raw_type == 1:
                    pos_type = "SELL"
                else:
                    pos_type = str(raw_type)

            if sym:
                total_net_usd += self.get_usd_impact(sym, pos_type)

        return round(total_net_usd, 2)

    def can_execute_order(
        self,
        symbol: str,
        order_type: str,
        open_positions: Optional[List[Any]] = None
    ) -> Tuple[bool, str]:
        """
        Pre-Trade Gatekeeper Validation:
        Checks whether opening a new position would violate the Max Net USD exposure threshold.
        Returns: (allowed: bool, reason: str)
        """
        order_impact = self.get_usd_impact(symbol, order_type)

        # Asset has zero direct USD exposure weight (e.g. BTCUSD, Indices)
        if order_impact == 0.0:
            return True, f"Independent asset ({symbol}): USD exposure impact is 0"

        current_net_usd = self.calculate_net_usd_exposure(open_positions)
        projected_net_usd = round(current_net_usd + order_impact, 2)

        if abs(projected_net_usd) > self.max_net_usd_units:
            direction_str = "Long USD" if projected_net_usd > 0 else "Short USD"
            reason = (
                f"USD exposure limit exceeded: projected net {projected_net_usd:+.1f} units ({direction_str}) "
                f"> max ±{self.max_net_usd_units} units (Current: {current_net_usd:+.1f})"
            )
            logger.warning(f"⚠️ [RISK GATEKEEPER] Order blocked for {symbol} ({order_type}): {reason}")
            return False, reason

        return True, f"Approved (projected net USD: {projected_net_usd:+.1f} / ±{self.max_net_usd_units})"

    def get_exposure_summary(self, open_positions: Optional[List[Any]] = None) -> Dict[str, Any]:
        """Telemetry snapshot of current portfolio USD concentration."""
        net_usd = self.calculate_net_usd_exposure(open_positions)
        headroom_long = max(0.0, self.max_net_usd_units - net_usd)
        headroom_short = max(0.0, self.max_net_usd_units + net_usd)

        return {
            "net_usd_units": net_usd,
            "max_net_usd_units": self.max_net_usd_units,
            "status": "NORMAL" if abs(net_usd) <= self.max_net_usd_units else "LIMIT_EXCEEDED",
            "headroom_long_usd": headroom_long,
            "headroom_short_usd": headroom_short,
        }
