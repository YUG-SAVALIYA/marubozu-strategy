"""
Daily_ST_M Backtester — Metrics & Reporting
Trade-level and portfolio-level metric calculations.
"""
from typing import List, Dict, Optional
from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class BacktestMetrics:
    """All computed backtest metrics."""
    # Trade-level counts
    total_trades: int
    positive_trades: int
    negative_trades: int
    flat_trades: int
    positive_rate: float  # %

    # Target
    target_hit_count: int
    target_hit_rate: float  # %

    # Gap statistics
    avg_gap_pct: float
    median_gap_pct: float
    best_gap_pct: float
    worst_gap_pct: float

    # Portfolio
    total_pnl: float
    ending_equity: float
    return_pct: float
    max_drawdown_pct: float
    gross_profit: float
    gross_loss: float
    profit_factor: float
    avg_return_per_trade: float
    best_day_pnl: float
    worst_day_pnl: float

    # Yearly
    yearly_returns: Dict[int, float]  # year -> return %


def compute_trade_metrics(trades: list) -> dict:
    """
    Compute trade-level gap metrics.

    Args:
        trades: List of Trade objects.

    Returns:
        Dict with trade-level metrics.
    """
    if not trades:
        return {
            "total_trades": 0,
            "positive_trades": 0,
            "negative_trades": 0,
            "flat_trades": 0,
            "positive_rate": 0.0,
            "target_hit_count": 0,
            "target_hit_rate": 0.0,
            "avg_gap_pct": 0.0,
            "median_gap_pct": 0.0,
            "best_gap_pct": 0.0,
            "worst_gap_pct": 0.0,
        }

    gaps = [t.gap_pct for t in trades]
    total = len(trades)

    positive = sum(1 for g in gaps if g > 0)
    negative = sum(1 for g in gaps if g < 0)
    flat = sum(1 for g in gaps if g == 0.0)

    target_hits = sum(1 for t in trades if t.target_hit)

    return {
        "total_trades": total,
        "positive_trades": positive,
        "negative_trades": negative,
        "flat_trades": flat,
        "positive_rate": (positive / total) * 100.0 if total > 0 else 0.0,
        "target_hit_count": target_hits,
        "target_hit_rate": (target_hits / total) * 100.0 if total > 0 else 0.0,
        "avg_gap_pct": float(np.mean(gaps)),
        "median_gap_pct": float(np.median(gaps)),
        "best_gap_pct": float(np.max(gaps)),
        "worst_gap_pct": float(np.min(gaps)),
    }


def compute_max_drawdown(equity_series: pd.DataFrame) -> float:
    """
    Compute maximum drawdown from daily EOD equity series.

    Args:
        equity_series: DataFrame with 'date' and 'equity' columns, sorted by date.

    Returns:
        Maximum drawdown as a percentage (0-100).
    """
    if equity_series.empty or len(equity_series) < 2:
        return 0.0

    equity = equity_series["equity"].values.astype(np.float64)
    peak = equity[0]
    max_dd = 0.0

    for i in range(1, len(equity)):
        if equity[i] > peak:
            peak = equity[i]
        dd = (peak - equity[i]) / peak * 100.0
        if dd > max_dd:
            max_dd = dd

    return max_dd


def compute_yearly_returns(equity_series: pd.DataFrame) -> Dict[int, float]:
    """
    Compute calendar-year returns from daily EOD equity series.

    For each calendar year, return = ((year_end_equity / year_start_equity) - 1) * 100.

    Args:
        equity_series: DataFrame with 'date' and 'equity' columns, sorted by date.

    Returns:
        Dict mapping year -> return %.
    """
    if equity_series.empty:
        return {}

    # Ensure date is a proper date type
    df = equity_series.copy()
    df["year"] = df["date"].apply(lambda d: d.year if hasattr(d, "year") else pd.Timestamp(d).year)

    yearly = {}
    for year, group in df.groupby("year"):
        group = group.sort_values("date")
        start_eq = group.iloc[0]["equity"]
        end_eq = group.iloc[-1]["equity"]
        if start_eq != 0:
            yearly[int(year)] = ((end_eq / start_eq) - 1.0) * 100.0
        else:
            yearly[int(year)] = 0.0

    return yearly


def compute_all_metrics(
    trades: list,
    daily_equity: pd.DataFrame,
    initial_capital: float,
) -> BacktestMetrics:
    """
    Compute all metrics: trade-level, portfolio-level, drawdown, yearly.

    Args:
        trades: List of Trade objects.
        daily_equity: DataFrame with date, equity columns.
        initial_capital: Starting capital.

    Returns:
        BacktestMetrics dataclass.
    """
    trade_metrics = compute_trade_metrics(trades)
    max_dd = compute_max_drawdown(daily_equity)
    yearly = compute_yearly_returns(daily_equity)

    total_pnl = sum(t.pnl for t in trades)
    ending_equity = initial_capital + total_pnl
    return_pct = ((ending_equity / initial_capital) - 1.0) * 100.0 if initial_capital > 0 else 0.0
    
    gross_profit = sum(t.pnl for t in trades if t.pnl > 0)
    gross_loss = abs(sum(t.pnl for t in trades if t.pnl < 0))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else (float('inf') if gross_profit > 0 else 0.0)
    avg_return = total_pnl / trade_metrics["total_trades"] if trade_metrics["total_trades"] > 0 else 0.0

    if not daily_equity.empty and "equity" in daily_equity.columns and len(daily_equity) > 1:
        daily_pnls = daily_equity["equity"].diff().dropna()
        best_day_pnl = float(daily_pnls.max()) if not daily_pnls.empty else 0.0
        worst_day_pnl = float(daily_pnls.min()) if not daily_pnls.empty else 0.0
    else:
        best_day_pnl = 0.0
        worst_day_pnl = 0.0

    return BacktestMetrics(
        total_trades=trade_metrics["total_trades"],
        positive_trades=trade_metrics["positive_trades"],
        negative_trades=trade_metrics["negative_trades"],
        flat_trades=trade_metrics["flat_trades"],
        positive_rate=trade_metrics["positive_rate"],
        target_hit_count=trade_metrics["target_hit_count"],
        target_hit_rate=trade_metrics["target_hit_rate"],
        avg_gap_pct=trade_metrics["avg_gap_pct"],
        median_gap_pct=trade_metrics["median_gap_pct"],
        best_gap_pct=trade_metrics["best_gap_pct"],
        worst_gap_pct=trade_metrics["worst_gap_pct"],
        total_pnl=total_pnl,
        ending_equity=ending_equity,
        return_pct=return_pct,
        max_drawdown_pct=max_dd,
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        profit_factor=profit_factor,
        avg_return_per_trade=avg_return,
        best_day_pnl=best_day_pnl,
        worst_day_pnl=worst_day_pnl,
        yearly_returns=yearly,
    )


def format_summary_report(metrics: BacktestMetrics, config=None) -> str:
    """Format a human-readable summary report."""
    lines = []
    lines.append("=" * 60)
    lines.append("  Daily_ST_M Backtest — Summary Report")
    lines.append("=" * 60)

    if config:
        lines.append(f"  Period:     {config.start_date} to {config.end_date}")
        lines.append(f"  Universe:   {config.universe_size} symbols")
        lines.append(f"  Capital:    ₹{config.initial_capital:,.0f}")
        lines.append(f"  Per trade:  ₹{config.allocation_per_trade:,.0f}")
        lines.append(f"  Leverage:   {config.leverage}x")
        lines.append(f"  Fees:       {config.fee_pct}%")
        lines.append(f"  Slippage:   {config.slippage_pct}%")
        lines.append(f"  Gap target: {config.gap_target_pct}%")
        lines.append("")

    lines.append("─── Trade-Level Metrics ───")
    lines.append(f"  Total trades:      {metrics.total_trades}")
    lines.append(f"  Positive trades:   {metrics.positive_trades}")
    lines.append(f"  Negative trades:   {metrics.negative_trades}")
    lines.append(f"  Flat trades:       {metrics.flat_trades}")
    lines.append(f"  Positive rate:     {metrics.positive_rate:.2f}%")
    lines.append(f"  Target hits:       {metrics.target_hit_count}")
    lines.append(f"  Target hit rate:   {metrics.target_hit_rate:.2f}%")
    lines.append("")
    lines.append("─── Gap Statistics ───")
    lines.append(f"  Average gap:       {metrics.avg_gap_pct:.4f}%")
    lines.append(f"  Median gap:        {metrics.median_gap_pct:.4f}%")
    lines.append(f"  Best gap:          {metrics.best_gap_pct:.4f}%")
    lines.append(f"  Worst gap:         {metrics.worst_gap_pct:.4f}%")
    lines.append("")
    lines.append("─── Portfolio Metrics ───")
    lines.append(f"  Total P&L:         ₹{metrics.total_pnl:,.2f}")
    lines.append(f"  Ending equity:     ₹{metrics.ending_equity:,.2f}")
    lines.append(f"  Return:            {metrics.return_pct:.2f}%")
    lines.append(f"  Max drawdown:      {metrics.max_drawdown_pct:.2f}%")
    lines.append("")
    lines.append("─── Yearly Returns ───")
    for year in sorted(metrics.yearly_returns.keys()):
        lines.append(f"  {year}:  {metrics.yearly_returns[year]:.2f}%")
    lines.append("")
    lines.append("=" * 60)

    return "\n".join(lines)
