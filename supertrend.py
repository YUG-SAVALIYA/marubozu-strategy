"""
Daily_ST_M Backtester — Supertrend Calculation & Flip Detection
Implements standard Supertrend with Wilder's ATR and bullish flip detection.
"""
from typing import List, Tuple, Dict, Optional

import numpy as np
import pandas as pd
from numba import njit


@njit(cache=True)
def compute_atr(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    period: int,
) -> np.ndarray:
    """
    Compute Wilder's ATR (exponential smoothing).

    Args:
        high, low, close: Price arrays (same length).
        period: ATR lookback period.

    Returns:
        ATR array of same length, with NaN for first (period-1) values.
    """
    n = len(high)
    tr = np.empty(n, dtype=np.float64)

    # First true range has no previous close
    tr[0] = high[0] - low[0]

    for i in range(1, n):
        hl = high[i] - low[i]
        hc = abs(high[i] - close[i - 1])
        lc = abs(low[i] - close[i - 1])
        tr[i] = max(hl, hc, lc)

    atr = np.full(n, np.nan, dtype=np.float64)

    # Initial ATR = simple average of first `period` true ranges
    if n < period:
        return atr

    atr[period - 1] = np.mean(tr[:period])

    # Wilder's smoothing: ATR[i] = (ATR[i-1] * (period-1) + TR[i]) / period
    alpha = 1.0 / period
    for i in range(period, n):
        atr[i] = atr[i - 1] * (1 - alpha) + tr[i] * alpha

    return atr


@njit(cache=True)
def compute_supertrend(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    period: int,
    multiplier: float,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Compute Supertrend indicator.

    Args:
        high, low, close: Price arrays.
        period: ATR period.
        multiplier: Band multiplier.

    Returns:
        Tuple of (upper_band, lower_band, direction) arrays.
        direction: 1 = bullish (price above lower band), -1 = bearish.
    """
    n = len(high)
    atr = compute_atr(high, low, close, period)

    # Mid-point
    hl2 = (high + low) / 2.0

    # Basic bands
    basic_upper = hl2 + multiplier * atr
    basic_lower = hl2 - multiplier * atr

    # Final bands and direction
    final_upper = np.full(n, np.nan, dtype=np.float64)
    final_lower = np.full(n, np.nan, dtype=np.float64)
    direction = np.zeros(n, dtype=np.int8)

    if n < period:
        return final_upper, final_lower, direction

    # Initialize at the first valid ATR index
    start_idx = period - 1
    final_upper[start_idx] = basic_upper[start_idx]
    final_lower[start_idx] = basic_lower[start_idx]
    # Default initial direction: bullish if close is above mid, else bearish
    direction[start_idx] = 1 if close[start_idx] > hl2[start_idx] else -1

    for i in range(start_idx + 1, n):
        # ── Final upper band ──
        # The upper band can only decrease (tighten) when price stays below it.
        # If previous close was <= previous final upper, take the min (tighten).
        # If previous close was > previous final upper, reset to basic.
        if basic_upper[i] < final_upper[i - 1] or close[i - 1] > final_upper[i - 1]:
            final_upper[i] = basic_upper[i]
        else:
            final_upper[i] = final_upper[i - 1]

        # ── Final lower band ──
        # The lower band can only increase (tighten) when price stays above it.
        # If previous close was >= previous final lower, take the max (tighten).
        # If previous close was < previous final lower, reset to basic.
        if basic_lower[i] > final_lower[i - 1] or close[i - 1] < final_lower[i - 1]:
            final_lower[i] = basic_lower[i]
        else:
            final_lower[i] = final_lower[i - 1]

        # ── Direction ──
        prev_dir = direction[i - 1]
        if prev_dir == 1:
            # Currently bullish — flip to bearish if close drops below lower band
            if close[i] < final_lower[i]:
                direction[i] = -1
            else:
                direction[i] = 1
        else:
            # Currently bearish — flip to bullish if close rises above upper band
            if close[i] > final_upper[i]:
                direction[i] = 1
            else:
                direction[i] = -1

    return final_upper, final_lower, direction


def detect_bullish_flips(
    df: pd.DataFrame,
    st_configs: List[Tuple[int, float]],
) -> pd.DataFrame:
    """
    Detect Supertrend bullish flips (bearish → bullish) across multiple configurations.

    For each date where ANY config flips bullish, creates one entry listing all
    configs that flipped on that date (deduplication: one signal per date).

    Args:
        df: DataFrame with columns: date, open, high, low, close, volume.
            Must be sorted by date.
        st_configs: List of (period, multiplier) tuples.

    Returns:
        DataFrame with columns:
            - date: signal date
            - st_triggered: list of (period, multiplier) tuples that flipped
            - idx: index into the original df for the signal row
    """
    high = df["high"].values.astype(np.float64)
    low = df["low"].values.astype(np.float64)
    close = df["close"].values.astype(np.float64)
    dates = df["date"].values
    n = len(df)

    # Collect all flip dates across all configs
    # date_idx -> set of configs that flipped
    flip_map: Dict[int, List[Tuple[int, float]]] = {}

    for period, multiplier in st_configs:
        _, _, direction = compute_supertrend(high, low, close, period, multiplier)

        for i in range(1, n):
            if direction[i] == 1 and direction[i - 1] == -1:
                if i not in flip_map:
                    flip_map[i] = []
                flip_map[i].append((period, multiplier))

    if not flip_map:
        return pd.DataFrame(columns=["date", "st_triggered", "idx"])

    # Build result DataFrame
    records = []
    for idx in sorted(flip_map.keys()):
        records.append({
            "date": dates[idx],
            "st_triggered": flip_map[idx],
            "idx": idx,
        })

    return pd.DataFrame(records)
