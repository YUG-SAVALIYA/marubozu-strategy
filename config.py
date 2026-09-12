"""
Daily_ST_M Backtester — Configuration
All strategy constants, thresholds, and parameters in one place.
"""
from dataclasses import dataclass, field
from typing import List, Tuple

# ─────────────────────────────────────────────
# Supertrend configurations (period, multiplier)
# ─────────────────────────────────────────────
SUPERTREND_CONFIGS: List[Tuple[int, float]] = [
    (7, 2.0),
    (7, 3.0),
    (10, 2.0),
    (10, 3.0),
    (14, 2.0),
    (14, 3.0),
    (21, 1.5),
    (21, 2.0),
    (21, 3.0),
]

# ─────────────────────────────────────────────
# ATR period used for filter feature calculation
# ─────────────────────────────────────────────
ATR_PERIOD: int = 14

# ─────────────────────────────────────────────
# Turnover lookback (excludes signal day)
# ─────────────────────────────────────────────
TURNOVER_LOOKBACK: int = 20

# ─────────────────────────────────────────────
# Candle filter thresholds — exact values
# Operators: <= means <=, > means strictly >
# ─────────────────────────────────────────────
FILTER_TIGHT_MARUBOZU = {
    "atr_pct_le": 10.885584,
    "upper_wick_pct_le": 0.019433,
    "signal_body_pct_gt": -0.087366,
    "range_pct_le": 2.457461,
}

FILTER_MID_RANGE_MARUBOZU_LIQ = {
    "atr_pct_le": 5.233445,
    "upper_wick_pct_le": 0.019433,
    "signal_body_pct_gt": -0.087366,
    "range_pct_gt": 2.457461,
    "range_pct_le": 10.480694,
    "avg20_turnover_cr_gt": 12.555267,
}

FILTER_WIDE_MARUBOZU = {
    "atr_pct_le": 10.885584,
    "upper_wick_pct_le": 0.019433,
    "signal_body_pct_gt": -0.087366,
    "range_pct_gt": 10.480694,
}

# ─────────────────────────────────────────────
# Backtest date range
# ─────────────────────────────────────────────
BACKTEST_START: str = "2021-01-01"
BACKTEST_END: str = "2025-12-31"

# ─────────────────────────────────────────────
# Universe
# ─────────────────────────────────────────────
UNIVERSE_SIZE: int = 484

# ─────────────────────────────────────────────
# Gap target
# ─────────────────────────────────────────────
GAP_TARGET_PCT: float = 1.0

# ─────────────────────────────────────────────
# Capital & risk parameters
# ─────────────────────────────────────────────

@dataclass
class BacktestConfig:
    """All backtester parameters in a single, parameterized object."""
    # Data
    data_dir: str = r"C:\Users\Yug\Desktop\datas"
    start_date: str = BACKTEST_START
    end_date: str = BACKTEST_END
    universe_size: int = UNIVERSE_SIZE

    # Strategy
    supertrend_configs: List[Tuple[int, float]] = field(default_factory=lambda: list(SUPERTREND_CONFIGS))
    atr_period: int = ATR_PERIOD
    turnover_lookback: int = TURNOVER_LOOKBACK
    gap_target_pct: float = GAP_TARGET_PCT

    # Capital
    initial_capital: float = 100_000.0
    allocation_per_trade: float = 20_000.0
    leverage: float = 1.0
    fee_pct: float = 0.0       # Total fee as % of trade value (entry + exit)
    slippage_pct: float = 0.0  # Slippage as % of price
