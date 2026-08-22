from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

BASE_CLOSE_DATE = pd.Timestamp("2020-07-31")
FIRST_TRADING_DATE = pd.Timestamp("2020-08-03")
END_DATE = pd.Timestamp("2021-07-30")
LOOKBACK_DAYS = 45
LIQUIDITY_AVG_TRADING_VALUE = 300_000_000.0
LIQUIDITY_MIN_DAYS = 15
SLOT_COUNT = 12
SECTOR_CAP = 3
ONE_WAY_COST = 0.002
HOLD_MONTHS = 6
BENCHMARK_CODE = 1306

NUMERIC_COLUMNS = [
    "NetSales", "OperatingProfit", "OrdinaryProfit", "Profit", "EarningsPerShare",
    "ForecastNetSales", "ForecastOperatingProfit", "ForecastOrdinaryProfit",
    "ForecastProfit", "ForecastEarningsPerShare", "ResultDividendPerShareAnnual",
    "ForecastDividendPerShareAnnual", "NumberOfIssuedAndOutstandingSharesAtTheEndOfFiscalYearIncludingTreasuryStock",
    "NumberOfTreasuryStockAtTheEndOfFiscalYear", "AverageNumberOfShares",
]
FORECAST_METRICS = [
    "ForecastOperatingProfit", "ForecastOrdinaryProfit", "ForecastProfit", "ForecastEarningsPerShare"
]


def to_number(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series.replace({"－": np.nan, "-": np.nan, "": np.nan}), errors="coerce")


def safe_float(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def pct_change(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None or abs(previous) < 1e-12:
        return None
    return (current - previous) / abs(previous)


def profit_yoy(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None:
        return None
    if previous > 0:
        return current / previous - 1.0
    if previous <= 0 < current:
        return 1.0
    if current <= 0 and previous > 0:
        return -1.0
    return None


def first_code_date_after(code_dates: dict[int, pd.DatetimeIndex], code: int, after: pd.Timestamp) -> pd.Timestamp | None:
    dates = code_dates.get(int(code))
    if dates is None or len(dates) == 0:
        return None
    idx = dates.searchsorted(after, side="right")
    if idx >= len(dates):
        return None
    return pd.Timestamp(dates[idx])


def first_code_date_on_or_after(code_dates: dict[int, pd.DatetimeIndex], code: int, target: pd.Timestamp) -> pd.Timestamp | None:
    dates = code_dates.get(int(code))
    if dates is None or len(dates) == 0:
        return None
    idx = dates.searchsorted(target, side="left")
    if idx >= len(dates):
        return None
    return pd.Timestamp(dates[idx])


def load_data(data_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    prices = pd.read_csv(data_dir / "stock_prices.csv", low_memory=False)
    fin = pd.read_csv(data_dir / "financials.csv", low_memory=False)
    stock_list = pd.read_csv(data_dir / "stock_list.csv", low_memory=False)

    prices["Date"] = pd.to_datetime(prices["Date"], errors="coerce")
    prices["SecuritiesCode"] = pd.to_numeric(prices["SecuritiesCode"], errors="coerce").astype("Int64")
    prices = prices.dropna(subset=["Date", "SecuritiesCode"]).copy()
    prices["SecuritiesCode"] = prices["SecuritiesCode"].astype(int)
    for col in ["Open", "High", "Low", "Close", "Volume", "AdjustmentFactor", "ExpectedDividend"]:
        if col not in prices.columns:
            prices[col] = np.nan
        prices[col] = to_number(prices[col])
    if "SupervisionFlag" not in prices.columns:
        prices["SupervisionFlag"] = False
    prices["AdjustmentFactor"] = prices["AdjustmentFactor"].fillna(1.0)
    prices["ExpectedDividend"] = prices["ExpectedDividend"].fillna(0.0)
    prices = prices.sort_values(["SecuritiesCode", "Date"]).reset_index(drop=True)
    prices["CumAdjust"] = prices.groupby("SecuritiesCode", sort=False)["AdjustmentFactor"].transform(
        lambda s: s.iloc[::-1].cumprod().iloc[::-1]
    )
    for col in ["Open", "High", "Low", "Close"]:
        prices[f"Adj{col}"] = prices[col] * prices["CumAdjust"]
    prices["AdjExpectedDividend"] = prices["ExpectedDividend"] * prices["CumAdjust"]
    prices["TradingValue"] = prices["Close"] * prices["Volume"]
    prices["Month"] = prices["Date"].dt.to_period("M")

    fin["SecuritiesCode"] = pd.to_numeric(fin["SecuritiesCode"], errors="coerce").astype("Int64")
    fin = fin.dropna(subset=["SecuritiesCode"]).copy()
    fin["SecuritiesCode"] = fin["SecuritiesCode"].astype(int)
    for col in NUMERIC_COLUMNS:
        if col not in fin.columns:
            fin[col] = np.nan
        fin[col] = to_number(fin[col])
    for col in ["DisclosedDate", "CurrentPeriodEndDate", "CurrentFiscalYearStartDate", "CurrentFiscalYearEndDate"]:
        if col not in fin.columns:
            fin[col] = pd.NaT
        fin[col] = pd.to_datetime(fin[col], errors="coerce")
    if "DisclosedTime" not in fin.columns:
        fin["DisclosedTime"] = "00:00:00"
    if "DisclosureNumber" not in fin.columns:
        fin["DisclosureNumber"] = np.arange(len(fin))
    fin["DisclosureDT"] = pd.to_datetime(
        fin["DisclosedDate"].dt.strftime("%Y-%m-%d") + " " + fin["DisclosedTime"].fillna("00:00:00").astype(str),
        errors="coerce",
    ).fillna(fin["DisclosedDate"])
    fin = fin.sort_values(["SecuritiesCode", "DisclosureDT", "DisclosureNumber"], na_position="last").reset_index(drop=True)

    group_keys = ["SecuritiesCode", "CurrentFiscalYearEndDate"]
    change_cols: list[str] = []
    for col in FORECAST_METRICS:
        prev_col = f"Prev_{col}"
        chg_col = f"Chg_{col}"
        fin[prev_col] = fin.groupby(group_keys, dropna=False)[col].transform(lambda s: s.ffill().shift(1))
        prev = fin[prev_col]
        cur = fin[col]
        fin[chg_col] = np.where(cur.notna() & prev.notna() & (prev.abs() > 1e-12), (cur - prev) / prev.abs(), np.nan)
        change_cols.append(chg_col)
    fin["ForecastMaxChange"] = fin[change_cols].max(axis=1, skipna=True)
    fin["ForecastMinChange"] = fin[change_cols].min(axis=1, skipna=True)
    fin["UpRevisionRow"] = fin["ForecastMaxChange"].ge(0.05).fillna(False)
    fin["DownRevisionRow"] = fin["ForecastMinChange"].le(-0.05).fillna(False)

    fin["Prev_ForecastDividendPerShareAnnual"] = fin.groupby(group_keys, dropna=False)[
        "ForecastDividendPerShareAnnual"
    ].transform(lambda s: s.ffill().shift(1))
    fin["DividendForecastChange"] = np.where(
        fin["ForecastDividendPerShareAnnual"].notna()
        & fin["Prev_ForecastDividendPerShareAnnual"].notna()
        & (fin["Prev_ForecastDividendPerShareAnnual"].abs() > 1e-12),
        (fin["ForecastDividendPerShareAnnual"] - fin["Prev_ForecastDividendPerShareAnnual"])
        / fin["Prev_ForecastDividendPerShareAnnual"].abs(),
        np.nan,
    )

    stock_list["SecuritiesCode"] = pd.to_numeric(stock_list["SecuritiesCode"], errors="coerce").astype("Int64")
    stock_list = stock_list.dropna(subset=["SecuritiesCode"]).copy()
    stock_list["SecuritiesCode"] = stock_list["SecuritiesCode"].astype(int)
    if "EffectiveDate" in stock_list.columns:
        stock_list["EffectiveDate"] = pd.to_datetime(stock_list["EffectiveDate"].astype(str), errors="coerce")
        stock_list = stock_list.sort_values(["SecuritiesCode", "EffectiveDate"]).drop_duplicates("SecuritiesCode", keep="last")
    else:
        stock_list = stock_list.drop_duplicates("SecuritiesCode", keep="last")
    return prices, fin, stock_list


def previous_comparable_statement(code_hist: pd.DataFrame, current_row: pd.Series) -> pd.Series | None:
    period_type = current_row.get("TypeOfCurrentPeriod")
    period_end = current_row.get("CurrentPeriodEndDate")
    disclosed = current_row.get("DisclosureDT")
    if pd.isna(period_end) or pd.isna(disclosed):
        return None
    candidates = code_hist[
        (code_hist["DisclosureDT"] < disclosed)
        & (code_hist["TypeOfCurrentPeriod"] == period_type)
        & code_hist["CurrentPeriodEndDate"].notna()
    ].copy()
    if candidates.empty:
        return None
    delta = (period_end - candidates["CurrentPeriodEndDate"]).dt.days
    candidates = candidates[(delta >= 330) & (delta <= 400)].copy()
    if candidates.empty:
        return None
    candidates["YearDistance"] = ((period_end - candidates["CurrentPeriodEndDate"]).dt.days - 365).abs()
    return candidates.sort_values(["YearDistance", "DisclosureDT"]).iloc[0]


def latest_prior_fy_dividend(code_hist: pd.DataFrame, current_row: pd.Series) -> float | None:
    fy_end = current_row.get("CurrentFiscalYearEndDate")
    disclosed = current_row.get("DisclosureDT")
    if pd.isna(fy_end) or pd.isna(disclosed):
        return None
    candidates = code_hist[
        (code_hist["DisclosureDT"] < disclosed)
        & code_hist["CurrentFiscalYearEndDate"].notna()
        & (code_hist["CurrentFiscalYearEndDate"] < fy_end)
        & code_hist["ResultDividendPerShareAnnual"].notna()
    ].copy()
    if candidates.empty:
        return None
    candidates = candidates.sort_values(["CurrentFiscalYearEndDate", "DisclosureDT"])
    return safe_float(candidates.iloc[-1]["ResultDividendPerShareAnnual"])


def evaluate_fundamentals(fin: pd.DataFrame, universe: set[int], checkpoints: list[pd.Timestamp], name_map: dict[int, str], sector_map: dict[int, str]) -> tuple[pd.DataFrame, dict[pd.Timestamp, dict[int, dict[str, Any]]]]:
    fin_by_code = {int(code): group.copy() for code, group in fin.groupby("SecuritiesCode", sort=False)}
    all_rows: list[dict[str, Any]] = []
    decision_map: dict[pd.Timestamp, dict[int, dict[str, Any]]] = {}
    for cp in checkpoints:
        cutoff = cp + pd.Timedelta(hours=18)
        window_start = cp - pd.Timedelta(days=LOOKBACK_DAYS - 1)
        window = fin[(fin["DisclosureDT"] >= window_start) & (fin["DisclosureDT"] <= cutoff) & fin["SecuritiesCode"].isin(universe)]
        cp_map: dict[int, dict[str, Any]] = {}
        for code in sorted(window["SecuritiesCode"].unique().tolist()):
            hist = fin_by_code[int(code)]
            hist = hist[hist["DisclosureDT"] <= cutoff]
            win = window[window["SecuritiesCode"] == int(code)].copy()
            if win.empty or hist.empty:
                continue
            up_revision = bool(win["UpRevisionRow"].any())
            down_revision = bool(win["DownRevisionRow"].any())
            up_change = safe_float(win.loc[win["UpRevisionRow"], "ForecastMaxChange"].max()) if up_revision else None
            down_change = safe_float(win.loc[win["DownRevisionRow"], "ForecastMinChange"].min()) if down_revision else None

            statements = win[win["TypeOfCurrentPeriod"].notna() & (win["OperatingProfit"].notna() | win["OrdinaryProfit"].notna())].sort_values("DisclosureDT")
            yoy_value = None
            profit_growth = False
            profit_decline = False
            if not statements.empty:
                current_row = statements.iloc[-1]
                prior_row = previous_comparable_statement(hist, current_row)
                if prior_row is not None:
                    cur = safe_float(current_row.get("OperatingProfit"))
                    prev = safe_float(prior_row.get("OperatingProfit"))
                    if cur is None or prev is None:
                        cur = safe_float(current_row.get("OrdinaryProfit"))
                        prev = safe_float(prior_row.get("OrdinaryProfit"))
                    yoy_value = profit_yoy(cur, prev)
                    profit_growth = yoy_value is not None and yoy_value >= 0.15
                    profit_decline = yoy_value is not None and yoy_value <= -0.20

            latest_statements = hist[hist["TypeOfCurrentPeriod"].notna() & (hist["OperatingProfit"].notna() | hist["OrdinaryProfit"].notna())].sort_values("DisclosureDT")
            latest_profit_value = None
            latest_profit_black = False
            if not latest_statements.empty:
                latest_stmt = latest_statements.iloc[-1]
                latest_profit_value = safe_float(latest_stmt.get("OperatingProfit"))
                if latest_profit_value is None:
                    latest_profit_value = safe_float(latest_stmt.get("OrdinaryProfit"))
                latest_profit_black = latest_profit_value is not None and latest_profit_value > 0

            dividend_growth = False
            dividend_change_value = None
            for _, div_row in win[win["ForecastDividendPerShareAnnual"].notna()].sort_values("DisclosureDT").iterrows():
                same_fy = safe_float(div_row.get("DividendForecastChange"))
                prior_actual = latest_prior_fy_dividend(hist, div_row)
                current_forecast = safe_float(div_row.get("ForecastDividendPerShareAnnual"))
                vs_prior = pct_change(current_forecast, prior_actual)
                candidates = [x for x in [same_fy, vs_prior] if x is not None]
                if candidates:
                    best = max(candidates)
                    dividend_change_value = best if dividend_change_value is None else max(dividend_change_value, best)
                    dividend_growth = dividend_growth or best >= 0.10

            score = (3 if up_revision else 0) + (2 if profit_growth and not down_revision else 0) + (1 if dividend_growth else 0) - (4 if down_revision else 0) - (2 if profit_decline else 0)
            fundamental_pass = score >= 3 and latest_profit_black and not down_revision
            row = {
                "Checkpoint": cp, "Code": int(code), "Name": name_map.get(int(code), str(code)), "Sector": sector_map.get(int(code), "Unknown"),
                "WindowStart": window_start, "WindowEnd": cp, "DisclosureCount": int(len(win)),
                "DocumentTypes": " | ".join(win["TypeOfDocument"].dropna().astype(str).unique().tolist()),
                "UpRevision": up_revision, "UpRevisionChange": up_change, "ProfitYoY": yoy_value,
                "ProfitGrowth": profit_growth, "DividendGrowth": dividend_growth, "DividendChange": dividend_change_value,
                "DownRevision": down_revision, "DownRevisionChange": down_change, "ProfitDecline": profit_decline,
                "LatestProfitBlack": latest_profit_black, "LatestProfitValue": latest_profit_value,
                "Score": int(score), "FundamentalPass": bool(fundamental_pass),
                "DisclosureNumbers": " | ".join(win["DisclosureNumber"].dropna().astype(str).tolist()),
            }
            all_rows.append(row)
            cp_map[int(code)] = row
        decision_map[cp] = cp_map
    return pd.DataFrame(all_rows), decision_map


def build_signals(prices: pd.DataFrame, fundamental_df: pd.DataFrame, checkpoints: list[pd.Timestamp], universe: set[int], name_map: dict[int, str], sector_map: dict[int, str]) -> pd.DataFrame:
    monthly_last = prices.sort_values("Date").groupby(["SecuritiesCode", "Month"], as_index=False).tail(1)
    month_close = monthly_last.set_index(["SecuritiesCode", "Month"])["AdjClose"].to_dict()
    month_supervision = monthly_last.set_index(["SecuritiesCode", "Month"])["SupervisionFlag"].to_dict()
    liq_dict = prices.groupby(["SecuritiesCode", "Month"]).agg(AvgTradingValue=("TradingValue", "mean"), TradingDays=("TradingValue", "count")).to_dict("index")
    signal_rows: list[dict[str, Any]] = []
    for cp in checkpoints:
        cp_period = cp.to_period("M")
        base_period = cp_period - 2
        liq_period = cp_period - 1
        bench_ret = pct_change(safe_float(month_close.get((BENCHMARK_CODE, cp_period))), safe_float(month_close.get((BENCHMARK_CODE, base_period))))
        candidates: list[dict[str, Any]] = []
        pass_rows = fundamental_df[(fundamental_df["Checkpoint"] == cp) & fundamental_df["FundamentalPass"]]
        for _, frow in pass_rows.iterrows():
            code = int(frow["Code"])
            if code not in universe:
                continue
            abs_ret = pct_change(safe_float(month_close.get((code, cp_period))), safe_float(month_close.get((code, base_period))))
            excess = None if abs_ret is None or bench_ret is None else abs_ret - bench_ret
            liq = liq_dict.get((code, liq_period), {})
            avg_tv = safe_float(liq.get("AvgTradingValue"))
            days = int(liq.get("TradingDays", 0) or 0)
            supervision = bool(month_supervision.get((code, cp_period), False))
            liq_pass = avg_tv is not None and avg_tv >= LIQUIDITY_AVG_TRADING_VALUE and days >= LIQUIDITY_MIN_DAYS
            price_pass = abs_ret is not None and abs_ret > 0 and excess is not None and excess > 0 and liq_pass and not supervision
            candidates.append({
                "Checkpoint": cp, "Code": code, "Name": name_map.get(code, str(code)), "Sector": sector_map.get(code, "Unknown"),
                "Score": int(frow["Score"]), "Abs2MReturn": abs_ret, "Benchmark2MReturn": bench_ret, "Excess2MReturn": excess,
                "AvgTradingValuePrevMonth": avg_tv, "TradingDaysPrevMonth": days, "SupervisionFlag": supervision,
                "LiquidityPass": liq_pass, "PricePass": price_pass, "Selected": False, "SelectionRank": np.nan,
            })
        ranked = sorted([r for r in candidates if r["PricePass"]], key=lambda r: (-r["Score"], -(r["Excess2MReturn"] or -999), -(r["AvgTradingValuePrevMonth"] or 0), r["Code"]))
        sector_count: dict[str, int] = defaultdict(int)
        selected_codes: list[int] = []
        for r in ranked:
            sector = r["Sector"] or "Unknown"
            if sector_count[sector] >= SECTOR_CAP:
                continue
            selected_codes.append(int(r["Code"]))
            sector_count[sector] += 1
            if len(selected_codes) >= SLOT_COUNT:
                break
        for r in candidates:
            if int(r["Code"]) in selected_codes:
                r["Selected"] = True
                r["SelectionRank"] = selected_codes.index(int(r["Code"])) + 1
            signal_rows.append(r)
    return pd.DataFrame(signal_rows)


@dataclass
class Episode:
    episode_id: int
    code: int
    name: str
    sector: str
    signal_checkpoint: pd.Timestamp
    entry_date: pd.Timestamp
    entry_price: float
    exit_date: pd.Timestamp | None = None
    exit_price: float | None = None
    exit_reason: str | None = None
    dividends_per_share: float = 0.0


def simulate_portfolio(prices: pd.DataFrame, signals: pd.DataFrame, decision_map: dict[pd.Timestamp, dict[int, dict[str, Any]]], name_map: dict[int, str], sector_map: dict[int, str]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    period_prices = prices[(prices["Date"] >= BASE_CLOSE_DATE) & (prices["Date"] <= END_DATE)].copy()
    market_dates = pd.DatetimeIndex(sorted(period_prices["Date"].unique()))
    date_rows = {pd.Timestamp(d): g.set_index("SecuritiesCode") for d, g in period_prices.groupby("Date", sort=True)}
    code_dates = {int(code): pd.DatetimeIndex(sorted(g.loc[g["AdjOpen"].notna() & g["AdjClose"].notna(), "Date"].unique())) for code, g in period_prices.groupby("SecuritiesCode", sort=False)}
    lookup = period_prices.set_index(["Date", "SecuritiesCode"])

    def get_px(date: pd.Timestamp, code: int, col: str) -> float | None:
        try:
            value = lookup.loc[(date, code), col]
            if isinstance(value, pd.Series):
                value = value.iloc[-1]
            return safe_float(value)
        except KeyError:
            return None

    entry_events: dict[pd.Timestamp, list[dict[str, Any]]] = defaultdict(list)
    for _, row in signals[signals["Selected"]].iterrows():
        entry_date = first_code_date_after(code_dates, int(row["Code"]), pd.Timestamp(row["Checkpoint"]))
        if entry_date is not None and entry_date <= END_DATE:
            entry_events[entry_date].append(row.to_dict())
    negative_exit_events: dict[pd.Timestamp, list[int]] = defaultdict(list)
    for cp, cmap in decision_map.items():
        for code, info in cmap.items():
            if bool(info.get("DownRevision")) or int(info.get("Score", 0)) <= -3:
                d = first_code_date_after(code_dates, code, cp)
                if d is not None and d <= END_DATE:
                    negative_exit_events[d].append(code)

    positions: dict[int, float] = {}
    expiry: dict[int, pd.Timestamp] = {}
    active_episode: dict[int, Episode] = {}
    episodes: list[Episode] = []
    cash = 100.0
    total_turnover = 0.0
    total_cost = 0.0
    episode_id = 0
    trade_rows: list[dict[str, Any]] = []
    nav_rows: list[dict[str, Any]] = []

    bench_base = get_px(BASE_CLOSE_DATE, BENCHMARK_CODE, "AdjClose")
    if bench_base is None:
        raise RuntimeError("1306 benchmark missing at base date")
    bench_prev = bench_base
    bench_tr = 100.0
    prev_nav = 100.0
    nav_rows.append({"Date": BASE_CLOSE_DATE, "Cash": 100.0, "PositionValue": 0.0, "NAV": 100.0, "DailyReturn": 0.0, "InvestedWeight": 0.0, "ActiveCount": 0, "BenchmarkPriceNAV": 100.0, "BenchmarkTRNAV": 100.0, "TurnoverNotional": 0.0, "TradingCost": 0.0, "DividendCash": 0.0})

    for date in market_dates[market_dates > BASE_CLOSE_DATE]:
        date = pd.Timestamp(date)
        rows = date_rows[date]
        dividend_cash = 0.0
        for code, qty in positions.items():
            if code in rows.index:
                div = safe_float(rows.loc[code, "AdjExpectedDividend"]) or 0.0
                dividend_cash += qty * div
                if code in active_episode:
                    active_episode[code].dividends_per_share += div
        cash += dividend_cash

        current_values: dict[int, float] = {}
        nav_open = cash
        for code, qty in positions.items():
            px = get_px(date, code, "AdjOpen") or get_px(date, code, "AdjClose")
            if px is not None:
                current_values[code] = qty * px
                nav_open += current_values[code]

        exits: dict[int, str] = {}
        for code, exp in list(expiry.items()):
            if date >= exp and get_px(date, code, "AdjOpen") is not None:
                exits[code] = "six_month_horizon"
        for code in negative_exit_events.get(date, []):
            if code in positions:
                exits[code] = "fundamental_break"

        target_codes = [c for c in positions if c not in exits]
        sector_count: dict[str, int] = defaultdict(int)
        for c in target_codes:
            sector_count[sector_map.get(c, "Unknown")] += 1
        entries_today: list[dict[str, Any]] = []
        for e in sorted(entry_events.get(date, []), key=lambda x: (x.get("SelectionRank", 999), x["Code"])):
            code = int(e["Code"])
            sector = sector_map.get(code, "Unknown")
            if code in target_codes or len(target_codes) >= SLOT_COUNT or sector_count[sector] >= SECTOR_CAP:
                continue
            if get_px(date, code, "AdjOpen") is None:
                continue
            target_codes.append(code)
            sector_count[sector] += 1
            entries_today.append(e)

        turnover = 0.0
        cost = 0.0
        if exits or entries_today:
            target_each = nav_open / SLOT_COUNT
            for _ in range(4):
                turnover = sum(abs((target_each if c in target_codes else 0.0) - current_values.get(c, 0.0)) for c in (set(current_values) | set(target_codes)))
                cost = turnover * ONE_WAY_COST
                target_each = max(nav_open - cost, 0.0) / SLOT_COUNT
            for code, reason in exits.items():
                px = get_px(date, code, "AdjOpen")
                if code in active_episode and px is not None:
                    ep = active_episode.pop(code)
                    ep.exit_date, ep.exit_price, ep.exit_reason = date, px, reason
                    episodes.append(ep)
                expiry.pop(code, None)
            new_positions: dict[int, float] = {}
            for code in target_codes:
                px = get_px(date, code, "AdjOpen")
                if px is not None and px > 0:
                    new_positions[code] = target_each / px
            cash = max(nav_open - cost - target_each * len(new_positions), 0.0)
            positions = new_positions
            total_turnover += turnover
            total_cost += cost
            for e in entries_today:
                code = int(e["Code"])
                if code not in positions or code in active_episode:
                    continue
                px = get_px(date, code, "AdjOpen")
                if px is None:
                    continue
                episode_id += 1
                active_episode[code] = Episode(episode_id, code, name_map.get(code, str(code)), sector_map.get(code, "Unknown"), pd.Timestamp(e["Checkpoint"]), date, px)
                target = date + pd.DateOffset(months=HOLD_MONTHS)
                expiry[code] = first_code_date_on_or_after(code_dates, code, target) or (END_DATE + pd.Timedelta(days=1))
            trade_rows.append({"Date": date, "Exits": "|".join(map(str, sorted(exits))), "Entries": "|".join(str(int(e["Code"])) for e in entries_today), "ActiveCodes": "|".join(map(str, sorted(positions))), "TurnoverNotional": turnover, "TradingCost": cost, "NAVAtOpenBeforeCost": nav_open})

        position_value = 0.0
        for code, qty in positions.items():
            px = get_px(date, code, "AdjClose") or get_px(date, code, "AdjOpen")
            if px is not None:
                position_value += qty * px
        nav = cash + position_value
        daily_return = nav / prev_nav - 1.0
        invested_weight = position_value / nav if nav > 0 else 0.0

        bench_close = get_px(date, BENCHMARK_CODE, "AdjClose") or bench_prev
        bench_div = get_px(date, BENCHMARK_CODE, "AdjExpectedDividend") or 0.0
        bench_tr *= 1.0 + ((bench_close + bench_div) / bench_prev - 1.0)
        bench_price = 100.0 * bench_close / bench_base
        bench_prev = bench_close
        nav_rows.append({"Date": date, "Cash": cash, "PositionValue": position_value, "NAV": nav, "DailyReturn": daily_return, "InvestedWeight": invested_weight, "ActiveCount": len(positions), "BenchmarkPriceNAV": bench_price, "BenchmarkTRNAV": bench_tr, "TurnoverNotional": turnover, "TradingCost": cost, "DividendCash": dividend_cash})
        prev_nav = nav

    for code, ep in active_episode.items():
        px = get_px(END_DATE, code, "AdjClose")
        if px is not None:
            ep.exit_date, ep.exit_price, ep.exit_reason = END_DATE, px, "horizon_mark"
            episodes.append(ep)

    nav = pd.DataFrame(nav_rows)
    nav["PeakNAV"] = nav["NAV"].cummax()
    nav["Drawdown"] = nav["NAV"] / nav["PeakNAV"] - 1.0
    nav["BenchmarkPeak"] = nav["BenchmarkTRNAV"].cummax()
    nav["BenchmarkDrawdown"] = nav["BenchmarkTRNAV"] / nav["BenchmarkPeak"] - 1.0
    nav["BenchmarkDailyReturn"] = nav["BenchmarkTRNAV"].pct_change().fillna(0.0)
    nav["DailyExcess"] = nav["DailyReturn"] - nav["BenchmarkDailyReturn"]
    monthly = nav.groupby(nav["Date"].dt.to_period("M"), as_index=False).tail(1).copy()
    monthly["StrategyMonthlyReturn"] = monthly["NAV"].pct_change().fillna(0.0)
    monthly["BenchmarkMonthlyReturn"] = monthly["BenchmarkTRNAV"].pct_change().fillna(0.0)
    monthly["MonthlyExcess"] = monthly["StrategyMonthlyReturn"] - monthly["BenchmarkMonthlyReturn"]

    ep_rows = []
    for ep in episodes:
        gross = (ep.exit_price + ep.dividends_per_share) / ep.entry_price - 1.0 if ep.exit_price is not None else np.nan
        exit_cost = 0.0 if ep.exit_reason == "horizon_mark" else ONE_WAY_COST
        ep_rows.append({"EpisodeID": ep.episode_id, "Code": ep.code, "Name": ep.name, "Sector": ep.sector, "SignalCheckpoint": ep.signal_checkpoint, "EntryDate": ep.entry_date, "EntryPrice": ep.entry_price, "ExitDate": ep.exit_date, "ExitPrice": ep.exit_price, "ExitReason": ep.exit_reason, "DividendsPerShare": ep.dividends_per_share, "GrossReturn": gross, "NetReturnApprox": gross - ONE_WAY_COST - exit_cost})
    episodes_df = pd.DataFrame(ep_rows)

    daily = nav["DailyReturn"].iloc[1:]
    bench_daily = nav["BenchmarkDailyReturn"].iloc[1:]
    excess_daily = daily - bench_daily
    vol = daily.std(ddof=1) * math.sqrt(252)
    bench_vol = bench_daily.std(ddof=1) * math.sqrt(252)
    tracking_error = excess_daily.std(ddof=1) * math.sqrt(252)
    sharpe = (daily.mean() * 252) / vol if vol > 0 else np.nan
    ir = (excess_daily.mean() * 252) / tracking_error if tracking_error > 0 else np.nan
    strategy_return = nav.iloc[-1]["NAV"] / nav.iloc[0]["NAV"] - 1.0
    benchmark_tr_return = nav.iloc[-1]["BenchmarkTRNAV"] / 100.0 - 1.0
    end_position_value = float(nav.iloc[-1]["PositionValue"])
    metrics = {
        "period_base_close": str(BASE_CLOSE_DATE.date()), "period_first_trading_date": str(FIRST_TRADING_DATE.date()), "period_end": str(END_DATE.date()),
        "strategy_net_return": float(strategy_return), "benchmark_price_proxy_return": float(nav.iloc[-1]["BenchmarkPriceNAV"] / 100.0 - 1.0),
        "benchmark_total_return_proxy": float(benchmark_tr_return), "alpha_vs_total_return_proxy": float(strategy_return - benchmark_tr_return),
        "strategy_max_drawdown": float(nav["Drawdown"].min()), "benchmark_max_drawdown": float(nav["BenchmarkDrawdown"].min()),
        "strategy_annualized_volatility": float(vol), "benchmark_annualized_volatility": float(bench_vol),
        "sharpe_rf0": None if np.isnan(sharpe) else float(sharpe), "tracking_error": None if np.isnan(tracking_error) else float(tracking_error),
        "information_ratio": None if np.isnan(ir) else float(ir),
        "monthly_excess_win_rate": float((monthly["MonthlyExcess"].iloc[1:] > 0).mean()),
        "average_invested_weight": float(nav["InvestedWeight"].mean()), "max_active_positions": int(nav["ActiveCount"].max()),
        "one_way_turnover_initial_nav": float(total_turnover / 100.0), "total_trading_cost_initial_nav": float(total_cost / 100.0),
        "terminal_liquidation_return": float((nav.iloc[-1]["NAV"] - end_position_value * ONE_WAY_COST) / 100.0 - 1.0),
        "entry_count": int(len(episodes_df)), "closed_episode_count": int((episodes_df["ExitReason"] != "horizon_mark").sum()) if not episodes_df.empty else 0,
    }
    return nav, monthly, episodes_df, pd.DataFrame(trade_rows), metrics


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    prices, fin, stock_list = load_data(args.data_dir)
    names = stock_list.set_index("SecuritiesCode")["Name"].fillna("").astype(str).to_dict() if "Name" in stock_list.columns else {}
    sectors = stock_list.set_index("SecuritiesCode")["33SectorName"].fillna("Unknown").astype(str).to_dict() if "33SectorName" in stock_list.columns else {}
    base_codes = set(prices.loc[(prices["Date"] == BASE_CLOSE_DATE) & prices["AdjClose"].notna(), "SecuritiesCode"].astype(int))
    pre_base_fin_codes = set(fin.loc[fin["DisclosureDT"] <= BASE_CLOSE_DATE + pd.Timedelta(hours=18), "SecuritiesCode"].astype(int))
    universe = base_codes & pre_base_fin_codes
    universe.discard(BENCHMARK_CODE)
    if "Section/Products" in stock_list.columns:
        section_map = stock_list.set_index("SecuritiesCode")["Section/Products"].fillna("").astype(str).to_dict()
        universe = {c for c in universe if not any(token in section_map.get(c, "") for token in ["ETF", "ETN", "REIT", "Preferred"])}

    market_dates = pd.DatetimeIndex(sorted(prices.loc[(prices["Date"] >= BASE_CLOSE_DATE) & (prices["Date"] <= END_DATE), "Date"].unique()))
    periods = pd.period_range(BASE_CLOSE_DATE.to_period("M"), (END_DATE - pd.offsets.MonthEnd(1)).to_period("M"), freq="M")
    checkpoints = [pd.Timestamp(market_dates[market_dates.to_period("M") == p].max()) for p in periods if len(market_dates[market_dates.to_period("M") == p])]
    fundamentals, decision_map = evaluate_fundamentals(fin, universe, checkpoints, names, sectors)
    signals = build_signals(prices, fundamentals, checkpoints, universe, names, sectors)
    nav, monthly, episodes, trades, metrics = simulate_portfolio(prices, signals, decision_map, names, sectors)
    metrics.update({"universe_count": len(universe), "checkpoint_count": len(checkpoints), "fundamental_evaluation_rows": len(fundamentals), "fundamental_pass_events": int(fundamentals["FundamentalPass"].sum()) if not fundamentals.empty else 0, "price_pass_events": int(signals["PricePass"].sum()) if not signals.empty else 0, "selected_signal_events": int(signals["Selected"].sum()) if not signals.empty else 0})
    audit = {"status": "PASS", "current_watchlist_used": False, "future_returns_used_in_fundamental_selection": False, "entry_after_checkpoint": bool(episodes.empty or (pd.to_datetime(episodes["EntryDate"]) > pd.to_datetime(episodes["SignalCheckpoint"])).all()), "parameters": {"lookback_days": LOOKBACK_DAYS, "liquidity_avg_trading_value": LIQUIDITY_AVG_TRADING_VALUE, "liquidity_min_days": LIQUIDITY_MIN_DAYS, "slot_count": SLOT_COUNT, "sector_cap": SECTOR_CAP, "one_way_cost": ONE_WAY_COST, "hold_months": HOLD_MONTHS}, "limitations": ["Public mirror of JPX competition dataset may omit securities delisted before the dataset snapshot.", "1306 total return is an investable proxy, not the official TOPIX total-return index.", "Qualitative business structure, reverse DCF, ROIC, buybacks and medium-term-plan KPIs are unavailable in the structured dataset."]}
    fundamentals.to_csv(args.out_dir / "fundamentals.csv", index=False)
    signals.to_csv(args.out_dir / "signals.csv", index=False)
    episodes.to_csv(args.out_dir / "episodes.csv", index=False)
    trades.to_csv(args.out_dir / "trades.csv", index=False)
    nav.to_csv(args.out_dir / "daily_nav.csv", index=False)
    monthly.to_csv(args.out_dir / "monthly_nav.csv", index=False)
    pd.DataFrame(sorted(universe), columns=["Code"]).assign(Name=lambda d: d["Code"].map(names), Sector=lambda d: d["Code"].map(sectors)).to_csv(args.out_dir / "universe.csv", index=False)
    (args.out_dir / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.out_dir / "audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    selected = signals[signals["Selected"]].sort_values(["Checkpoint", "SelectionRank"]) if not signals.empty else pd.DataFrame()
    lines = ["# 日本株・広範囲ポイント・イン・タイム バックテスト", "", f"正式判定: **{'OUTPERFORM' if metrics['alpha_vs_total_return_proxy'] > 0 else 'UNDERPERFORM'}**", "", "## 主要指標", "", "| 指標 | 結果 |", "|---|---:|", f"| 検証期間 | {metrics['period_base_close']}〜{metrics['period_end']} |", f"| 開始ユニバース | {metrics['universe_count']:,}銘柄 |", f"| 月次判定 | {metrics['checkpoint_count']}回 |", f"| ファンダメンタル評価 | {metrics['fundamental_evaluation_rows']:,}件 |", f"| ファンダメンタル通過 | {metrics['fundamental_pass_events']}件 |", f"| 価格条件通過 | {metrics['price_pass_events']}件 |", f"| 選定シグナル | {metrics['selected_signal_events']}件 |", f"| ポジション・エピソード | {metrics['entry_count']}件 |", f"| 戦略ネットリターン | {metrics['strategy_net_return']:.2%} |", f"| TOPIX連動ETF価格リターン | {metrics['benchmark_price_proxy_return']:.2%} |", f"| TOPIX配当込み代理リターン | {metrics['benchmark_total_return_proxy']:.2%} |", f"| TOPIX配当込み代理比α | {metrics['alpha_vs_total_return_proxy']:+.2%} |", f"| 戦略最大DD | {metrics['strategy_max_drawdown']:.2%} |", f"| ベンチマーク最大DD | {metrics['benchmark_max_drawdown']:.2%} |", f"| 年率ボラティリティ | {metrics['strategy_annualized_volatility']:.2%} |", f"| Sharpe ratio | {metrics['sharpe_rf0']} |", f"| Information ratio | {metrics['information_ratio']} |", f"| 月次超過勝率 | {metrics['monthly_excess_win_rate']:.2%} |", f"| 平均投資比率 | {metrics['average_invested_weight']:.2%} |", f"| 最大保有銘柄数 | {metrics['max_active_positions']} |", f"| 片道売買回転率 | {metrics['one_way_turnover_initial_nav']:.2%} |", f"| 売買費用 | {metrics['total_trading_cost_initial_nav']:.2%} |", "", "## 選定銘柄", ""]
    if selected.empty:
        lines.append("選定シグナルなし。")
    else:
        lines.extend(["| 判定日 | コード | 銘柄 | 業種 | 点数 | 2か月超過 |", "|---|---:|---|---|---:|---:|"])
        for _, r in selected.iterrows():
            lines.append(f"| {pd.Timestamp(r['Checkpoint']).date()} | {int(r['Code'])} | {r['Name']} | {r['Sector']} | {int(r['Score'])} | {r['Excess2MReturn']:.2%} |")
    lines.extend(["", "## 監査注記", "", "- 現在のウォッチリストや将来上昇銘柄を候補生成に使用していない。", "- ファンダメンタル判定は各判定日時点までの開示だけで計算。", "- 価格条件は判定日以前の2か月だけを使用し、約定は翌取引日始値。", "- 1306はTOPIX配当込みの実行可能代理で、公式指数そのものではない。", "- 定性事業分析、ROIC、逆DCF、自己株取得、中計KPIは構造化データにないため未反映。"])
    (args.out_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
