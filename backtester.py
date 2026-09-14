"""
Daily_ST_M Backtester — Core Engine
Processes all symbols, generates signals, applies filters, manages capital,
and produces the trade log.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from datetime import date as dt_date
import math

import numpy as np
import pandas as pd

from config import BacktestConfig, SUPERTREND_CONFIGS
from supertrend import detect_bullish_flips
from filters import compute_filter_features, check_filters


@dataclass
class Trade:
    """A single trade record."""
    symbol: str
    signal_date: object          # date
    st_triggered: List[Tuple[int, float]]
    filter_group: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    atr_pct: float
    upper_wick_pct: float
    signal_body_pct: float
    range_pct: float
    avg20_turnover_cr: float
    entry_price: float           # = signal day close
    exit_date: object            # next trading day date
    exit_price: float            # = next trading day open
    gap_pct: float
    target_hit: bool
    allocated_capital: float = 0.0
    shares: int = 0
    pnl: float = 0.0
    fees: float = 0.0


@dataclass
class BacktestResult:
    """Complete backtest output."""
    trades: List[Trade]
    daily_equity: pd.DataFrame  # date, equity
    config: BacktestConfig


def _prepare_symbol_data(
    df: pd.DataFrame,
    config: BacktestConfig,
) -> pd.DataFrame:
    """Pre-compute all features for a single symbol."""
    df = compute_filter_features(
        df,
        atr_period=config.atr_period,
        turnover_lookback=config.turnover_lookback,
    )
    return df


def _generate_raw_signals(
    symbol: str,
    df: pd.DataFrame,
    config: BacktestConfig,
    start_dt: dt_date,
    end_dt: dt_date,
) -> List[dict]:
    """
    Generate raw trade signals for a single symbol.

    Returns list of signal dicts, each containing all info needed for a trade.
    Does NOT allocate capital — that's done at the portfolio level.
    """
    # Detect bullish flips across all ST configs
    flips = detect_bullish_flips(df, config.supertrend_configs)

    if flips.empty:
        return []

    signals = []
    dates = df["date"].values

    for _, flip_row in flips.iterrows():
        signal_date = flip_row["date"]
        idx = flip_row["idx"]

        # Convert numpy datetime to Python date if needed
        if hasattr(signal_date, "item"):
            signal_date = signal_date.item()
        if hasattr(signal_date, "date"):
            signal_date = signal_date.date() if callable(signal_date.date) else signal_date

        # Filter to backtest period
        if signal_date < start_dt or signal_date > end_dt:
            continue

        # ── Check candle filters ──
        row = df.iloc[idx]
        atr_pct = row.get("atr_pct", float("nan"))
        upper_wick_pct = row.get("upper_wick_pct", float("nan"))
        signal_body_pct = row.get("signal_body_pct", float("nan"))
        range_pct = row.get("range_pct", float("nan"))
        avg20_turnover_cr = row.get("avg20_turnover_cr", float("nan"))

        passed, filter_group = check_filters(
            atr_pct=atr_pct,
            upper_wick_pct=upper_wick_pct,
            signal_body_pct=signal_body_pct,
            range_pct=range_pct,
            avg20_turnover_cr=avg20_turnover_cr,
            signal_close=float(row["close"]),
        )

        if not passed:
            continue

        # ── Find next trading day ──
        if idx + 1 >= len(df):
            # No next trading day data available
            continue

        next_row = df.iloc[idx + 1]
        next_date = next_row["date"]
        if hasattr(next_date, "item"):
            next_date = next_date.item()

        exit_price = float(next_row["open"])
        entry_price = float(row["close"])

        # ── Gap calculation ──
        gap_pct = ((exit_price / entry_price) - 1.0) * 100.0
        
        # Remove outlier trades that gap more than +20% or less than -20%
        if abs(gap_pct) > 20.0:
            continue
            
        target_hit = gap_pct >= config.gap_target_pct

        signals.append({
            "symbol": symbol,
            "signal_date": signal_date,
            "st_triggered": flip_row["st_triggered"],
            "filter_group": filter_group,
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": float(row["volume"]),
            "atr_pct": float(atr_pct) if not math.isnan(atr_pct) else None,
            "upper_wick_pct": float(upper_wick_pct),
            "signal_body_pct": float(signal_body_pct),
            "range_pct": float(range_pct),
            "avg20_turnover_cr": float(avg20_turnover_cr) if not math.isnan(avg20_turnover_cr) else None,
            "entry_price": entry_price,
            "exit_date": next_date,
            "exit_price": exit_price,
            "gap_pct": gap_pct,
            "target_hit": target_hit,
        })

    return signals


def run_backtest(
    universe: Dict[str, pd.DataFrame],
    config: BacktestConfig,
) -> BacktestResult:
    """
    Run the full backtest across all symbols in the universe.

    Steps:
    1. Pre-compute features for each symbol.
    2. Generate raw signals (with filters applied).
    3. Sort signals chronologically.
    4. Allocate capital and compute P&L.
    5. Build daily equity curve.

    Args:
        universe: Dict of symbol -> DataFrame.
        config: BacktestConfig with all parameters.

    Returns:
        BacktestResult with trades, equity curve, and config.
    """
    start_dt = pd.Timestamp(config.start_date).date()
    end_dt = pd.Timestamp(config.end_date).date()

    # ── Step 1 & 2: Generate all raw signals ──
    print("Generating signals across all symbols...")
    all_signals = []
    processed = 0

    for symbol, raw_df in universe.items():
        df = _prepare_symbol_data(raw_df, config)
        signals = _generate_raw_signals(symbol, df, config, start_dt, end_dt)
        all_signals.extend(signals)
        processed += 1
        if processed % 100 == 0:
            print(f"  Processed {processed}/{len(universe)} symbols...")

    print(f"  Total raw signals (post-filter): {len(all_signals)}")

    # ── Step 3: Sort by signal date, then symbol (alphabetical for determinism) ──
    all_signals.sort(key=lambda s: (s["signal_date"], s["symbol"]))

    # ── Deduplication: one trade per (symbol, signal_date) ──
    seen = set()
    deduped_signals = []
    for sig in all_signals:
        key = (sig["symbol"], sig["signal_date"])
        if key not in seen:
            seen.add(key)
            deduped_signals.append(sig)

    print(f"  After deduplication: {len(deduped_signals)} signals")

    # ── Step 4: Capital allocation ──
    # ── Rebuild capital flow properly ──
    # We need a proper chronological simulation. Let's redo capital tracking properly.
    trades, daily_equity_df = _simulate_capital(deduped_signals, config)

    return BacktestResult(
        trades=trades,
        daily_equity=daily_equity_df,
        config=config,
    )


def _simulate_capital(
    signals: List[dict],
    config: BacktestConfig,
) -> Tuple[List[Trade], pd.DataFrame]:
    """
    Simulate capital allocation chronologically using true percentage-based compounding.

    Capital flow:
    - On exit_date at 09:15: P&L of exiting trades is realized and added to running_equity.
    - On signal_date at 15:30: We allocate a fixed fraction of the current running_equity to each new trade.
    - Infinite concurrent trades are allowed (no capital exhaustion bounds).
    """
    trades = []
    
    # Calculate fixed fractional allocation based on user inputs
    # E.g., if allocation_per_trade=20, alloc_fraction = 20%
    alloc_fraction = config.allocation_per_trade / 100.0

    running_equity = config.initial_capital
    cumulative_pnl = 0.0

    # Track P&L to realize on exit dates
    pending_returns: Dict = {}  # exit_date -> list of pnl

    # Build signal date -> signals mapping
    signals_by_date = {}
    for sig in signals:
        d = sig["signal_date"]
        if d not in signals_by_date:
            signals_by_date[d] = []
        signals_by_date[d].append(sig)

    # All unique dates
    all_dates = set()
    for sig in signals:
        sd = sig["signal_date"]
        ed = sig["exit_date"]
        if isinstance(sd, np.generic):
            sd = sd.item()
        if isinstance(ed, np.generic):
            ed = ed.item()
        all_dates.add(sd)
        all_dates.add(ed)

    sorted_dates = sorted(all_dates)
    equity_records = []

    for current_date in sorted_dates:
        # ── 09:15: Realize P&L from trades exiting today ──
        if current_date in pending_returns:
            for pnl in pending_returns[current_date]:
                running_equity += pnl
                cumulative_pnl += pnl
            del pending_returns[current_date]

        # ── 15:30: Process signals for today ──
        if current_date in signals_by_date:
            for sig in signals_by_date[current_date]:
                # Bankruptcy check
                if running_equity <= 0:
                    continue

                # Compounding dynamically adjusts allocation size based on current equity
                alloc = running_equity * alloc_fraction

                entry_price = sig["entry_price"]
                exit_price = sig["exit_price"]

                # Apply slippage
                effective_entry = entry_price * (1 + config.slippage_pct / 100.0)
                effective_exit = exit_price * (1 - config.slippage_pct / 100.0)

                shares = int((alloc * config.leverage) / effective_entry)
                if shares <= 0:
                    continue

                # Fees
                trade_value_entry = shares * effective_entry
                trade_value_exit = shares * effective_exit
                fees = (trade_value_entry + trade_value_exit) * (config.fee_pct / 100.0)

                # P&L
                pnl = shares * (effective_exit - effective_entry) - fees

                # Schedule P&L realization on exit date
                exit_d = sig["exit_date"]
                if exit_d not in pending_returns:
                    pending_returns[exit_d] = []
                pending_returns[exit_d].append(pnl)

                trade = Trade(
                    symbol=sig["symbol"],
                    signal_date=sig["signal_date"],
                    st_triggered=sig["st_triggered"],
                    filter_group=sig["filter_group"],
                    open=sig["open"],
                    high=sig["high"],
                    low=sig["low"],
                    close=sig["close"],
                    volume=sig["volume"],
                    atr_pct=sig["atr_pct"] if sig["atr_pct"] is not None else float("nan"),
                    upper_wick_pct=sig["upper_wick_pct"],
                    signal_body_pct=sig["signal_body_pct"],
                    range_pct=sig["range_pct"],
                    avg20_turnover_cr=sig["avg20_turnover_cr"] if sig["avg20_turnover_cr"] is not None else float("nan"),
                    entry_price=sig["entry_price"],
                    exit_date=sig["exit_date"],
                    exit_price=sig["exit_price"],
                    gap_pct=sig["gap_pct"],
                    target_hit=sig["target_hit"],
                    allocated_capital=alloc,
                    shares=shares,
                    pnl=pnl,
                    fees=fees,
                )
                trades.append(trade)

        # Record EOD equity
        equity_records.append({"date": current_date, "equity": running_equity})

    # Handle any remaining pending returns (shouldn't happen in normal flow)
    for exit_d in sorted(pending_returns.keys()):
        for pnl in pending_returns[exit_d]:
            running_equity += pnl
        equity_records.append({"date": exit_d, "equity": running_equity})

    daily_equity_df = pd.DataFrame(equity_records)
    if not daily_equity_df.empty:
        daily_equity_df = daily_equity_df.sort_values("date").reset_index(drop=True)
        # Remove duplicate dates (keep last)
        daily_equity_df = daily_equity_df.drop_duplicates(subset="date", keep="last").reset_index(drop=True)

    return trades, daily_equity_df
