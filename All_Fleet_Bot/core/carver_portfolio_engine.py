"""
Unified Quant Fleet — Carver Systematic Portfolio & Sizing Engine
Inspired by Robert Carver's 'Systematic Trading' (pysystemtrade).
Implements:
1. Volatility Targeting (Annualized Vol -> Daily Cash Volatility Target).
2. Instrument Diversification Multiplier (IDM) for multi-asset cross-hedging.
3. Continuous Forecast Scaling (-20 to +20 signal strength).
4. Position Buffering (Trade buffer to eliminate churn & spread friction).
"""

from typing import Dict, Any, Optional, Tuple
import numpy as np

from core.audit_logger import get_logger

logger = get_logger()


class CarverPortfolioEngine:
    """
    Robert Carver (pysystemtrade / Man AHL) Systematic Portfolio Engine.
    Scales multi-asset positions to target an exact annualized portfolio volatility,
    incorporating instrument correlation benefits via the Instrument Diversification Multiplier (IDM).
    """

    def __init__(
        self,
        annual_target_vol_pct: float = 16.0,
        default_idm: float = 1.40,
        buffer_fraction: float = 0.10,
        trading_days_per_year: int = 256,
        weights: Optional[Dict[str, float]] = None,
    ):
        """
        :param annual_target_vol_pct: Target annual volatility of portfolio equity (default 16.0%).
        :param default_idm: Instrument Diversification Multiplier for 5 uncorrelated asset classes (default 1.40).
        :param buffer_fraction: Rebalancing trade buffer bandwidth (default 10%).
        :param trading_days_per_year: Number of trading days per year (default 256, sqrt(256) = 16).
        :param weights: Nominal portfolio weights per bot/asset.
        """
        self.annual_target_vol_pct = annual_target_vol_pct
        self.annual_vol_target = annual_target_vol_pct / 100.0
        self.daily_vol_factor = np.sqrt(trading_days_per_year)  # 16.0
        self.daily_vol_target = self.annual_vol_target / self.daily_vol_factor  # 1.0% daily for 16% annual
        self.idm = default_idm
        self.buffer_fraction = buffer_fraction
        self.weights = weights or {
            "btc_8h": 0.20,
            "fx_d1": 0.20,
            "gold_sess": 0.25,
            "usidx_sess": 0.15,
            "nowtrading_mtf": 0.20,
        }

    def compute_idm_from_correlation(
        self,
        correlation_matrix: np.ndarray,
        weights: Optional[np.ndarray] = None
    ) -> float:
        """
        Calculates theoretical Instrument Diversification Multiplier (IDM):
        IDM = 1.0 / sqrt(w^T * CorrelationMatrix * w)
        Bounded between 1.0 (perfect correlation) and 2.5 (maximum institutional leverage benefit).
        """
        n = correlation_matrix.shape[0]
        if weights is None:
            w = np.ones(n) / n
        else:
            w = weights / np.sum(weights)

        portfolio_var_factor = float(np.dot(w.T, np.dot(correlation_matrix, w)))
        if portfolio_var_factor <= 0.0:
            return 1.0

        idm = 1.0 / np.sqrt(portfolio_var_factor)
        self.idm = float(np.clip(idm, 1.0, 2.5))
        return self.idm

    def get_cash_vol_target(
        self,
        equity: float,
        strategy_name: str,
        custom_weight: Optional[float] = None
    ) -> float:
        """
        Computes the Daily Cash Volatility Target ($) allocated to a specific strategy:
        Daily Cash Vol Target = Equity * Daily Vol Target * Strategy Weight * IDM
        """
        weight = custom_weight if custom_weight is not None else self.weights.get(strategy_name, 0.20)
        cash_vol = equity * self.daily_vol_target * weight * self.idm
        return max(1.0, cash_vol)

    def calculate_position_size(
        self,
        equity: float,
        strategy_name: str,
        atr_price: float,
        contract_size: float = 100.0,
        point: float = 0.01,
        forecast: float = 10.0,  # Carver convention: +10 is standard buy, -10 is standard sell
        min_lot: float = 0.01,
        max_lot: float = 5.0,
        current_position_lot: float = 0.0,
    ) -> Tuple[float, Dict[str, Any]]:
        """
        Calculates optimal position size in lots following Robert Carver's formula:
        1. Block Value / Value at Risk per Lot = ATR * point_multiplier * contract_size
        2. Unbuffered Target Lots = (Cash Vol Target * (Forecast / 10.0)) / (Daily Risk per Lot)
        3. Apply Carver Position Buffer against current_position_lot to eliminate churn.
        4. Bounds clamping [min_lot, max_lot].
        """
        if equity <= 0 or atr_price <= 0:
            return 0.0, {"error": "Invalid equity or ATR"}

        cash_vol_target = self.get_cash_vol_target(equity, strategy_name)

        # Dollar risk per 1 full contract per day
        dollar_risk_per_unit = atr_price * contract_size
        if dollar_risk_per_unit <= 0:
            return min_lot, {"error": "Zero dollar risk per unit"}

        # Carver forecast multiplier: 10 is baseline (+1.0x), 20 is max (+2.0x)
        forecast_scalar = np.clip(forecast / 10.0, -2.0, 2.0)
        direction = 1.0 if forecast_scalar >= 0 else -1.0
        magnitude = abs(forecast_scalar)

        target_lots_raw = (cash_vol_target * magnitude) / dollar_risk_per_unit

        # Apply Carver buffer: if new target is close to current position, don't change
        target_lots_buffered = self.apply_trade_buffer(
            current_lot=current_position_lot,
            target_lot=target_lots_raw
        )

        final_lots = float(np.clip(target_lots_buffered, min_lot, max_lot))
        rounded_lots = round(final_lots, 2)

        metadata = {
            "strategy": strategy_name,
            "equity": equity,
            "cash_vol_target": round(cash_vol_target, 2),
            "dollar_risk_per_unit": round(dollar_risk_per_unit, 2),
            "forecast_scalar": round(forecast_scalar, 2),
            "raw_target_lots": round(target_lots_raw, 3),
            "buffered_lots": rounded_lots,
            "idm": self.idm,
            "annual_target_vol_pct": self.annual_target_vol_pct,
        }
        return rounded_lots, metadata

    def apply_trade_buffer(
        self,
        current_lot: float,
        target_lot: float
    ) -> float:
        """
        Robert Carver Position Buffering:
        If difference between target and current position is within buffer bandwidth,
        do NOT trade to save commission, spread, and market impact.
        """
        if current_lot <= 0.0:
            return target_lot

        diff = abs(target_lot - current_lot)
        buffer_threshold = self.buffer_fraction * current_lot

        if diff <= buffer_threshold:
            # Retain current position (no churn)
            return current_lot
        return target_lot
