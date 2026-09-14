from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd


BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = BASE_DIR / "top_200_files" / "content" / "drive" / "MyDrive" / "All_Data"
DEFAULT_OUT_DIR = BASE_DIR / "backtest_results" / "daily_st_m_replication"


@dataclass(frozen=True)
class SupertrendSpec:
    period: int
    multiplier: float

    @property
    def label(self) -> str:
        return f"st{self.period}_{str(self.multiplier).replace('.', 'p')}"


SUPER_TREND_SPECS = [
    SupertrendSpec(7, 2.0),
    SupertrendSpec(7, 3.0),
    SupertrendSpec(10, 2.0),
    SupertrendSpec(10, 3.0),
    SupertrendSpec(14, 2.0),
    SupertrendSpec(14, 3.0),
    SupertrendSpec(21, 1.5),
    SupertrendSpec(21, 2.0),
    SupertrendSpec(21, 3.0),
]


def read_day_file(path: Path) -> pd.DataFrame:
    cols = ["datetime", "open", "high", "low", "close", "volume"]
    if path.suffix.lower() == ".parquet":
        df = pd.read_parquet(path, columns=cols)
    else:
        df = pd.read_csv(path, usecols=cols)
    df = df.copy()
    df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce").dt.tz_localize(None)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["datetime", "open", "high", "low", "close"])
    df = df[(df["open"] > 0) & (df["high"] > 0) & (df["low"] > 0) & (df["close"] > 0)]
    return df.sort_values("datetime").reset_index(drop=True)


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    return pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)


def supertrend_direction(df: pd.DataFrame, period: int, multiplier: float) -> pd.Series:
    tr = true_range(df)
    atr = tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    hl2 = ((df["high"] + df["low"]) / 2).to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    atr_arr = atr.to_numpy(dtype=float)
    final_upper = hl2 + multiplier * atr_arr
    final_lower = hl2 - multiplier * atr_arr
    uptrend = np.ones(len(df), dtype=bool)

    for current in range(1, len(df)):
        previous = current - 1
        if close[current] > final_upper[previous]:
            uptrend[current] = True
        elif close[current] < final_lower[previous]:
            uptrend[current] = False
        else:
            uptrend[current] = uptrend[previous]
            if uptrend[current] and final_lower[current] < final_lower[previous]:
                final_lower[current] = final_lower[previous]
            if not uptrend[current] and final_upper[current] > final_upper[previous]:
                final_upper[current] = final_upper[previous]

    return pd.Series(uptrend, index=df.index)


def add_signal_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    tr = true_range(df)
    df["turnover_cr"] = df["close"] * df["volume"] / 10_000_000.0
    df["signal_avg20_turnover_cr"] = df["turnover_cr"].rolling(20, min_periods=20).mean()
    df["signal_atr_pct"] = tr.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean() / df["close"] * 100.0
    df["signal_body_pct"] = (df["close"] / df["open"] - 1.0) * 100.0
    df["signal_range_pct"] = (df["high"] / df["low"] - 1.0) * 100.0
    df["signal_upper_wick_pct"] = (
        (df["high"] - df[["open", "close"]].max(axis=1)) / df["close"] * 100.0
    )
    df["close_pos_pct"] = np.where(
        (df["high"] - df["low"]) > 0,
        ((df["close"] - df["low"]) / (df["high"] - df["low"])) * 100.0,
        np.nan,
    )
    return df.replace([np.inf, -np.inf], np.nan)


def rule_masks(df: pd.DataFrame) -> dict[str, pd.Series]:
    return {
        "tight_marubozu": (
            (df["signal_atr_pct"] <= 10.885584)
            & (df["signal_upper_wick_pct"] <= 0.019433)
            & (df["signal_body_pct"] > -0.087366)
            & (df["signal_range_pct"] <= 2.457461)
        ),
        "mid_range_marubozu_liq": (
            (df["signal_atr_pct"] <= 5.233445)
            & (df["signal_upper_wick_pct"] <= 0.019433)
            & (df["signal_body_pct"] > -0.087366)
            & (df["signal_range_pct"] > 2.457461)
            & (df["signal_range_pct"] <= 10.480694)
            & (df["signal_avg20_turnover_cr"] > 12.555267)
        ),
        "wide_marubozu": (
            (df["signal_atr_pct"] <= 10.885584)
            & (df["signal_upper_wick_pct"] <= 0.019433)
            & (df["signal_body_pct"] > -0.087366)
            & (df["signal_range_pct"] > 10.480694)
        ),
        "low_price_wide_liq": (
            (df["signal_atr_pct"] <= 10.885584)
            & (df["signal_upper_wick_pct"] > 0.019433)
            & (df["close"] <= 38.279999)
            & (df["signal_range_pct"] > 7.479814)
            & (df["signal_avg20_turnover_cr"] > 39.406544)
        ),
    }


def active_rules(rule_set: str) -> list[str]:
    if rule_set == "avg_gap_optimized":
        return ["tight_marubozu", "mid_range_marubozu_liq", "wide_marubozu"]
    if rule_set == "higher_sample_robust":
        return ["tight_marubozu", "mid_range_marubozu_liq", "wide_marubozu", "low_price_wide_liq"]
    raise ValueError("rule_set must be avg_gap_optimized or higher_sample_robust")


def rank_liquidity(paths: list[Path], start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    rows = []
    for idx, path in enumerate(paths, 1):
        if idx % 250 == 0:
            print(f"ranked liquidity {idx}/{len(paths)}", flush=True)
        try:
            if path.suffix.lower() == ".parquet":
                df = pd.read_parquet(path, columns=["datetime", "close", "volume"])
            else:
                df = pd.read_csv(path, usecols=["datetime", "close", "volume"])
            df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce").dt.tz_localize(None)
            df["close"] = pd.to_numeric(df["close"], errors="coerce")
            df["volume"] = pd.to_numeric(df["volume"], errors="coerce")
            df = df[(df["datetime"] >= start) & (df["datetime"] <= end)].dropna(subset=["close", "volume"])
            if len(df) < 500:
                continue
            turnover = df["close"] * df["volume"] / 10_000_000.0
            rows.append(
                {
                    "symbol": path.name.rsplit("_Day", 1)[0].upper(),
                    "path": str(path),
                    "rows": int(len(df)),
                    "median_turnover_cr": float(turnover.median()),
                    "mean_turnover_cr": float(turnover.mean()),
                }
            )
        except Exception as exc:
            print(f"liquidity skip {path.name}: {exc}", flush=True)
    return pd.DataFrame(rows).sort_values("median_turnover_cr", ascending=False).reset_index(drop=True)


def build_symbol_trades(
    path: Path,
    start: pd.Timestamp,
    end: pd.Timestamp,
    rule_names: list[str],
) -> list[dict]:
    symbol = path.name.rsplit("_Day", 1)[0].upper()
    read_start = start - pd.Timedelta(days=370)
    read_end = end + pd.Timedelta(days=14)
    df = read_day_file(path)
    df = df[(df["datetime"] >= read_start) & (df["datetime"] <= read_end)].reset_index(drop=True)
    if len(df) < 260:
        return []
    df = add_signal_features(df)
    masks = rule_masks(df)
    combined_mask = pd.Series(False, index=df.index)
    for name in rule_names:
        combined_mask = combined_mask | masks[name].fillna(False)

    selected: dict[str, dict] = {}
    for spec in SUPER_TREND_SPECS:
        uptrend = supertrend_direction(df, spec.period, spec.multiplier)
        previous = uptrend.shift(1).astype("boolean").fillna(False).astype(bool)
        flip_up = (uptrend.astype(bool) & ~previous).to_numpy(dtype=bool)
        for signal_i in np.flatnonzero(flip_up):
            exit_i = signal_i + 1
            if exit_i >= len(df) or not bool(combined_mask.iloc[signal_i]):
                continue
            signal_dt = pd.to_datetime(df.at[signal_i, "datetime"])
            if signal_dt < start or signal_dt > end:
                continue
            entry_close = float(df.at[signal_i, "close"])
            exit_open = float(df.at[exit_i, "open"])
            if not np.isfinite(entry_close) or not np.isfinite(exit_open) or entry_close <= 0:
                continue

            signal_date = signal_dt.date().isoformat()
            key = f"{symbol}|{signal_date}"
            matched_rules = [name for name in rule_names if bool(masks[name].fillna(False).iloc[signal_i])]
            if key not in selected:
                selected[key] = {
                    "symbol": symbol,
                    "signal_date": signal_date,
                    "exit_date": pd.to_datetime(df.at[exit_i, "datetime"]).date().isoformat(),
                    "entry_close": entry_close,
                    "exit_open": exit_open,
                    "gap_pct": (exit_open / entry_close - 1.0) * 100.0,
                    "signal_open": float(df.at[signal_i, "open"]),
                    "signal_high": float(df.at[signal_i, "high"]),
                    "signal_low": float(df.at[signal_i, "low"]),
                    "signal_close": entry_close,
                    "signal_volume": float(df.at[signal_i, "volume"]),
                    "signal_body_pct": float(df.at[signal_i, "signal_body_pct"]),
                    "signal_upper_wick_pct": float(df.at[signal_i, "signal_upper_wick_pct"]),
                    "signal_range_pct": float(df.at[signal_i, "signal_range_pct"]),
                    "signal_atr_pct": float(df.at[signal_i, "signal_atr_pct"]),
                    "signal_avg20_turnover_cr": float(df.at[signal_i, "signal_avg20_turnover_cr"]),
                    "rule_name": "+".join(matched_rules),
                    "supertrend_labels": [],
                }
            selected[key]["supertrend_labels"].append(spec.label)

    rows = list(selected.values())
    for row in rows:
        labels = sorted(set(row["supertrend_labels"]))
        row["supertrend_labels"] = ",".join(labels)
        row["supertrend_signal_count"] = len(labels)
        row["gap_up"] = bool(row["exit_open"] > row["entry_close"])
        row["gap_down"] = bool(row["exit_open"] < row["entry_close"])
        row["flat"] = bool(row["exit_open"] == row["entry_close"])
    return rows


def summarize(trades: pd.DataFrame, gap_target: float) -> dict:
    if trades.empty:
        return {
            "trades": 0,
            "symbols": 0,
            "gap_up_count": 0,
            "gap_down_count": 0,
            "flat_count": 0,
            "accuracy_gap_up_pct": 0.0,
            "avg_gap_pct": 0.0,
        }
    gaps = trades["gap_pct"].astype(float)
    return {
        "trades": int(len(trades)),
        "symbols": int(trades["symbol"].nunique()),
        "gap_up_count": int((gaps > 0).sum()),
        "gap_down_count": int((gaps < 0).sum()),
        "flat_count": int((gaps == 0).sum()),
        "target_hits": int((gaps >= gap_target).sum()),
        "accuracy_gap_up_pct": round(float((gaps > 0).mean()) * 100, 2),
        "target_hit_rate_pct": round(float((gaps >= gap_target).mean()) * 100, 2),
        "avg_gap_pct": round(float(gaps.mean()), 3),
        "median_gap_pct": round(float(gaps.median()), 3),
        "best_gap_pct": round(float(gaps.max()), 3),
        "worst_gap_pct": round(float(gaps.min()), 3),
        "avg_supertrend_signals_per_trade": round(float(trades["supertrend_signal_count"].mean()), 2),
    }


def grouped_summary(trades: pd.DataFrame, key: str, gap_target: float) -> pd.DataFrame:
    rows = []
    for label, group in trades.groupby(key):
        row = summarize(group, gap_target)
        row[key] = label
        rows.append(row)
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values(key).reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Replicate Daily_ST_M backtest results.")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--start", default="2021-01-01")
    parser.add_argument("--end", default="2025-12-31")
    parser.add_argument("--top-symbols", type=int, default=484)
    parser.add_argument("--gap-target", type=float, default=1.0)
    parser.add_argument(
        "--rule-set",
        choices=["avg_gap_optimized", "higher_sample_robust"],
        default="higher_sample_robust",
    )
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    start = pd.Timestamp(args.start)
    end = pd.Timestamp(args.end)
    rule_names = active_rules(args.rule_set)

    day_files = sorted(data_dir.glob("*_Day.parquet"))
    if not day_files:
        day_files = sorted(data_dir.glob("*_Day.csv"))
    liquidity = rank_liquidity(day_files, start, end)
    selected_paths = [Path(p) for p in liquidity.head(args.top_symbols)["path"].tolist()]

    rows = []
    for idx, path in enumerate(selected_paths, 1):
        if idx % 50 == 0:
            print(f"processed {idx}/{len(selected_paths)}", flush=True)
        rows.extend(build_symbol_trades(path, start, end, rule_names))

    trades = pd.DataFrame(rows)
    if not trades.empty:
        trades = trades.sort_values(["signal_date", "symbol"]).reset_index(drop=True)
        trades["year"] = pd.to_datetime(trades["signal_date"]).dt.year
        trades["month"] = pd.to_datetime(trades["signal_date"]).dt.strftime("%Y-%m")

    summary = {
        "strategy": "Daily_ST_M",
        "rule_set": args.rule_set,
        "active_rules": rule_names,
        "start": args.start,
        "end": args.end,
        "top_symbols": args.top_symbols,
        "gap_target_pct": args.gap_target,
        **summarize(trades, args.gap_target),
    }

    trades.to_csv(out_dir / f"daily_st_m_{args.rule_set}_trades.csv", index=False)
    liquidity.head(args.top_symbols).to_csv(out_dir / "daily_st_m_liquidity_universe.csv", index=False)
    grouped_summary(trades, "year", args.gap_target).to_csv(out_dir / f"daily_st_m_{args.rule_set}_year_summary.csv", index=False)
    grouped_summary(trades, "month", args.gap_target).to_csv(out_dir / f"daily_st_m_{args.rule_set}_month_summary.csv", index=False)
    grouped_summary(trades, "symbol", args.gap_target).to_csv(out_dir / f"daily_st_m_{args.rule_set}_company_summary.csv", index=False)
    with (out_dir / f"daily_st_m_{args.rule_set}_summary.json").open("w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)

    print(json.dumps(summary, indent=2), flush=True)
    print(f"Wrote results to: {out_dir}", flush=True)


if __name__ == "__main__":
    main()
