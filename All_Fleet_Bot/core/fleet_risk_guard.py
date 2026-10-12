"""
Unified Quant Fleet — Fleet Risk Guard
Account-wide Tier-1 circuit breaker and risk governance.
Guards equity, max drawdown, and margin levels across all 5 bots.
"""

import os
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any, Optional

from core.audit_logger import get_logger

logger = get_logger()


class FleetRiskGuard:
    """
    Unified Quant Fleet — Fleet Risk Guard
    Account-wide Tier-1 circuit breaker and risk governance.
    Enforces Institutional Drawdown Ladder (Millennium/Citadel model):
    - Tier 1 (-3% DD): Warning log / telemetry flag
    - Tier 2 (-5% DD): Dynamic De-risking (position sizes cut by 50%)
    - Tier 3 (-7.5% DD or absolute USD cap): Hard Circuit Breaker (Halt all trading)
    """

    def __init__(
        self,
        max_daily_drawdown_usd: float = 300.0,
        max_daily_drawdown_pct: float = 0.075,
        warning_drawdown_pct: float = 0.03,
        derisk_drawdown_pct: float = 0.05,
        min_margin_level_percent: float = 200.0,
        max_total_positions: int = 10,
        halt_file: str = "HALT_FLEET"
    ):
        self.max_daily_drawdown_usd = max_daily_drawdown_usd
        self.max_daily_drawdown_pct = max_daily_drawdown_pct
        self.warning_drawdown_pct = warning_drawdown_pct
        self.derisk_drawdown_pct = derisk_drawdown_pct
        self.min_margin_level_percent = min_margin_level_percent
        self.max_total_positions = max_total_positions
        self.halt_file = Path(halt_file)

        self._daily_peak_equity = 0.0
        self._current_date = ""
        self._is_halted = False
        self._halt_reason = ""
        self._warning_logged = False

    def update_account_state(self, account_info: Dict[str, Any]) -> None:
        """Track daily peak equity and detect reset on new calendar day (UTC)."""
        now_utc = datetime.now(timezone.utc)
        today_str = now_utc.strftime("%Y-%m-%d")

        current_equity = account_info.get("equity", 0.0)

        # Reset peak on new day
        if today_str != self._current_date:
            self._current_date = today_str
            self._daily_peak_equity = current_equity
            self._is_halted = False
            self._halt_reason = ""
            self._warning_logged = False
            logger.info(f"🌅 New trading day {today_str}. Reset daily peak equity to ${self._daily_peak_equity:.2f}")

        # Update high-water mark
        if current_equity > self._daily_peak_equity:
            self._daily_peak_equity = current_equity

    def get_drawdown_metrics(self, account_info: Dict[str, Any]) -> tuple[float, float]:
        """Calculates current drawdown in USD and percentage relative to daily peak."""
        current_equity = account_info.get("equity", 0.0)
        drawdown_usd = max(0.0, self._daily_peak_equity - current_equity)
        drawdown_pct = (drawdown_usd / self._daily_peak_equity) if self._daily_peak_equity > 0 else 0.0
        return drawdown_usd, drawdown_pct

    def get_risk_multiplier(self, account_info: Dict[str, Any]) -> float:
        """
        Returns dynamic sizing multiplier based on Drawdown Ladder:
        1.0 = Normal operations
        0.5 = Tier-2 De-risking (drawdown between 5% and 7.5%)
        0.0 = Tier-3 Halted (drawdown >= 7.5% or USD cap exceeded)
        """
        if self._is_halted:
            return 0.0

        drawdown_usd, drawdown_pct = self.get_drawdown_metrics(account_info)
        if drawdown_usd > self.max_daily_drawdown_usd or drawdown_pct >= self.max_daily_drawdown_pct:
            return 0.0
        if drawdown_pct >= self.derisk_drawdown_pct:
            return 0.5
        return 1.0

    def can_open_position(self, account_info: Dict[str, Any], current_position_count: int) -> tuple[bool, str]:
        """
        Check whether opening a new position is permitted.
        Returns (allowed: bool, reason: str).
        """
        # 1. Manual Kill-Switch Check
        if self.halt_file.exists():
            return False, f"Emergency halt triggered via '{self.halt_file.name}' file."

        # 2. Existing Halt State Check
        if self._is_halted:
            return False, f"Fleet is HALTED: {self._halt_reason}"

        # 3. Max Total Positions Check
        if current_position_count >= self.max_total_positions:
            return False, f"Max fleet positions reached ({current_position_count}/{self.max_total_positions})"

        # 4. Margin Level Guard
        margin_level = account_info.get("margin_level", 0.0)
        # Note: margin_level is 0.0 when no positions are open, so check only when margin > 0
        if account_info.get("margin", 0.0) > 0 and margin_level < self.min_margin_level_percent:
            return False, f"Margin level critical: {margin_level:.1f}% < {self.min_margin_level_percent}% limit"

        # 5. Institutional Drawdown Ladder Guard
        drawdown_usd, drawdown_pct = self.get_drawdown_metrics(account_info)

        # Tier 1: Warning threshold (-3%)
        if drawdown_pct >= self.warning_drawdown_pct and not self._warning_logged:
            self._warning_logged = True
            logger.warning(f"⚠️ DRAWDOWN WARNING: Daily drawdown at {drawdown_pct*100:.2f}% (${drawdown_usd:.2f})")

        # Tier 3 / Hard Cap: USD limit or Percentage limit (-7.5%)
        if drawdown_usd > self.max_daily_drawdown_usd:
            self._is_halted = True
            self._halt_reason = f"Daily drawdown ${drawdown_usd:.2f} exceeded USD limit ${self.max_daily_drawdown_usd:.2f}"
            logger.critical(f"🛑 CIRCUIT BREAKER TRIPPED: {self._halt_reason}")
            return False, self._halt_reason

        if self._daily_peak_equity > 0 and drawdown_pct >= self.max_daily_drawdown_pct:
            self._is_halted = True
            self._halt_reason = f"Daily drawdown {drawdown_pct*100:.2f}% exceeded percentage kill limit {self.max_daily_drawdown_pct*100:.1f}%"
            logger.critical(f"🛑 CIRCUIT BREAKER TRIPPED: {self._halt_reason}")
            return False, self._halt_reason

        return True, "OK"

    def get_status(self, account_info: Dict[str, Any]) -> Dict[str, Any]:
        """Return human-readable risk status summary."""
        drawdown_usd, drawdown_pct = self.get_drawdown_metrics(account_info)
        multiplier = self.get_risk_multiplier(account_info)

        if self._is_halted:
            tier = "TIER_3_HALTED"
        elif drawdown_pct >= self.derisk_drawdown_pct:
            tier = "TIER_2_DERISK"
        elif drawdown_pct >= self.warning_drawdown_pct:
            tier = "TIER_1_WARNING"
        else:
            tier = "NORMAL"

        return {
            "is_halted": self._is_halted,
            "halt_reason": self._halt_reason,
            "daily_peak_equity": self._daily_peak_equity,
            "current_drawdown_usd": drawdown_usd,
            "current_drawdown_pct": round(drawdown_pct * 100.0, 2),
            "drawdown_limit_usd": self.max_daily_drawdown_usd,
            "drawdown_limit_pct": round(self.max_daily_drawdown_pct * 100.0, 1),
            "margin_level": account_info.get("margin_level", 0.0),
            "drawdown_tier": tier,
            "risk_multiplier": multiplier,
        }
