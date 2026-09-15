"""
Daily_ST_M Backtester — Data Loader
Load parquet files, normalize dates, select universe by median daily turnover.
"""
import os
import glob
import sqlite3
from typing import Dict, Optional

import pandas as pd
import numpy as np


def load_single_file(filepath: str) -> pd.DataFrame:
    """Load a single _Day file and normalize its datetime column to date."""
    if filepath.endswith('.csv'):
        df = pd.read_csv(filepath)
    else:
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
    """Extract symbol name from filename like 'RELIANCE_Day.parquet' or 'RELIANCE_Day.csv'."""
    basename = os.path.basename(filepath)
    return basename.replace("_Day.parquet", "").replace("_Day.csv", "")


def load_all_daily_data(data_dir: str) -> Dict[str, pd.DataFrame]:
    """
    Load all historical data directly from SQLite database for instant loading.
    Returns dict mapping symbol -> DataFrame with columns: date, open, high, low, close, volume.
    """
    db_path = r"D:\overnight\live_signals.db"
    
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"Database not found at {db_path}")
        
    conn = sqlite3.connect(db_path)
    
    # Read entire market data into memory instantly
    df = pd.read_sql("SELECT * FROM market_data", conn)
    conn.close()
    
    if df.empty:
        raise ValueError("market_data table is empty in SQLite database!")
        
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"]).dt.date

    # Group by symbol and return dict
    all_data = {symbol: group.drop(columns=['symbol']).reset_index(drop=True) 
                for symbol, group in df.groupby('symbol')}
                
    print(f"Loaded {len(all_data)} symbols from database")
    return all_data


def get_index_symbols(index_name: str, companies_dir: str = r"c:\Users\Yug\Desktop\Overnight strategy\companies") -> list:
    """Read CSV and return list of symbols for the given index."""
    filepath = os.path.join(companies_dir, f"{index_name}.csv")
    if not os.path.exists(filepath):
        print(f"Warning: {filepath} not found.")
        return []
        
    df = pd.read_csv(filepath)
    if "Symbol" in df.columns:
        return df["Symbol"].tolist()
    return []


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

        if len(period_df) < 500:
            # Need minimum 500 days of data to match the strict universe selection
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
