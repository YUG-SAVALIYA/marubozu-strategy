"""
Daily_ST_M Backtester — Data Loader
Load parquet files, normalize dates, select universe by median daily turnover.
"""
import os
import glob
from typing import Dict, Optional

import pandas as pd
import numpy as np


def load_single_file(filepath: str) -> pd.DataFrame:
    """Load a single _Day.parquet file and normalize its datetime column to date."""
    df = pd.read_parquet(filepath)

    # Normalize datetime to date (drop time/tz info)
    df["date"] = pd.to_datetime(df["datetime"]).dt.date
    df = df.drop(columns=["datetime"])

    # Ensure columns are lowercase
    df.columns = [c.lower() for c in df.columns]

    # Sort by date
    df = df.sort_values("date").reset_index(drop=True)

    # Drop rows with missing OHLCV
    df = df.dropna(subset=["open", "high", "low", "close", "volume"])

    # Drop rows with zero/negative prices or volume
    df = df[(df["open"] > 0) & (df["high"] > 0) & (df["low"] > 0) &
            (df["close"] > 0) & (df["volume"] > 0)]

    df = df.reset_index(drop=True)
    return df


def extract_symbol(filepath: str) -> str:
    """Extract symbol name from filename like 'RELIANCE_Day.parquet'."""
    basename = os.path.basename(filepath)
    return basename.replace("_Day.parquet", "")


def load_all_daily_data(data_dir: str) -> Dict[str, pd.DataFrame]:
    """
    Load all *_Day.parquet files from data_dir.
    Returns dict mapping symbol -> DataFrame with columns: date, open, high, low, close, volume.
    """
    pattern = os.path.join(data_dir, "*_Day.parquet")
    files = sorted(glob.glob(pattern))

    if not files:
        raise FileNotFoundError(f"No *_Day.parquet files found in {data_dir}")

    all_data: Dict[str, pd.DataFrame] = {}
    for filepath in files:
        symbol = extract_symbol(filepath)
        try:
            df = load_single_file(filepath)
            if len(df) > 0:
                all_data[symbol] = df
        except Exception as e:
            print(f"Warning: Skipping {symbol} due to error: {e}")

    print(f"Loaded {len(all_data)} symbols from {data_dir}")
    return all_data


def select_universe(
    all_data: Dict[str, pd.DataFrame],
    top_n: int,
    start_date: str,
    end_date: str,
) -> Dict[str, pd.DataFrame]:
    """
    Select top_n symbols by median daily turnover (close * volume) within [start_date, end_date].

    Args:
        all_data: Dict of symbol -> DataFrame
        top_n: Number of top symbols to select
        start_date: Start date string (YYYY-MM-DD)
        end_date: End date string (YYYY-MM-DD)

    Returns:
        Filtered dict with only the top_n symbols.
    """
    from datetime import date as dt_date

    start_dt = pd.Timestamp(start_date).date()
    end_dt = pd.Timestamp(end_date).date()

    turnover_scores: Dict[str, float] = {}

    for symbol, df in all_data.items():
        # Filter to backtest period
        mask = (df["date"] >= start_dt) & (df["date"] <= end_dt)
        period_df = df[mask]

        if len(period_df) < 20:
            # Need minimum data for meaningful turnover calculation
            continue

        # Median daily turnover (in raw units, not crores — ranking is invariant)
        daily_turnover = period_df["close"] * period_df["volume"]
        turnover_scores[symbol] = daily_turnover.median()

    # Rank and select top N
    ranked = sorted(turnover_scores.items(), key=lambda x: x[1], reverse=True)
    selected_symbols = set(sym for sym, _ in ranked[:top_n])

    universe = {sym: df for sym, df in all_data.items() if sym in selected_symbols}
    print(f"Selected universe: {len(universe)} symbols (requested top {top_n})")
    return universe
