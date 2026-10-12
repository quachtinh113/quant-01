"""
Unified Quant Fleet — Base Strategy Class
Common contract and lifecycle hooks for all 5 quant strategies.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, List
from datetime import datetime

from core.mt5_connection_manager import MT5ConnectionManager
from core.fleet_risk_guard import FleetRiskGuard
from core.audit_logger import get_logger

logger = get_logger()


class BaseStrategy(ABC):
    def __init__(self, name: str, magic: int, config: Dict[str, Any]):
        self.name = name
        self.magic = magic
        self.config = config
        self.enabled = config.get("enabled", True)
        self.last_decision_time = None
        self.last_action = "INITIALIZED"
        self.open_positions: List[Dict[str, Any]] = []

    def sync_positions(self, mt5_mgr: MT5ConnectionManager) -> List[Dict[str, Any]]:
        """Sync open positions belonging to this strategy's magic number."""
        self.open_positions = mt5_mgr.get_positions(magic=self.magic)
        return self.open_positions

    @abstractmethod
    def should_run_cycle(self, now_utc: datetime) -> bool:
        """Evaluate if the strategy's decision cadence is met."""
        pass

    @abstractmethod
    def evaluate_cycle(
        self,
        now_utc: datetime,
        mt5_mgr: MT5ConnectionManager,
        risk_guard: FleetRiskGuard,
        account_info: Dict[str, Any]
    ) -> None:
        """Run strategy decision cycle (signal generation, entry, position management)."""
        pass

    def calculate_order_volume(
        self,
        base_volume: float,
        risk_guard: FleetRiskGuard,
        account_info: Dict[str, Any],
        sl_distance: float = 0.0,
        contract_size: float = 100.0,
        risk_per_trade_pct: float = 0.005,
        half_kelly_multiplier: float = 0.50,
        sizing_mode: str = "fixed",
        min_lot: float = 0.01,
        max_lot: float = 5.0,
    ) -> float:
        """
        Calculates dynamic order volume:
        1. If sizing_mode == 'dynamic_risk' and sl_distance > 0:
           Uses Andreas Clenow Volatility Parity & Ernie Chan Half-Kelly:
           Lots = (Equity * risk_per_trade_pct) / (sl_distance * contract_size) * half_kelly_multiplier
        2. Scaled by FleetRiskGuard Drawdown Ladder (1.0x Normal, 0.5x De-risk, 0.0x Halted).
        3. Clamped strictly between min_lot and max_lot.
        """
        multiplier = risk_guard.get_risk_multiplier(account_info)
        if multiplier <= 0.0:
            return 0.0

        if sizing_mode == "dynamic_risk" and sl_distance > 0 and contract_size > 0:
            equity = float(account_info.get("equity", 10000.0))
            risk_dollar = equity * risk_per_trade_pct
            dollar_per_lot_risk = sl_distance * contract_size
            if dollar_per_lot_risk > 0:
                raw_lot = risk_dollar / dollar_per_lot_risk
                target_volume = raw_lot * half_kelly_multiplier
            else:
                target_volume = base_volume
        else:
            target_volume = base_volume

        scaled = target_volume * multiplier
        bounded = max(min_lot, min(max_lot, scaled))
        return round(bounded, 2)

    def on_tick_update(
        self,
        now_utc: datetime,
        mt5_mgr: MT5ConnectionManager,
        risk_guard: FleetRiskGuard,
        account_info: Dict[str, Any]
    ) -> None:
        """Optional hook called on every master loop tick for real-time trailing/profit exits."""
        pass

    def get_summary(self) -> Dict[str, Any]:
        """Summary for the live fleet dashboard."""
        total_pnl = sum(p.get("profit", 0.0) for p in self.open_positions)
        return {
            "name": self.name,
            "magic": self.magic,
            "enabled": self.enabled,
            "positions_count": len(self.open_positions),
            "unrealized_pnl": total_pnl,
            "last_action": self.last_action,
            "last_decision_time": self.last_decision_time.strftime("%H:%M:%S") if self.last_decision_time else "Never",
        }
