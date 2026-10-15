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
        half_kelly_multiplier: float = 1.0,
        sizing_mode: str = "dynamic_risk",
        min_lot: float = 0.01,
        max_lot: float = 5.0,
        forecast: float = 10.0,
        current_lot: float = 0.0,
        symbol: str = "",
    ) -> float:
        """
        Calculates dynamic order volume:
        1. If sizing_mode == 'carver_systematic':
           Uses Robert Carver (pysystemtrade) Volatility Targeting + IDM Multiplier + Buffering.
        2. If sizing_mode == 'dynamic_risk' and sl_distance > 0:
           Uses Andreas Clenow Volatility Parity & Ernie Chan Half-Kelly:
           Lots = (Equity * risk_per_trade_pct) / dollar_risk_per_lot * half_kelly_multiplier
        3. Scaled by FleetRiskGuard Drawdown Ladder & Profit Lock (1.0x Normal, 0.5x De-risk/Lock, 0.0x Halted).
        4. Clamped strictly between min_lot and effective hard cap (max_lot_gold 0.04, max_lot_btc 0.04, max_lot_fx 0.06).
        """
        multiplier = risk_guard.get_risk_multiplier(account_info)
        if multiplier <= 0.0:
            return 0.0

        # Enforce hard ceiling from Risk Guard if symbol is provided
        if hasattr(risk_guard, "get_max_lot") and symbol:
            effective_max_lot = min(max_lot, risk_guard.get_max_lot(symbol))
        else:
            effective_max_lot = max_lot

        if sizing_mode == "carver_systematic":
            lot, _ = risk_guard.calculate_carver_volume(
                strategy_name=self.name,
                account_info=account_info,
                atr_price=sl_distance if sl_distance > 0 else 1.0,
                contract_size=contract_size,
                forecast=forecast,
                min_lot=min_lot,
                max_lot=effective_max_lot,
                current_lot=current_lot,
            )
            return lot

        if sizing_mode == "dynamic_risk" and sl_distance > 0:
            equity = float(account_info.get("equity", 10000.0))
            # Normalize percentage (e.g., 0.65 -> 0.0065, or 0.005 -> 0.005)
            risk_pct = (risk_per_trade_pct / 100.0) if risk_per_trade_pct > 0.05 else risk_per_trade_pct
            risk_dollar = equity * risk_pct

            # Calculate dollar loss per 1.0 lot at sl_distance
            dollar_per_lot_risk = 0.0
            try:
                import MetaTrader5 as mt5
                term = mt5.terminal_info()
                if term is not None and getattr(term, "connected", False) and symbol:
                    tick = mt5.symbol_info_tick(symbol)
                    if tick:
                        p = tick.ask
                        calc = mt5.order_calc_profit(mt5.ORDER_TYPE_BUY, symbol, 1.0, p, p - sl_distance)
                        if calc is not None and abs(calc) > 0:
                            dollar_per_lot_risk = abs(calc)
            except Exception:
                pass

            if dollar_per_lot_risk <= 0.0 and contract_size > 0:
                dollar_per_lot_risk = sl_distance * contract_size

            if dollar_per_lot_risk > 0:
                raw_lot = (risk_dollar / dollar_per_lot_risk) * half_kelly_multiplier
                target_volume = raw_lot
            else:
                target_volume = base_volume
        else:
            target_volume = base_volume

        scaled = target_volume * multiplier
        bounded = max(min_lot, min(effective_max_lot, scaled))
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
