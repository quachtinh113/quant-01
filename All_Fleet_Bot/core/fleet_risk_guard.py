"""
Unified Quant Fleet — Fleet Risk Guard
Account-wide Tier-1 circuit breaker and risk governance.
Guards equity, max drawdown, and margin levels across all 5 bots.
"""

import os
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any, Optional, Tuple, List

from core.audit_logger import get_logger
from core.carver_portfolio_engine import CarverPortfolioEngine

logger = get_logger()


class FleetRiskGuard:
    """
    Unified Quant Fleet — Fleet Risk Guard
    Account-wide Tier-1 circuit breaker and risk governance.
    Enforces Institutional Drawdown Ladder (Millennium/Citadel model)
    and Robert Carver Systematic Portfolio Sizing (pysystemtrade model):
    - Tier 1 (-3% DD): Warning log / telemetry flag
    - Tier 2 (-5% DD): Dynamic De-risking (position sizes cut by 50%)
    - Tier 3 (-7.5% DD or absolute USD cap): Hard Circuit Breaker (Halt all trading)
    - Carver Engine: Multi-asset Volatility Targeting & IDM diversification.
    """

    def __init__(
        self,
        max_daily_drawdown_usd: float = 150.0,
        max_daily_drawdown_pct: float = 0.075,
        warning_drawdown_pct: float = 0.03,
        derisk_drawdown_pct: float = 0.05,
        daily_profit_target_pct: float = 0.015,
        daily_profit_target_usd: float = 150.0,
        max_daily_loss_pct: Optional[float] = None,
        max_daily_loss_usd: Optional[float] = None,
        max_total_open_risk_pct: float = 0.02,
        min_margin_level_percent: float = 200.0,
        max_total_positions: int = 10,
        halt_file: str = "HALT_FLEET",
        annual_target_vol_pct: float = 16.0,
        idm: float = 1.40,
        max_lot_gold: float = 0.04,
        max_lot_btc: float = 0.04,
        max_lot_fx: float = 0.06,
    ):
        self.max_daily_drawdown_usd = max_daily_drawdown_usd
        self.max_daily_drawdown_pct = max_daily_drawdown_pct
        self.warning_drawdown_pct = warning_drawdown_pct
        self.derisk_drawdown_pct = derisk_drawdown_pct
        
        # Daily PnL Circuit Breaker (-1.5% / -$150) & Profit Lock (+1.5% / +$150)
        self.max_daily_loss_pct = max_daily_loss_pct if max_daily_loss_pct is not None else max_daily_drawdown_pct
        self.max_daily_loss_usd = max_daily_loss_usd if max_daily_loss_usd is not None else max_daily_drawdown_usd
        self.daily_profit_target_pct = daily_profit_target_pct
        self.daily_profit_target_usd = daily_profit_target_usd
        self.max_total_open_risk_pct = max_total_open_risk_pct

        # Hard lot caps
        self.max_lot_gold = max_lot_gold
        self.max_lot_btc = max_lot_btc
        self.max_lot_fx = max_lot_fx

        self.min_margin_level_percent = min_margin_level_percent
        self.max_total_positions = max_total_positions
        self.halt_file = Path(halt_file)

        self.carver_engine = CarverPortfolioEngine(
            annual_target_vol_pct=annual_target_vol_pct,
            default_idm=idm,
            buffer_fraction=0.10
        )

        self._daily_peak_equity = 0.0
        self._start_of_day_equity = 0.0
        self._daily_pnl_usd = 0.0
        self._daily_pnl_pct = 0.0
        self._current_date = ""
        self._is_halted = False
        self._halt_reason = ""
        self._warning_logged = False

    def get_max_lot(self, symbol: str) -> float:
        """Return hard lot ceiling for specific instrument class."""
        sym = (symbol or "").upper()
        if "XAU" in sym or "GOLD" in sym or "XAG" in sym:
            return self.max_lot_gold
        elif "BTC" in sym:
            return self.max_lot_btc
        else:
            return self.max_lot_fx

    def update_account_state(self, account_info: Dict[str, Any]) -> None:
        """Track daily peak equity, baseline equity, and closed+floating PnL."""
        now_utc = datetime.now(timezone.utc)
        today_str = now_utc.strftime("%Y-%m-%d")

        current_equity = account_info.get("equity", 0.0)

        # Reset peak and baseline on new calendar day (UTC) or first run
        if today_str != self._current_date or self._start_of_day_equity == 0.0:
            self._current_date = today_str
            self._daily_peak_equity = current_equity
            self._is_halted = False
            self._halt_reason = ""
            self._warning_logged = False

            # Retrieve today's closed deals from MT5 if connected to establish true day baseline
            start_equity = current_equity
            try:
                import MetaTrader5 as mt5
                term = mt5.terminal_info()
                if term is not None and getattr(term, "connected", False):
                    today_start = datetime(now_utc.year, now_utc.month, now_utc.day, tzinfo=timezone.utc)
                    deals = mt5.history_deals_get(today_start, now_utc)
                    if deals:
                        closed_pnl_today = sum(
                            float(d.profit + getattr(d, 'swap', 0.0) + getattr(d, 'commission', 0.0))
                            for d in deals if getattr(d, 'entry', -1) == 1
                        )
                        current_balance = float(account_info.get("balance", current_equity))
                        start_equity = current_balance - closed_pnl_today
            except Exception:
                pass

            self._start_of_day_equity = start_equity if start_equity > 0 else current_equity
            logger.info(
                f"🌅 Trading day {today_str}. Daily Baseline Equity set to ${self._start_of_day_equity:.2f} "
                f"(Peak: ${self._daily_peak_equity:.2f})"
            )

        # Update high-water mark
        if current_equity > self._daily_peak_equity:
            self._daily_peak_equity = current_equity

        # Update Daily PnL (Closed + Floating)
        if self._start_of_day_equity > 0:
            self._daily_pnl_usd = current_equity - self._start_of_day_equity
            self._daily_pnl_pct = self._daily_pnl_usd / self._start_of_day_equity
        else:
            self._daily_pnl_usd = 0.0
            self._daily_pnl_pct = 0.0

    def get_drawdown_metrics(self, account_info: Dict[str, Any]) -> tuple[float, float]:
        """Calculates current drawdown in USD and percentage relative to daily peak."""
        current_equity = account_info.get("equity", 0.0)
        drawdown_usd = max(0.0, self._daily_peak_equity - current_equity)
        drawdown_pct = (drawdown_usd / self._daily_peak_equity) if self._daily_peak_equity > 0 else 0.0
        return drawdown_usd, drawdown_pct

    def get_risk_multiplier(self, account_info: Dict[str, Any]) -> float:
        """
        Returns dynamic sizing multiplier based on Risk Governance:
        0.0 = Circuit Breaker Halted (daily loss >= limit or drawdown >= limit)
        0.5 = Profit Lock / De-risk (daily profit >= +1.5% or drawdown in Tier-2)
        1.0 = Normal operations
        """
        if self._is_halted:
            return 0.0

        drawdown_usd, drawdown_pct = self.get_drawdown_metrics(account_info)
        if drawdown_usd > self.max_daily_drawdown_usd or drawdown_pct >= self.max_daily_drawdown_pct:
            return 0.0

        # Daily loss circuit breaker (-$150 or -1.5%)
        if self._daily_pnl_usd <= -self.max_daily_loss_usd or (self._start_of_day_equity > 0 and self._daily_pnl_pct <= -self.max_daily_loss_pct):
            return 0.0

        # Daily profit target lock (+1.5% / +$150): cut new order sizes by 50% to protect gains
        if self._daily_pnl_usd >= self.daily_profit_target_usd or (self._start_of_day_equity > 0 and self._daily_pnl_pct >= self.daily_profit_target_pct):
            return 0.5

        if drawdown_pct >= self.derisk_drawdown_pct:
            return 0.5

        return 1.0

    def can_open_position(
        self,
        account_info: Dict[str, Any],
        current_position_count: int,
        open_positions: Optional[List[Dict[str, Any]]] = None
    ) -> tuple[bool, str]:
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

        # 5. Daily PnL Circuit Breaker (Closed + Floating) Check (-1.5% / -$150)
        if self._daily_pnl_usd <= -self.max_daily_loss_usd or (self._start_of_day_equity > 0 and self._daily_pnl_pct <= -self.max_daily_loss_pct):
            self._is_halted = True
            self._halt_reason = (
                f"Daily drawdown kill limit reached: Daily PnL ${self._daily_pnl_usd:.2f} "
                f"({self._daily_pnl_pct*100:.2f}%) <= -${self.max_daily_loss_usd:.2f} "
                f"(-{self.max_daily_loss_pct*100:.1f}%)"
            )
            logger.critical(f"🛑 CIRCUIT BREAKER TRIPPED: {self._halt_reason}")
            return False, self._halt_reason

        # 6. Institutional Drawdown Ladder Guard
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

        # 7. Total Open Risk Guard (max_total_open_risk_pct = 2.0%)
        if open_positions:
            equity = float(account_info.get("equity", 10000.0))
            total_open_risk = 0.0
            for pos in open_positions:
                vol = float(pos.get("volume", 0.0))
                price_open = float(pos.get("price_open", 0.0))
                sl = float(pos.get("sl", 0.0))
                if sl > 0 and price_open > 0:
                    dist = abs(price_open - sl)
                    sym = pos.get("symbol", "")
                    c_size = 100.0 if "XAU" in sym else (1.0 if "BTC" in sym else 100000.0)
                    total_open_risk += dist * c_size * vol
            if equity > 0 and (total_open_risk / equity) >= self.max_total_open_risk_pct:
                return False, f"Max total open risk reached: {total_open_risk/equity*100:.2f}% >= {self.max_total_open_risk_pct*100:.1f}% limit"

        return True, "OK"

    def get_status(self, account_info: Dict[str, Any]) -> Dict[str, Any]:
        """Return human-readable risk status summary."""
        drawdown_usd, drawdown_pct = self.get_drawdown_metrics(account_info)
        multiplier = self.get_risk_multiplier(account_info)

        if self._is_halted:
            tier = "TIER_3_HALTED"
        elif self._daily_pnl_usd >= self.daily_profit_target_usd or (self._start_of_day_equity > 0 and self._daily_pnl_pct >= self.daily_profit_target_pct):
            tier = "PROFIT_LOCKED_0.5X"
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
            "daily_baseline_equity": self._start_of_day_equity,
            "daily_pnl_usd": round(self._daily_pnl_usd, 2),
            "daily_pnl_pct": round(self._daily_pnl_pct * 100.0, 2),
            "current_drawdown_usd": drawdown_usd,
            "current_drawdown_pct": round(drawdown_pct * 100.0, 2),
            "drawdown_limit_usd": self.max_daily_drawdown_usd,
            "drawdown_limit_pct": round(self.max_daily_drawdown_pct * 100.0, 1),
            "margin_level": account_info.get("margin_level", 0.0),
            "drawdown_tier": tier,
            "risk_multiplier": multiplier,
            "carver_idm": self.carver_engine.idm,
            "carver_annual_vol_pct": self.carver_engine.annual_target_vol_pct,
            "max_lot_gold": self.max_lot_gold,
            "max_lot_btc": self.max_lot_btc,
            "max_lot_fx": self.max_lot_fx,
        }

    def calculate_carver_volume(
        self,
        strategy_name: str,
        account_info: Dict[str, Any],
        atr_price: float,
        contract_size: float = 100.0,
        point: float = 0.01,
        forecast: float = 10.0,
        min_lot: float = 0.01,
        max_lot: float = 5.0,
        current_lot: float = 0.0,
    ) -> Tuple[float, Dict[str, Any]]:
        """
        Calculates position volume using Carver Systematic Sizing and scales by Drawdown Ladder.
        """
        equity = float(account_info.get("equity", 10000.0))
        multiplier = self.get_risk_multiplier(account_info)
        if multiplier <= 0.0:
            return 0.0, {"halted": True, "reason": "Risk Desk Multiplier is 0.0"}

        carver_lot, meta = self.carver_engine.calculate_position_size(
            equity=equity,
            strategy_name=strategy_name,
            atr_price=atr_price,
            contract_size=contract_size,
            point=point,
            forecast=forecast,
            min_lot=min_lot,
            max_lot=max_lot,
            current_position_lot=current_lot,
        )

        scaled_lot = carver_lot * multiplier
        final_lot = max(min_lot, min(max_lot, round(scaled_lot, 2)))
        meta["drawdown_multiplier"] = multiplier
        meta["final_lot"] = final_lot
        return final_lot, meta
