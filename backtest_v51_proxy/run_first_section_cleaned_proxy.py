from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

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

# Complete set of market transfers INTO TSE First Section after the fixed
# 2020-07-31 starting-universe date and through the 2021-07-30 test horizon,
# as listed in JPX-R Annual Reports 2021/2022. Names that transferred on or
# before 2020-07-31 are intentionally NOT excluded because they belong to the
# point-in-time starting universe.
POST_BASE_FIRST_SECTION_TRANSFERS = {
    # Second Section -> First Section, 2020-11-24 through 2021-02-12
    3150, 2722, 3633, 4481, 4251, 3965, 3839, 2804, 9055, 6502, 2929,
    # Mothers -> First Section, 2020-09-07 through 2021-03-11
    7038, 7059, 6095, 9279, 3446, 7172, 4427, 7037, 6096, 2489,
    4931, 2980, 4390, 4449, 6556, 4434, 4443, 3489, 3135, 3923,
    # JASDAQ -> First Section, 2020-10-19
    4765,
    # FY2021 transfers through test horizon: Second/Mothers/JASDAQ -> First
    6254, 7183, 3994, 6787,
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    prices, fin, stock_list = load_data(args.data_dir)

    equity_market_dates = set(
        prices.loc[
            (prices["SecuritiesCode"] != BENCHMARK_CODE) & prices["AdjClose"].notna(),
            "Date",
        ]
    )
    prices = prices[prices["Date"].isin(equity_market_dates)].copy()

    names = stock_list.set_index("SecuritiesCode")["Name"].fillna("").astype(str).to_dict()
    sectors = stock_list.set_index("SecuritiesCode")["33SectorName"].fillna("Unknown").astype(str).to_dict()
    section_map = stock_list.set_index("SecuritiesCode")["Section/Products"].fillna("").astype(str).to_dict()

    base_codes = set(prices.loc[(prices["Date"] == BASE_CLOSE_DATE) & prices["AdjClose"].notna(), "SecuritiesCode"].astype(int))
    pre_base_fin_codes = set(fin.loc[fin["DisclosureDT"] <= BASE_CLOSE_DATE + pd.Timedelta(hours=18), "SecuritiesCode"].astype(int))
    raw_universe = {
        c for c in (base_codes & pre_base_fin_codes)
        if section_map.get(c, "") == "First Section (Domestic)"
    }
    raw_universe.discard(BENCHMARK_CODE)
    removed_transfers = sorted(raw_universe & POST_BASE_FIRST_SECTION_TRANSFERS)
    universe = raw_universe - POST_BASE_FIRST_SECTION_TRANSFERS

    market_dates = pd.DatetimeIndex(sorted(prices.loc[(prices["Date"] >= BASE_CLOSE_DATE) & (prices["Date"] <= END_DATE), "Date"].unique()))
    periods = pd.period_range(BASE_CLOSE_DATE.to_period("M"), (END_DATE - pd.offsets.MonthEnd(1)).to_period("M"), freq="M")
    checkpoints = [pd.Timestamp(market_dates[market_dates.to_period("M") == p].max()) for p in periods if len(market_dates[market_dates.to_period("M") == p])]

    fundamentals, decision_map = evaluate_fundamentals(fin, universe, checkpoints, names, sectors)
    signals = build_signals(prices, fundamentals, checkpoints, universe, names, sectors)
    nav, monthly, episodes, trades, metrics = simulate_portfolio(prices, signals, decision_map, names, sectors)

    extreme_nav_rows = nav[nav["DailyReturn"].abs() > 0.50]
    if not extreme_nav_rows.empty:
        extreme_nav_rows.to_csv(args.out_dir / "nav_integrity_failures.csv", index=False)
        raise RuntimeError("NAV integrity check failed: daily move exceeded 50%")

    metrics.update({
        "proxy_name": "v5.1 first-section cleaned snapshot proxy",
        "formal_v51": False,
        "nav_integrity_pass": True,
        "raw_snapshot_universe_count": len(raw_universe),
        "removed_post_base_transfer_count": len(removed_transfers),
        "removed_post_base_transfer_codes": removed_transfers,
        "universe_count": len(universe),
        "checkpoint_count": len(checkpoints),
        "fundamental_evaluation_rows": len(fundamentals),
        "fundamental_pass_events": int(fundamentals["FundamentalPass"].sum()) if not fundamentals.empty else 0,
        "price_pass_events": int(signals["PricePass"].sum()) if not signals.empty else 0,
        "selected_signal_events": int(signals["Selected"].sum()) if not signals.empty else 0,
        "known_limitations": [
            "Post-2020-07-31 First Section transfer look-ahead is removed using the complete transfer sets in JPX-R Annual Reports 2021/2022 through the test horizon.",
            "The starting Section/Products snapshot is still 2021-12-30 and the public core competition price file omits many less-liquid securities; this is not the complete 2020-07-31 First Section universe.",
            "This proxy still uses the structured v5.2 fundamental score and does not yet add v5.1 buyback, quantified capital-efficiency, and order/KPI points.",
            "Dividends are not yet booked on actual payment dates as required by formal v5.1.",
            "1306 is an investable total-return proxy, not the official TOPIX total-return index.",
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
    pd.DataFrame({"Code": removed_transfers}).assign(Name=lambda d: d["Code"].map(names)).to_csv(
        args.out_dir / "removed_post_base_transfers.csv", index=False
    )
    selected = signals[signals["Selected"]].sort_values(["Checkpoint", "SelectionRank"]) if not signals.empty else pd.DataFrame()
    selected.to_csv(args.out_dir / "selected.csv", index=False)
    (args.out_dir / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
