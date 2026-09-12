"""
Daily_ST_M Backtester — CLI Entry Point
Run the full backtest, generate trades CSV, summary report, and equity curve.
"""
import argparse
import os
import sys
import time

import pandas as pd
import matplotlib
matplotlib.use("Agg")  # Non-interactive backend
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

from config import BacktestConfig
from data_loader import load_all_daily_data, select_universe
from backtester import run_backtest
from metrics import compute_all_metrics, format_summary_report


def trades_to_dataframe(trades: list) -> pd.DataFrame:
    """Convert list of Trade objects to a DataFrame for CSV export."""
    records = []
    for t in trades:
        st_str = "; ".join(f"ST({p},{m})" for p, m in t.st_triggered)
        records.append({
            "Symbol": t.symbol,
            "Signal Date": t.signal_date,
            "Supertrend(s) Triggered": st_str,
            "Filter Group": t.filter_group,
            "Open": t.open,
            "High": t.high,
            "Low": t.low,
            "Close": t.close,
            "Volume": t.volume,
            "ATR %": t.atr_pct,
            "Upper Wick %": t.upper_wick_pct,
            "Signal Body %": t.signal_body_pct,
            "Range %": t.range_pct,
            "Avg20 Turnover Cr": t.avg20_turnover_cr,
            "Entry Price": t.entry_price,
            "Exit Date": t.exit_date,
            "Exit Price": t.exit_price,
            "Gap %": t.gap_pct,
            "Target Hit": t.target_hit,
            "Allocated Capital": t.allocated_capital,
            "Shares": t.shares,
            "P&L": t.pnl,
            "Fees": t.fees,
        })
    return pd.DataFrame(records)


def plot_equity_curve(daily_equity: pd.DataFrame, output_path: str):
    """Generate and save equity curve plot."""
    if daily_equity.empty:
        print("No equity data to plot.")
        return

    fig, ax = plt.subplots(figsize=(14, 6))

    dates = pd.to_datetime(daily_equity["date"])
    equity = daily_equity["equity"].values

    ax.plot(dates, equity, color="#2196F3", linewidth=1.2, label="Equity")
    ax.fill_between(dates, equity, alpha=0.1, color="#2196F3")

    # Format
    ax.set_title("Daily_ST_M — Equity Curve", fontsize=14, fontweight="bold")
    ax.set_xlabel("Date", fontsize=11)
    ax.set_ylabel("Equity (₹)", fontsize=11)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=6))
    plt.xticks(rotation=45, ha="right")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="upper left")

    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    plt.close()
    print(f"Equity curve saved to: {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Daily_ST_M Backtester")
    parser.add_argument("--start", default="2021-01-01", help="Backtest start date (YYYY-MM-DD)")
    parser.add_argument("--end", default="2025-12-31", help="Backtest end date (YYYY-MM-DD)")
    parser.add_argument("--universe", type=int, default=484, help="Top N symbols by median turnover")
    parser.add_argument("--capital", type=float, default=100_000, help="Initial capital")
    parser.add_argument("--allocation", type=float, default=20_000, help="Allocation per trade")
    parser.add_argument("--leverage", type=float, default=1.0, help="Leverage multiplier")
    parser.add_argument("--fees", type=float, default=0.0, help="Fee %% of trade value")
    parser.add_argument("--slippage", type=float, default=0.0, help="Slippage %% of price")
    parser.add_argument("--target", type=float, default=1.0, help="Gap target %%")
    parser.add_argument("--data-dir", default=r"C:\Users\Yug\Desktop\datas", help="Data directory")
    parser.add_argument("--output-dir", default="output", help="Output directory")
    args = parser.parse_args()

    config = BacktestConfig(
        data_dir=args.data_dir,
        start_date=args.start,
        end_date=args.end,
        universe_size=args.universe,
        initial_capital=args.capital,
        allocation_per_trade=args.allocation,
        leverage=args.leverage,
        fee_pct=args.fees,
        slippage_pct=args.slippage,
        gap_target_pct=args.target,
    )

    print("=" * 60)
    print("  Daily_ST_M Backtester")
    print("=" * 60)
    print(f"  Period:     {config.start_date} to {config.end_date}")
    print(f"  Universe:   top {config.universe_size} by median turnover")
    print(f"  Capital:    ₹{config.initial_capital:,.0f}")
    print(f"  Per trade:  ₹{config.allocation_per_trade:,.0f}")
    print(f"  Leverage:   {config.leverage}x")
    print(f"  Target:     {config.gap_target_pct}%")
    print("=" * 60)
    print()

    # ── Load data ──
    t0 = time.time()
    print("Loading data...")
    all_data = load_all_daily_data(config.data_dir)
    print(f"  Loaded in {time.time() - t0:.1f}s")

    # ── Select universe ──
    print("Selecting universe...")
    universe = select_universe(all_data, config.universe_size, config.start_date, config.end_date)

    # ── Run backtest ──
    print("\nRunning backtest...")
    t1 = time.time()
    result = run_backtest(universe, config)
    print(f"  Backtest completed in {time.time() - t1:.1f}s")

    # ── Compute metrics ──
    metrics = compute_all_metrics(result.trades, result.daily_equity, config.initial_capital)

    # ── Output ──
    os.makedirs(args.output_dir, exist_ok=True)

    # Summary report
    report = format_summary_report(metrics, config)
    print()
    print(report)

    summary_path = os.path.join(args.output_dir, "summary.txt")
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"\nSummary saved to: {summary_path}")

    # Trade log CSV
    if result.trades:
        trades_df = trades_to_dataframe(result.trades)
        trades_path = os.path.join(args.output_dir, "trades.csv")
        trades_df.to_csv(trades_path, index=False)
        print(f"Trade log saved to: {trades_path} ({len(result.trades)} trades)")

    # Equity curve
    if not result.daily_equity.empty:
        equity_path = os.path.join(args.output_dir, "equity_curve.png")
        plot_equity_curve(result.daily_equity, equity_path)

    # Equity data CSV
    if not result.daily_equity.empty:
        eq_csv_path = os.path.join(args.output_dir, "daily_equity.csv")
        result.daily_equity.to_csv(eq_csv_path, index=False)
        print(f"Daily equity saved to: {eq_csv_path}")

    print(f"\nTotal time: {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
