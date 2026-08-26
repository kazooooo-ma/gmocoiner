from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

# Allow direct execution from the repository root in CI without changing the
# registered backtest logic or package layout.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backtest_v52.run_backtest import (
    BASE_CLOSE_DATE,
    END_DATE,
    BENCHMARK_CODE,
    load_data,
    evaluate_fundamentals,
    build_signals,
    simulate_portfolio,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    prices, fin, stock_list = load_data(args.data_dir)

    # The benchmark provider can contain rows on dates on which TSE cash
    # equities did not trade (notably the 2020-10-01 full-day outage).  Such a
    # row must not create a portfolio valuation day with every stock missing.
    # Keep only dates on which at least one non-benchmark JPX equity has a
    # valid close.  This is a calendar/data-alignment repair, not a strategy
    # rule change.
    equity_market_dates = set(
        prices.loc[
            (prices["SecuritiesCode"] != BENCHMARK_CODE) & prices["AdjClose"].notna(),
            "Date",
        ]
    )
    prices = prices[prices["Date"].isin(equity_market_dates)].copy()

    stock_list = stock_list.copy()
    names = stock_list.set_index("SecuritiesCode")["Name"].fillna("").astype(str).to_dict()
    sectors = stock_list.set_index("SecuritiesCode")["33SectorName"].fillna("Unknown").astype(str).to_dict()
    section_map = stock_list.set_index("SecuritiesCode")["Section/Products"].fillna("").astype(str).to_dict()

    base_codes = set(prices.loc[(prices["Date"] == BASE_CLOSE_DATE) & prices["AdjClose"].notna(), "SecuritiesCode"].astype(int))
    pre_base_fin_codes = set(fin.loc[fin["DisclosureDT"] <= BASE_CLOSE_DATE + pd.Timedelta(hours=18), "SecuritiesCode"].astype(int))
    universe = {
        c for c in (base_codes & pre_base_fin_codes)
        if section_map.get(c, "") == "First Section (Domestic)"
    }
    universe.discard(BENCHMARK_CODE)

    market_dates = pd.DatetimeIndex(sorted(prices.loc[(prices["Date"] >= BASE_CLOSE_DATE) & (prices["Date"] <= END_DATE), "Date"].unique()))
    periods = pd.period_range(BASE_CLOSE_DATE.to_period("M"), (END_DATE - pd.offsets.MonthEnd(1)).to_period("M"), freq="M")
    checkpoints = [pd.Timestamp(market_dates[market_dates.to_period("M") == p].max()) for p in periods if len(market_dates[market_dates.to_period("M") == p])]

    fundamentals, decision_map = evaluate_fundamentals(fin, universe, checkpoints, names, sectors)
    signals = build_signals(prices, fundamentals, checkpoints, universe, names, sectors)
    nav, monthly, episodes, trades, metrics = simulate_portfolio(prices, signals, decision_map, names, sectors)

    # A 12-name equal-weight portfolio should not jump by orders of magnitude
    # in one day.  Fail closed if a residual missing-price/accounting defect
    # creates an implausible daily NAV move.
    extreme_nav_rows = nav[nav["DailyReturn"].abs() > 0.50]
    nav_integrity_pass = extreme_nav_rows.empty
    if not nav_integrity_pass:
        extreme_nav_rows.to_csv(args.out_dir / "nav_integrity_failures.csv", index=False)
        raise RuntimeError(
            "NAV integrity check failed: daily portfolio move exceeded 50%; "
            "inspect missing-price/accounting handling before using results."
        )

    metrics.update({
        "proxy_name": "v5.1 first-section snapshot proxy",
        "formal_v51": False,
        "nav_integrity_pass": nav_integrity_pass,
        "universe_count": len(universe),
        "checkpoint_count": len(checkpoints),
        "fundamental_evaluation_rows": len(fundamentals),
        "fundamental_pass_events": int(fundamentals["FundamentalPass"].sum()) if not fundamentals.empty else 0,
        "price_pass_events": int(signals["PricePass"].sum()) if not signals.empty else 0,
        "selected_signal_events": int(signals["Selected"].sum()) if not signals.empty else 0,
        "known_limitations": [
            "Section/Products comes from the 2021-12-30 stock-list snapshot, not the required 2020-07-31 point-in-time snapshot.",
            "The public competition mirror can omit stocks delisted before its snapshot.",
            "This proxy uses the structured v5.2 fundamental score and therefore does not yet add v5.1 buyback, quantified capital-efficiency, and order/KPI points.",
            "1306 is an investable total-return proxy, not the official TOPIX total-return index.",
            "Market-wide non-trading dates are removed using non-benchmark JPX equity closes; 2020-10-01 is therefore not treated as a valuation/trading day.",
        ],
    })

    fundamentals.to_csv(args.out_dir / "fundamentals.csv", index=False)
    signals.to_csv(args.out_dir / "signals.csv", index=False)
    episodes.to_csv(args.out_dir / "episodes.csv", index=False)
    trades.to_csv(args.out_dir / "trades.csv", index=False)
    nav.to_csv(args.out_dir / "daily_nav.csv", index=False)
    monthly.to_csv(args.out_dir / "monthly_nav.csv", index=False)
    pd.DataFrame(sorted(universe), columns=["Code"]).assign(
        Name=lambda d: d["Code"].map(names),
        Sector=lambda d: d["Code"].map(sectors),
        Section=lambda d: d["Code"].map(section_map),
    ).to_csv(args.out_dir / "universe.csv", index=False)
    (args.out_dir / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")

    selected = signals[signals["Selected"]].sort_values(["Checkpoint", "SelectionRank"]) if not signals.empty else pd.DataFrame()
    selected.to_csv(args.out_dir / "selected.csv", index=False)
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
