"""
Daily_ST_M Backtester — Candle Filter Features & Filter Groups
Computes filter features and checks the 3 exact rule groups.
"""
from typing import Optional, Tuple

import numpy as np
import pandas as pd

from supertrend import compute_atr
from config import (
    ATR_PERIOD,
    TURNOVER_LOOKBACK,
    FILTER_TIGHT_MARUBOZU,
    FILTER_MID_RANGE_MARUBOZU_LIQ,
    FILTER_WIDE_MARUBOZU,
)


def compute_filter_features(
    df: pd.DataFrame,
    atr_period: int = ATR_PERIOD,
    turnover_lookback: int = TURNOVER_LOOKBACK,
) -> pd.DataFrame:
    """
    Compute all candle-level filter features on the DataFrame.

    Adds columns:
        - atr_pct: ATR(14) / Close * 100
        - upper_wick_pct: (High - max(Open, Close)) / Close * 100
        - signal_body_pct: ((Close / Open) - 1) * 100
        - range_pct: ((High / Low) - 1) * 100
        - avg20_turnover_cr: avg of (Close * Volume / 1e7) over previous 20 trading days
                             (excludes signal day — uses shifted window)

    Args:
        df: DataFrame with date, open, high, low, close, volume. Must be sorted by date.
        atr_period: ATR period (default 14).
        turnover_lookback: Number of past trading days for turnover avg (default 20).

    Returns:
        DataFrame with feature columns added.
    """
    df = df.copy()

    high = df["high"].values.astype(np.float64)
    low = df["low"].values.astype(np.float64)
    close = df["close"].values.astype(np.float64)
    open_ = df["open"].values.astype(np.float64)
    volume = df["volume"].values.astype(np.float64)

    # ATR %
    atr = compute_atr(high, low, close, atr_period)
    df["atr_pct"] = atr / close * 100.0

    # Signal Body %
    df["signal_body_pct"] = ((close / open_) - 1.0) * 100.0

    # Upper Wick %
    max_oc = np.maximum(open_, close)
    df["upper_wick_pct"] = ((high - max_oc) / close) * 100.0

    # Range %
    df["range_pct"] = ((high / low) - 1.0) * 100.0

    # Avg20 Turnover Cr — previous 20 trading days, EXCLUDING signal day
    # daily_turnover_cr = Close * Volume / 1e7
    daily_turnover_cr = pd.Series(close * volume / 1e7, index=df.index)
    # .shift(1) excludes the current day; .rolling(lookback) covers the previous N days
    df["avg20_turnover_cr"] = (
        daily_turnover_cr.shift(1).rolling(window=turnover_lookback, min_periods=turnover_lookback).mean()
    )

    return df


def check_tight_marubozu(
    atr_pct: float,
    upper_wick_pct: float,
    signal_body_pct: float,
    range_pct: float,
    **kwargs,
) -> bool:
    """Check tight_marubozu filter group."""
    return (
        atr_pct <= FILTER_TIGHT_MARUBOZU["atr_pct_le"]
        and upper_wick_pct <= FILTER_TIGHT_MARUBOZU["upper_wick_pct_le"]
        and signal_body_pct > FILTER_TIGHT_MARUBOZU["signal_body_pct_gt"]
        and range_pct <= FILTER_TIGHT_MARUBOZU["range_pct_le"]
    )


def check_mid_range_marubozu_liq(
    atr_pct: float,
    upper_wick_pct: float,
    signal_body_pct: float,
    range_pct: float,
    avg20_turnover_cr: float,
    **kwargs,
) -> bool:
    """Check mid_range_marubozu_liq filter group."""
    return (
        atr_pct <= FILTER_MID_RANGE_MARUBOZU_LIQ["atr_pct_le"]
        and upper_wick_pct <= FILTER_MID_RANGE_MARUBOZU_LIQ["upper_wick_pct_le"]
        and signal_body_pct > FILTER_MID_RANGE_MARUBOZU_LIQ["signal_body_pct_gt"]
        and range_pct > FILTER_MID_RANGE_MARUBOZU_LIQ["range_pct_gt"]
        and range_pct <= FILTER_MID_RANGE_MARUBOZU_LIQ["range_pct_le"]
        and avg20_turnover_cr > FILTER_MID_RANGE_MARUBOZU_LIQ["avg20_turnover_cr_gt"]
    )


def check_wide_marubozu(
    atr_pct: float,
    upper_wick_pct: float,
    signal_body_pct: float,
    range_pct: float,
    **kwargs,
) -> bool:
    """Check wide_marubozu filter group."""
    return (
        atr_pct <= FILTER_WIDE_MARUBOZU["atr_pct_le"]
        and upper_wick_pct <= FILTER_WIDE_MARUBOZU["upper_wick_pct_le"]
        and signal_body_pct > FILTER_WIDE_MARUBOZU["signal_body_pct_gt"]
        and range_pct > FILTER_WIDE_MARUBOZU["range_pct_gt"]
    )


def check_filters(
    atr_pct: float,
    upper_wick_pct: float,
    signal_body_pct: float,
    range_pct: float,
    avg20_turnover_cr: float,
) -> Tuple[bool, Optional[str]]:
    """
    Check all 3 filter groups in order. Returns the first group that passes.

    Args:
        All filter feature values for the signal candle.

    Returns:
        (passed, group_name): True and group name if any group passes;
                              (False, None) if no group passes.
    """
    # Check for NaN — if any feature is NaN, cannot pass filters
    import math
    for val in [atr_pct, upper_wick_pct, signal_body_pct, range_pct, avg20_turnover_cr]:
        if val is None or (isinstance(val, float) and math.isnan(val)):
            # For tight and wide marubozu, avg20_turnover_cr is not needed.
            # But we still check it separately below.
            break

    kwargs = dict(
        atr_pct=atr_pct,
        upper_wick_pct=upper_wick_pct,
        signal_body_pct=signal_body_pct,
        range_pct=range_pct,
        avg20_turnover_cr=avg20_turnover_cr,
    )

    # Check NaN for required fields of each filter
    import math

    def _is_valid(*vals):
        return all(v is not None and not (isinstance(v, float) and math.isnan(v)) for v in vals)

    # tight_marubozu: needs atr_pct, upper_wick_pct, signal_body_pct, range_pct
    if _is_valid(atr_pct, upper_wick_pct, signal_body_pct, range_pct):
        if check_tight_marubozu(**kwargs):
            return True, "tight_marubozu"

    # mid_range_marubozu_liq: needs all 5
    if _is_valid(atr_pct, upper_wick_pct, signal_body_pct, range_pct, avg20_turnover_cr):
        if check_mid_range_marubozu_liq(**kwargs):
            return True, "mid_range_marubozu_liq"

    # wide_marubozu: needs atr_pct, upper_wick_pct, signal_body_pct, range_pct
    if _is_valid(atr_pct, upper_wick_pct, signal_body_pct, range_pct):
        if check_wide_marubozu(**kwargs):
            return True, "wide_marubozu"

    return False, None
