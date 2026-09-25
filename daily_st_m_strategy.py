"""
Standalone Daily_ST_M strategy implementation.

Current strategy logic implemented here:

1. Universe:
   - Default: Top 800 symbols from
     static/data/daily_stm_top_liquid_company_csvs/top_800_liquid_symbols_only_2021_2025.csv

2. Signal candle:
   - Uses 5-minute data up to the selected signal time, default 15:20.
   - Signal-day OHLCV is rebuilt as:
       Open   = daily open
       High   = max 5-min high up to signal time
       Low    = min 5-min low up to signal time
       Close  = selected signal-time 5-min close
       Volume = summed 5-min volume up to signal time

3. Entry / exit:
   - Entry = signal-time 5-min close.
   - Exit  = next trading day's selected 5-min candle open, default 09:15.

4. Open-data-quality filter:
   - strict mode checks:
       abs(signal first 5-min open vs signal daily open) <= threshold
       abs(exit 5-min open vs next daily open) <= threshold

5. Auto-regime rules:
   - Strong regime rule:
       mkt_up_pct >= strong_mkt_up_pct
       strong_breadth_ret1_3_pct >= strong_breadth_pct
       upper_wick_pct <= 0
       ret5_pct >= strong_ret5_pct
       ret1_pct >= strong_ret1_pct
       entry_price <= strong_max_close_price
       close_pos_pct >= strong_close_pos_pct

   - Normal regime rule:
       only if NOT strong regime
       mkt_up_pct >= normal_mkt_up_pct
       strong_breadth_ret1_3_pct >= normal_breadth_pct
       upper_wick_pct <= 0
       ret5_pct >= normal_ret5_pct
       ret1_pct >= normal_ret1_pct
       entry_price <= normal_max_close_price
       close_pos_pct >= normal_close_pos_pct

Usage examples:

    python daily_st_m_strategy.py

    python daily_st_m_strategy.py --regime-filter strong --open-mismatch-max-pct 10

    python daily_st_m_strategy.py --start-date 2021-01-01 --end-date 2025-12-31 ^
        --companies TOP800 --signal-time 15:20 --exit-time 09:15 --export-csv output/daily_st_m.csv
"""

from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = Path("D:/datas")
DEFAULT_TOP800_CSV = PROJECT_DIR / "top_800_liquid_stocks.csv"


@dataclass
class DailySTMConfig:
    start_date: str = "2021-01-01"
    end_date: str = "2026-04-30"
    data_dir: Path = DEFAULT_DATA_DIR
    companies: str = "TOP800"  # TOP800, TOP484, ALL, or comma-separated symbols
    top_symbols: int = 800
    top_symbols_csv: Path = DEFAULT_TOP800_CSV
    signal_time: str = "15:05"
    exit_time: str = "09:15"
    gap_target_pct: float = 1.0

    # "strict" = check both signal-day and exit-day daily-vs-5min open mismatch.
    # "all" / "off" = no open mismatch filter.
    open_mismatch_mode: str = "strict"
    open_mismatch_max_pct: float = 1.0

    # strong, normal, or both
    regime_filter: str = "strong"

    # Strong regime
    strong_mkt_up_pct: float = 45.0
    strong_breadth_pct: float = 5.0
    strong_ret5_pct: float = 20.0
    strong_ret1_pct: float = 3.0
    strong_max_close_price: float = 500.0
    strong_close_pos_pct: float = 90.0

    # Normal regime
    normal_mkt_up_pct: float = 45.0
    normal_breadth_pct: float = 5.0
    normal_ret5_pct: float = 25.0
    normal_ret1_pct: float = 5.0
    normal_max_close_price: float = 1000.0
    normal_close_pos_pct: float = 92.0

    max_workers: int = 16


def _to_datetime_no_tz(series: pd.Series) -> pd.Series:
    dt = pd.to_datetime(series, errors="coerce")
    try:
        if getattr(dt.dt, "tz", None) is not None:
            return dt.dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    except Exception:
        pass
    try:
        return dt.dt.tz_localize(None)
    except Exception:
        return dt


def _time_to_minutes(value: str, default: str) -> int:
    text = str(value or default).strip().lower().replace(".", ":")
    if "am" in text or "pm" in text:
        ts = pd.to_datetime(text, errors="coerce")
        if pd.notna(ts):
            return int(ts.hour * 60 + ts.minute)
    parts = text.replace(" ", "").split(":")
    if len(parts) >= 2:
        try:
            return int(parts[0]) * 60 + int(parts[1])
        except Exception:
            pass
    dparts = str(default).split(":")
    return int(dparts[0]) * 60 + int(dparts[1])


def _safe_float(value: Any, ndigits: int = 4) -> float | None:
    try:
        val = float(value)
        if np.isfinite(val):
            return round(val, ndigits)
    except Exception:
        pass
    return None


def _read_ohlcv(path: Path) -> pd.DataFrame:
    if path.suffix == ".parquet":
        df = pd.read_parquet(path)
    else:
        df = pd.read_csv(path)
    lower_map = {c: str(c).strip().lower() for c in df.columns}
    df = df.rename(columns=lower_map)
    if "datetime" not in df.columns:
        for candidate in ("date", "timestamp", "time"):
            if candidate in df.columns:
                df = df.rename(columns={candidate: "datetime"})
                break
    required = ["datetime", "open", "high", "low", "close", "volume"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"{path.name}: missing columns {missing}")
    df["datetime"] = _to_datetime_no_tz(df["datetime"])
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["datetime", "open", "high", "low", "close"]).sort_values("datetime")
    return df[required].reset_index(drop=True)


def _symbol_from_day_file(path: Path) -> str:
    return path.name.rsplit("_Day", 1)[0].upper()


def _find_5min_path(data_dir: Path, symbol: str) -> Path | None:
    candidates = [
        data_dir / f"{symbol}_5min.parquet",
        data_dir / f"{symbol}_5Min.parquet",
        data_dir / f"{symbol}_5MIN.parquet",
        data_dir / f"{symbol}_5min.csv",
        data_dir / f"{symbol}_5Min.csv",
        data_dir / f"{symbol}_5MIN.csv",
    ]
    for path in candidates:
        if path.exists():
            return path
    matches = list(data_dir.glob(f"{symbol}*5min*.*"))
    return matches[0] if matches else None


def load_symbols(config: DailySTMConfig) -> list[str] | None:
    companies = str(config.companies or "TOP800").strip()
    upper = companies.upper()

    if upper == "ALL":
        return None

    if "," in companies:
        return [s.strip().upper() for s in companies.split(",") if s.strip()]

    if upper.startswith("TOP"):
        csv_path = config.top_symbols_csv
        if upper == "TOP484":
            csv_path = csv_path.with_name("top_484_liquid_stocks.csv")
        elif upper == "TOP600":
            csv_path = csv_path.with_name("top_600_liquid_stocks.csv")
        elif upper == "TOP750":
            csv_path = csv_path.with_name("top_750_liquid_stocks.csv")
        elif upper == "TOP800":
            csv_path = csv_path.with_name("top_800_liquid_stocks.csv")

        top_n = config.top_symbols
        try:
            parsed_n = int(upper.replace("TOP", ""))
            top_n = parsed_n
        except Exception:
            pass

        df = pd.read_csv(csv_path)
        sym_col = "symbol" if "symbol" in df.columns else df.columns[-1]
        return [str(s).strip().upper() for s in df[sym_col].head(top_n).dropna().tolist()]

    return [upper]


def select_day_files(config: DailySTMConfig) -> list[Path]:
    symbols = load_symbols(config)
    if symbols is None:
        csvs = list(config.data_dir.glob("*_Day.csv"))
        pqs = list(config.data_dir.glob("*_Day.parquet"))
        return sorted(csvs + pqs)

    files: list[Path] = []
    for symbol in symbols:
        path_pq = config.data_dir / f"{symbol}_Day.parquet"
        path_csv = config.data_dir / f"{symbol}_Day.csv"
        if path_pq.exists():
            files.append(path_pq)
        elif path_csv.exists():
            files.append(path_csv)
    return files


def build_symbol_features(day_path: Path, config: DailySTMConfig) -> tuple[pd.DataFrame, str | None]:
    symbol = _symbol_from_day_file(day_path)
    five_path = _find_5min_path(config.data_dir, symbol)
    if five_path is None:
        return pd.DataFrame(), f"{symbol}: missing 5-min file"

    try:
        daily = _read_ohlcv(day_path)
        intraday = _read_ohlcv(five_path)
    except Exception as exc:
        return pd.DataFrame(), f"{symbol}: read failed: {exc}"

    if daily.empty or intraday.empty:
        return pd.DataFrame(), f"{symbol}: empty data"

    daily = daily.copy()
    daily["date"] = daily["datetime"].dt.normalize()
    daily = daily.drop_duplicates("date", keep="last").sort_values("date").reset_index(drop=True)
    if len(daily) < 8:
        return pd.DataFrame(), None

    daily["idx"] = np.arange(len(daily))
    daily["prev1_close"] = daily["close"].shift(1)
    daily["prev5_close"] = daily["close"].shift(5)

    intraday = intraday.copy()
    intraday["date"] = intraday["datetime"].dt.normalize()
    intraday["minute"] = intraday["datetime"].dt.hour * 60 + intraday["datetime"].dt.minute
    intraday = intraday.dropna(subset=["date", "minute"]).sort_values("datetime")

    signal_minutes = _time_to_minutes(config.signal_time, "15:20")
    exit_minutes = _time_to_minutes(config.exit_time, "09:15")

    first5 = (
        intraday.groupby("date", as_index=False)
        .agg(first5_open=("open", "first"), first5_time=("datetime", "first"))
    )

    # Exact selected signal candle must exist, matching the backend.
    exact_signal_dates = set(intraday.loc[intraday["minute"] == signal_minutes, "date"])
    signal_raw = intraday[(intraday["minute"] <= signal_minutes) & (intraday["date"].isin(exact_signal_dates))]
    if signal_raw.empty:
        return pd.DataFrame(), None

    early = (
        signal_raw.groupby("date", as_index=False)
        .agg(
            early_high=("high", "max"),
            early_low=("low", "min"),
            entry_price=("close", "last"),
            early_volume=("volume", "sum"),
            signal_time=("datetime", "last"),
        )
    )

    exit_map = (
        intraday[intraday["minute"] == exit_minutes]
        .groupby("date", as_index=False)
        .agg(exit_open=("open", "first"), exit_time=("datetime", "first"))
    )
    if exit_map.empty:
        return pd.DataFrame(), None

    current = daily.merge(early, on="date", how="inner").merge(first5, on="date", how="inner")
    current["next_idx"] = current["idx"] + 1
    next_daily = daily[["idx", "date", "open"]].rename(
        columns={"idx": "next_idx", "date": "next_date", "open": "next_daily_open"}
    )
    current = current.merge(next_daily, on="next_idx", how="inner")
    current = current.merge(
        exit_map.rename(columns={"date": "next_date"})[["next_date", "exit_open", "exit_time"]],
        on="next_date",
        how="inner",
    )
    if current.empty:
        return pd.DataFrame(), None

    start_dt = pd.to_datetime(config.start_date).normalize()
    end_dt = pd.to_datetime(config.end_date).normalize()
    current = current[(current["date"] >= start_dt) & (current["date"] <= end_dt)].copy()
    if current.empty:
        return pd.DataFrame(), None

    current["company"] = symbol
    signal_range = current["early_high"] - current["early_low"]
    current["signal_open_mismatch_pct"] = (current["first5_open"] / current["open"] - 1.0).abs() * 100.0
    current["exit_open_mismatch_pct"] = (current["exit_open"] / current["next_daily_open"] - 1.0).abs() * 100.0
    current["gap_pct"] = (current["exit_open"] / current["entry_price"] - 1.0) * 100.0
    current["upper_wick_pct"] = (
        (current["early_high"] - np.maximum(current["open"], current["entry_price"])) / current["entry_price"] * 100.0
    )
    current["ret1_pct"] = (current["entry_price"] / current["prev1_close"] - 1.0) * 100.0
    current["ret5_pct"] = (current["entry_price"] / current["prev5_close"] - 1.0) * 100.0
    current["close_pos_pct"] = np.where(
        signal_range > 0,
        ((current["entry_price"] - current["early_low"]) / signal_range) * 100.0,
        np.nan,
    )
    current["signal_body_pct"] = (current["entry_price"] / current["open"] - 1.0) * 100.0
    current["signal_range_pct"] = np.where(
        current["early_low"] > 0,
        (current["early_high"] / current["early_low"] - 1.0) * 100.0,
        np.nan,
    )

    columns = [
        "company",
        "date",
        "next_date",
        "signal_time",
        "exit_time",
        "open",
        "early_high",
        "early_low",
        "entry_price",
        "early_volume",
        "exit_open",
        "next_daily_open",
        "first5_open",
        "signal_open_mismatch_pct",
        "exit_open_mismatch_pct",
        "gap_pct",
        "upper_wick_pct",
        "ret1_pct",
        "ret5_pct",
        "close_pos_pct",
        "signal_body_pct",
        "signal_range_pct",
    ]
    return current[columns].replace([np.inf, -np.inf], np.nan), None


def build_all_features(config: DailySTMConfig) -> tuple[pd.DataFrame, list[str]]:
    day_files = select_day_files(config)
    frames: list[pd.DataFrame] = []
    errors: list[str] = []

    workers = min(max(1, config.max_workers), max(1, len(day_files)))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(build_symbol_features, path, config) for path in day_files]
        for future in as_completed(futures):
            frame, error = future.result()
            if error:
                errors.append(error)
            if frame is not None and not frame.empty:
                frames.append(frame)

    if not frames:
        return pd.DataFrame(), errors
    return pd.concat(frames, ignore_index=True), errors


def apply_daily_st_m_rules(feature_df: pd.DataFrame, config: DailySTMConfig) -> pd.DataFrame:
    if feature_df.empty:
        return feature_df.copy()

    df = feature_df.copy()

    if str(config.open_mismatch_mode).strip().lower() not in {"all", "off", "none", "no_filter"}:
        max_mismatch = float(config.open_mismatch_max_pct)
        df = df[
            (pd.to_numeric(df["signal_open_mismatch_pct"], errors="coerce") <= max_mismatch)
            & (pd.to_numeric(df["exit_open_mismatch_pct"], errors="coerce") <= max_mismatch)
        ].copy()

    if df.empty:
        return df

    regime = (
        df.groupby("date")
        .agg(
            mkt_ret1_median=("ret1_pct", "median"),
            mkt_ret5_median=("ret5_pct", "median"),
            mkt_up_pct=("ret1_pct", lambda s: float((pd.to_numeric(s, errors="coerce") > 0).mean() * 100.0)),
            strong_breadth_ret1_3_pct=("ret1_pct", lambda s: float((pd.to_numeric(s, errors="coerce") >= 3.0).mean() * 100.0)),
            mkt_count=("ret1_pct", "size"),
        )
        .reset_index()
    )
    df = df.merge(regime, on="date", how="left")

    mkt_up = pd.to_numeric(df["mkt_up_pct"], errors="coerce")
    breadth = pd.to_numeric(df["strong_breadth_ret1_3_pct"], errors="coerce")
    upper_wick = pd.to_numeric(df["upper_wick_pct"], errors="coerce")
    ret5 = pd.to_numeric(df["ret5_pct"], errors="coerce")
    ret1 = pd.to_numeric(df["ret1_pct"], errors="coerce")
    entry = pd.to_numeric(df["entry_price"], errors="coerce")
    close_pos = pd.to_numeric(df["close_pos_pct"], errors="coerce")

    strong_regime = (mkt_up >= config.strong_mkt_up_pct) & (breadth >= config.strong_breadth_pct)
    normal_regime = (~strong_regime.fillna(False)) & (mkt_up >= config.normal_mkt_up_pct) & (breadth >= config.normal_breadth_pct)

    strong_rule = (
        (upper_wick <= 0.0)
        & (ret5 >= config.strong_ret5_pct)
        & (ret1 >= config.strong_ret1_pct)
        & (entry <= config.strong_max_close_price)
        & (close_pos >= config.strong_close_pos_pct)
    )
    normal_rule = (
        (upper_wick <= 0.0)
        & (ret5 >= config.normal_ret5_pct)
        & (ret1 >= config.normal_ret1_pct)
        & (entry <= config.normal_max_close_price)
        & (close_pos >= config.normal_close_pos_pct)
    )

    strong_selected = strong_regime.fillna(False) & strong_rule.fillna(False)
    normal_selected = normal_regime.fillna(False) & normal_rule.fillna(False)

    mode = str(config.regime_filter or "both").strip().lower()
    if mode == "strong":
        selected_mask = strong_selected
    elif mode == "normal":
        selected_mask = normal_selected
    else:
        selected_mask = strong_selected | normal_selected

    selected = df[selected_mask].copy()
    if selected.empty:
        return selected

    selected["regime_name"] = np.where(strong_selected.loc[selected.index], "Strong", "Normal")
    selected["rule_name"] = np.where(strong_selected.loc[selected.index], "auto_regime_strong", "auto_regime_normal")
    selected = selected.sort_values(["date", "company"]).drop_duplicates(["company", "date"], keep="first")
    selected["target_hit"] = pd.to_numeric(selected["gap_pct"], errors="coerce") >= float(config.gap_target_pct)
    return selected.reset_index(drop=True)


def summarize_trades(trades: pd.DataFrame, config: DailySTMConfig) -> dict[str, Any]:
    if trades.empty:
        return {
            "total_trades": 0,
            "positive": 0,
            "negative": 0,
            "flat": 0,
            "positive_rate_pct": 0.0,
            "avg_next_gap_pct": 0.0,
            "median_next_gap_pct": 0.0,
            "target_hit_rate_pct": 0.0,
            "net_profit_pct": 0.0,
            "strong_trades": 0,
            "normal_trades": 0,
        }

    gaps = pd.to_numeric(trades["gap_pct"], errors="coerce").dropna()
    total = len(trades)
    positive = int((gaps > 0).sum())
    negative = int((gaps < 0).sum())
    flat = int((gaps == 0).sum())
    target_hits = int((gaps >= float(config.gap_target_pct)).sum())

    return {
        "total_trades": total,
        "positive": positive,
        "negative": negative,
        "flat": flat,
        "positive_rate_pct": round(positive / total * 100.0, 2) if total else 0.0,
        "negative_rate_pct": round(negative / total * 100.0, 2) if total else 0.0,
        "target_hit_rate_pct": round(target_hits / total * 100.0, 2) if total else 0.0,
        "avg_next_gap_pct": round(float(gaps.mean()), 4) if not gaps.empty else 0.0,
        "median_next_gap_pct": round(float(gaps.median()), 4) if not gaps.empty else 0.0,
        "net_profit_pct": round(float(gaps.sum()), 4) if not gaps.empty else 0.0,
        "gap_ge_0_5_pct": round(float((gaps >= 0.5).mean() * 100.0), 2) if not gaps.empty else 0.0,
        "gap_ge_1_pct": round(float((gaps >= 1.0).mean() * 100.0), 2) if not gaps.empty else 0.0,
        "gap_ge_2_pct": round(float((gaps >= 2.0).mean() * 100.0), 2) if not gaps.empty else 0.0,
        "gap_ge_3_pct": round(float((gaps >= 3.0).mean() * 100.0), 2) if not gaps.empty else 0.0,
        "gap_ge_5_pct": round(float((gaps >= 5.0).mean() * 100.0), 2) if not gaps.empty else 0.0,
        "best_gap_pct": round(float(gaps.max()), 4) if not gaps.empty else 0.0,
        "worst_gap_pct": round(float(gaps.min()), 4) if not gaps.empty else 0.0,
        "companies_with_trades": int(trades["company"].nunique()),
        "strong_trades": int((trades.get("regime_name") == "Strong").sum()),
        "normal_trades": int((trades.get("regime_name") == "Normal").sum()),
    }


def yearwise_summary(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    df = trades.copy()
    df["year"] = pd.to_datetime(df["date"], errors="coerce").dt.year
    df["gap_pct"] = pd.to_numeric(df["gap_pct"], errors="coerce")
    rows = []
    for year, g in df.groupby("year"):
        gaps = g["gap_pct"].dropna()
        rows.append(
            {
                "year": int(year),
                "trades": int(len(g)),
                "positive": int((gaps > 0).sum()),
                "negative": int((gaps < 0).sum()),
                "flat": int((gaps == 0).sum()),
                "positive_rate_pct": round(float((gaps > 0).mean() * 100.0), 2) if not gaps.empty else 0.0,
                "avg_gap_pct": round(float(gaps.mean()), 4) if not gaps.empty else 0.0,
                "median_gap_pct": round(float(gaps.median()), 4) if not gaps.empty else 0.0,
                "strong_trades": int((g["regime_name"] == "Strong").sum()) if "regime_name" in g else 0,
                "normal_trades": int((g["regime_name"] == "Normal").sum()) if "regime_name" in g else 0,
            }
        )
    return pd.DataFrame(rows).sort_values("year").reset_index(drop=True)


def run_daily_st_m(config: DailySTMConfig) -> tuple[pd.DataFrame, dict[str, Any], pd.DataFrame, list[str]]:
    features, errors = build_all_features(config)
    trades = apply_daily_st_m_rules(features, config)
    metrics = summarize_trades(trades, config)
    metrics["feature_rows_before_rules"] = int(len(features))
    metrics["errors"] = int(len(errors))
    metrics["companies_scanned"] = len(select_day_files(config))
    yearly = yearwise_summary(trades)
    return trades, metrics, yearly, errors


def _json_default(value: Any) -> Any:
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    if pd.isna(value):
        return None
    return str(value)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run standalone Daily_ST_M strategy backtest.")
    parser.add_argument("--start-date", default="2020-01-01")
    parser.add_argument("--end-date", default="2026-04-30")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--companies", default="TOP800")
    parser.add_argument("--top-symbols", type=int, default=800)
    parser.add_argument("--top-symbols-csv", default=str(DEFAULT_TOP800_CSV))
    parser.add_argument("--signal-time", default="15:05")
    parser.add_argument("--exit-time", default="09:15")
    parser.add_argument("--gap-target-pct", type=float, default=1.0)
    parser.add_argument("--open-mismatch-mode", default="strict", choices=["strict", "all", "off", "none"])
    parser.add_argument("--open-mismatch-max-pct", type=float, default=1.0)
    parser.add_argument("--regime-filter", default="strong", choices=["strong", "normal", "both"])

    parser.add_argument("--strong-mkt-up-pct", type=float, default=45.0)
    parser.add_argument("--strong-breadth-pct", type=float, default=5.0)
    parser.add_argument("--strong-ret5-pct", type=float, default=20.0)
    parser.add_argument("--strong-ret1-pct", type=float, default=3.0)
    parser.add_argument("--strong-max-close-price", type=float, default=500.0)
    parser.add_argument("--strong-close-pos-pct", type=float, default=90.0)

    parser.add_argument("--normal-mkt-up-pct", type=float, default=45.0)
    parser.add_argument("--normal-breadth-pct", type=float, default=5.0)
    parser.add_argument("--normal-ret5-pct", type=float, default=25.0)
    parser.add_argument("--normal-ret1-pct", type=float, default=5.0)
    parser.add_argument("--normal-max-close-price", type=float, default=1000.0)
    parser.add_argument("--normal-close-pos-pct", type=float, default=92.0)

    parser.add_argument("--max-workers", type=int, default=16)
    parser.add_argument("--export-csv", default="")
    parser.add_argument("--export-json", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = DailySTMConfig(
        start_date=args.start_date,
        end_date=args.end_date,
        data_dir=Path(args.data_dir),
        companies=args.companies,
        top_symbols=args.top_symbols,
        top_symbols_csv=Path(args.top_symbols_csv),
        signal_time=args.signal_time,
        exit_time=args.exit_time,
        gap_target_pct=args.gap_target_pct,
        open_mismatch_mode=args.open_mismatch_mode,
        open_mismatch_max_pct=args.open_mismatch_max_pct,
        regime_filter=args.regime_filter,
        strong_mkt_up_pct=args.strong_mkt_up_pct,
        strong_breadth_pct=args.strong_breadth_pct,
        strong_ret5_pct=args.strong_ret5_pct,
        strong_ret1_pct=args.strong_ret1_pct,
        strong_max_close_price=args.strong_max_close_price,
        strong_close_pos_pct=args.strong_close_pos_pct,
        normal_mkt_up_pct=args.normal_mkt_up_pct,
        normal_breadth_pct=args.normal_breadth_pct,
        normal_ret5_pct=args.normal_ret5_pct,
        normal_ret1_pct=args.normal_ret1_pct,
        normal_max_close_price=args.normal_max_close_price,
        normal_close_pos_pct=args.normal_close_pos_pct,
        max_workers=args.max_workers,
    )

    trades, metrics, yearly, errors = run_daily_st_m(config)

    print("\nDaily_ST_M Results")
    print(json.dumps(metrics, indent=2, default=_json_default))

    if not yearly.empty:
        print("\nYear-wise")
        print(yearly.to_string(index=False))

    if args.export_csv:
        out = Path(args.export_csv)
        out.parent.mkdir(parents=True, exist_ok=True)
        trades.to_csv(out, index=False)
        print(f"\nSaved trades CSV: {out}")

    if args.export_json:
        out = Path(args.export_json)
        out.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "strategy": "Daily_ST_M",
            "config": asdict(config),
            "metrics": metrics,
            "yearwise": yearly.to_dict(orient="records"),
            "trades": trades.to_dict(orient="records"),
            "errors": errors,
        }
        out.write_text(json.dumps(payload, indent=2, default=_json_default), encoding="utf-8")
        print(f"Saved JSON: {out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
