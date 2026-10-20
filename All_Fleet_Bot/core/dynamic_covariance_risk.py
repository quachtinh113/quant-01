"""
=====================================================================
UNIFIED QUANT FLEET — DYNAMIC COVARIANCE & CORRELATION GATEKEEPER
Institutional Multi-Pod Quant Architecture:
Employs the Ledoit-Wolf Shrinkage Covariance Matrix estimator
to mitigate sample noise in finite return windows (T x N):
    Sigma_LW = (1 - delta*) * S + delta* * F
Evaluates:
  1. Pairwise Dynamic Rolling Correlation Thresholds (Regime-Aware)
  2. Projected Portfolio Volatility: sigma_p = sqrt(w^T * Sigma_LW * w)
=====================================================================
"""

import os
import threading
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Tuple

import numpy as np
import pandas as pd
from sklearn.covariance import LedoitWolf

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    mt5 = None
    MT5_AVAILABLE = False

from core.audit_logger import get_logger

logger = get_logger()

DEFAULT_FLEET_SYMBOLS = [
    "XAUUSDm",
    "BTCUSDm",
    "EURUSDm",
    "GBPUSDm",
    "USDJPYm",
    "AUDUSDm",
    "USDCADm"
]


class DynamicCovarianceRiskManager:
    """
    Dynamic Covariance & Correlation Gatekeeper using Ledoit-Wolf Shrinkage.
    Monitors rolling return correlations and caps portfolio variance expansion.
    """

    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        timeframe: str = "H1",
        lookback: int = 90,
        max_portfolio_vol: float = 0.035,
        max_pairwise_corr: float = 0.75,
        update_interval_seconds: int = 3600,
        mt5_mgr: Optional[Any] = None
    ):
        """
        :param symbols: List of portfolio symbols tracked.
        :param timeframe: Candle timeframe for return calculation (default 'H1').
        :param lookback: Number of rolling bars (T) for returns matrix.
        :param max_portfolio_vol: Maximum allowable projected portfolio volatility.
        :param max_pairwise_corr: Maximum allowable absolute correlation for concurrent trades.
        :param update_interval_seconds: Cache duration before re-estimating covariance (default 1h).
        :param mt5_mgr: Optional MT5ConnectionManager instance for rates fetching.
        """
        self.symbols = [s for s in (symbols or DEFAULT_FLEET_SYMBOLS)]
        self.timeframe = timeframe
        self.lookback = lookback
        self.max_portfolio_vol = max_portfolio_vol
        self.max_pairwise_corr = max_pairwise_corr
        self.update_interval_seconds = update_interval_seconds
        self.mt5_mgr = mt5_mgr

        self._lock = threading.RLock()
        self.cov_matrix: Optional[pd.DataFrame] = None
        self.corr_matrix: Optional[pd.DataFrame] = None
        self.last_update: Optional[datetime] = None
        self.shrinkage_intensity: float = 0.0

        logger.info(
            f"🧠 Dynamic Ledoit-Wolf Covariance Engine INITIALIZED "
            f"({len(self.symbols)} symbols, lookback={self.lookback} {self.timeframe}, "
            f"max_corr={self.max_pairwise_corr:.2f}, max_vol={self.max_portfolio_vol:.3f})"
        )

    def clean_symbol(self, symbol: str) -> str:
        """Normalize symbol to match portfolio symbols catalog."""
        if not symbol:
            return ""
        s_clean = symbol.strip()
        # Direct match
        if s_clean in self.symbols:
            return s_clean
        # Match case-insensitive
        for s in self.symbols:
            if s.upper() == s_clean.upper():
                return s
        # Match by base name stripping suffixes (m, .pro, c, etc.)
        base = s_clean.upper()
        for suffix in ["M", ".PRO", "C", "_I", ".A", ".RAW"]:
            if base.endswith(suffix):
                base = base[:-len(suffix)]
                break
        for s in self.symbols:
            s_base = s.upper()
            for suffix in ["M", ".PRO", "C", "_I", ".A", ".RAW"]:
                if s_base.endswith(suffix):
                    s_base = s_base[:-len(suffix)]
                    break
            if s_base == base:
                return s
        return s_clean

    def update_covariance_matrix(self) -> bool:
        """
        Fetch rolling returns from MT5 and calculate the Ledoit-Wolf Shrinkage
        Covariance and Correlation Matrices.
        """
        with self._lock:
            returns_dict = {}

            # Attempt fetching via MT5ConnectionManager if provided
            for sym in self.symbols:
                closes = None
                if self.mt5_mgr is not None:
                    df = self.mt5_mgr.get_rates(sym, self.timeframe, count=self.lookback + 1)
                    if df is not None and len(df) >= self.lookback:
                        closes = pd.Series(df['close'].values)

                # Fallback to direct MT5 copy_rates if mt5_mgr not provided or failed
                if closes is None and MT5_AVAILABLE:
                    try:
                        tf_const = mt5.TIMEFRAME_H1 if self.timeframe.upper() == "H1" else mt5.TIMEFRAME_D1
                        rates = mt5.copy_rates_from_pos(sym, tf_const, 0, self.lookback + 1)
                        if rates is not None and len(rates) >= self.lookback:
                            closes = pd.Series([r['close'] for r in rates])
                    except Exception:
                        closes = None

                if closes is None or len(closes) < self.lookback:
                    logger.warning(f"⚠️ [DYN COV] Insufficient rate data for {sym} (Need {self.lookback} bars).")
                    continue

                # Compute log returns: r_t = ln(P_t / P_{t-1})
                ret = np.log(closes / closes.shift(1)).dropna()
                returns_dict[sym] = ret.values[-self.lookback:]

            # Require data for at least 3 core symbols to estimate
            if len(returns_dict) < min(3, len(self.symbols)):
                logger.warning(
                    f"⚠️ [DYN COV] Covariance estimation deferred: only {len(returns_dict)}/{len(self.symbols)} symbols available."
                )
                return False

            active_syms = list(returns_dict.keys())
            df_returns = pd.DataFrame(returns_dict, columns=active_syms)

            try:
                lw = LedoitWolf(store_precision=False, assume_centered=False)
                lw.fit(df_returns.values)

                self.cov_matrix = pd.DataFrame(lw.covariance_, index=active_syms, columns=active_syms)
                self.shrinkage_intensity = float(lw.shrinkage_)

                # Correlation Matrix: C_ij = Sigma_ij / (sigma_i * sigma_j)
                stds = np.sqrt(np.diag(lw.covariance_))
                stds_safe = np.where(stds > 1e-12, stds, 1e-12)
                corr = lw.covariance_ / np.outer(stds_safe, stds_safe)
                corr = np.clip(corr, -1.0, 1.0)
                self.corr_matrix = pd.DataFrame(corr, index=active_syms, columns=active_syms)

                self.last_update = datetime.now(timezone.utc)
                logger.info(
                    f"📊 [DYN COV] Ledoit-Wolf Shrinkage Covariance updated successfully: "
                    f"{len(active_syms)} assets | Shrinkage intensity (delta*): {self.shrinkage_intensity:.4f}"
                )
                return True

            except Exception as e:
                logger.error(f"❌ [DYN COV] Ledoit-Wolf fitting failed: {e}", exc_info=True)
                return False

    def can_execute_order(
        self,
        target_symbol: str,
        target_signal: str,
        open_positions: Optional[List[Any]] = None
    ) -> Tuple[bool, str]:
        """
        Pre-Trade Gatekeeper Validation:
        1. Pairwise Dynamic Correlation (Prevents concurrent positions in highly co-dependent pairs)
        2. Projected Portfolio Volatility: sigma_p = sqrt(w^T * Sigma * w)
        """
        with self._lock:
            now_utc = datetime.now(timezone.utc)
            # Re-estimate if cache is empty or expired (> update_interval_seconds)
            if self.cov_matrix is None or self.last_update is None or (now_utc - self.last_update).total_seconds() > self.update_interval_seconds:
                success = self.update_covariance_matrix()
                if not success and self.cov_matrix is None:
                    # Fallback gracefully if rates feed is temporarily unavailable
                    return True, "Dynamic Covariance matrix unavailable, fallback passed"

            norm_target = self.clean_symbol(target_symbol)
            if norm_target not in self.cov_matrix.columns:
                return True, f"Symbol {target_symbol} outside covariance matrix universe"

            target_dir = 1.0 if str(target_signal).upper() in ["BUY", "0"] else -1.0
            positions = open_positions or []

            # -------------------------------------------------------------
            # 1. PAIRWISE DYNAMIC CORRELATION CHECK
            # -------------------------------------------------------------
            if self.corr_matrix is not None:
                for pos in positions:
                    raw_sym = pos.get("symbol", "") if isinstance(pos, dict) else getattr(pos, "symbol", "")
                    pos_sym = self.clean_symbol(raw_sym)

                    # Skip same symbol (handled by individual bot position logic)
                    if pos_sym == norm_target or pos_sym not in self.corr_matrix.columns:
                        continue

                    # Extract position direction
                    pos_type = pos.get("type", "") if isinstance(pos, dict) else getattr(pos, "type", "")
                    pos_dir = 1.0 if str(pos_type).upper() in ["BUY", "0", "POSITION_TYPE_BUY"] else -1.0

                    pairwise_corr = float(self.corr_matrix.loc[norm_target, pos_sym])

                    # Same direction trade in strongly positively correlated pairs (Corr > +0.75)
                    if pairwise_corr > self.max_pairwise_corr and (target_dir * pos_dir > 0):
                        reason = (
                            f"Pairwise High Positive Correlation: Corr({norm_target}, {pos_sym}) = {pairwise_corr:+.2f} "
                            f"> {self.max_pairwise_corr:.2f} with concurrent direction ({target_signal})."
                        )
                        logger.warning(f"⚠️ [RISK GATEKEEPER - DYN COV] Order vetoed: {reason}")
                        return False, reason

                    # Opposing direction trade in strongly negatively correlated pairs (Corr < -0.75)
                    # (e.g. BUY EURUSD & BUY USDJPY when correlation is -0.80 concentrates USD exposure)
                    if pairwise_corr < -self.max_pairwise_corr and (target_dir * pos_dir < 0):
                        reason = (
                            f"Pairwise Inverse Extreme Correlation: Corr({norm_target}, {pos_sym}) = {pairwise_corr:+.2f} "
                            f"< -{self.max_pairwise_corr:.2f} with opposing positions."
                        )
                        logger.warning(f"⚠️ [RISK GATEKEEPER - DYN COV] Order vetoed: {reason}")
                        return False, reason

            # -------------------------------------------------------------
            # 2. PROJECTED PORTFOLIO VARIANCE CHECK: sigma_p = sqrt(w^T * Sigma * w)
            # -------------------------------------------------------------
            cov_syms = list(self.cov_matrix.columns)
            weight_vector = pd.Series(0.0, index=cov_syms)

            for pos in positions:
                raw_sym = pos.get("symbol", "") if isinstance(pos, dict) else getattr(pos, "symbol", "")
                ps = self.clean_symbol(raw_sym)
                if ps in cov_syms:
                    pos_type = pos.get("type", "") if isinstance(pos, dict) else getattr(pos, "type", "")
                    p_dir = 1.0 if str(pos_type).upper() in ["BUY", "0", "POSITION_TYPE_BUY"] else -1.0
                    weight_vector[ps] += p_dir

            # Add incremental impact of proposed order
            weight_vector[norm_target] += target_dir

            w = weight_vector.values
            sigma = self.cov_matrix.values

            port_var = float(np.dot(w.T, np.dot(sigma, w)))
            port_vol = float(np.sqrt(max(0.0, port_var)))

            if port_vol > self.max_portfolio_vol:
                reason = (
                    f"Projected Portfolio Volatility ({port_vol:.4f}) exceeds threshold ({self.max_portfolio_vol:.4f}). "
                    f"Shrinkage delta*: {self.shrinkage_intensity:.3f}"
                )
                logger.warning(f"⚠️ [RISK GATEKEEPER - DYN COV] Order vetoed: {reason}")
                return False, reason

            return True, f"Approved (Projected Vol: {port_vol:.4f}, Delta*: {self.shrinkage_intensity:.3f})"

    def get_correlation_matrix(self) -> Optional[pd.DataFrame]:
        """Return the current Ledoit-Wolf correlation matrix."""
        with self._lock:
            return self.corr_matrix.copy() if self.corr_matrix is not None else None
