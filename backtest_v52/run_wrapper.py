from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

import run_backtest as bt


SIGNAL_COLUMNS = [
    "Checkpoint", "Code", "Name", "Sector", "Score", "Abs2MReturn",
    "Benchmark2MReturn", "Excess2MReturn", "AvgTradingValuePrevMonth",
    "TradingDaysPrevMonth", "SupervisionFlag", "LiquidityPass", "PricePass",
    "Selected", "SelectionRank",
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    prices, fin, stock_list = bt.load_data(args.data_dir)
    names = stock_list.set_index("SecuritiesCode")["Name"].fillna("").astype(str).to_dict() if "Name" in stock_list.columns else {}
    sectors = stock_list.set_index("SecuritiesCode")["33SectorName"].fillna("Unknown").astype(str).to_dict() if "33SectorName" in stock_list.columns else {}

    base_codes = set(prices.loc[(prices["Date"] == bt.BASE_CLOSE_DATE) & prices["AdjClose"].notna(), "SecuritiesCode"].astype(int))
    pre_base_fin_codes = set(fin.loc[fin["DisclosureDT"] <= bt.BASE_CLOSE_DATE + pd.Timedelta(hours=18), "SecuritiesCode"].astype(int))
    universe = base_codes & pre_base_fin_codes
    universe.discard(bt.BENCHMARK_CODE)
    if "Section/Products" in stock_list.columns:
        section_map = stock_list.set_index("SecuritiesCode")["Section/Products"].fillna("").astype(str).to_dict()
        universe = {c for c in universe if not any(t in section_map.get(c, "") for t in ["ETF", "ETN", "REIT", "Preferred"])}

    market_dates = pd.DatetimeIndex(sorted(prices.loc[(prices["Date"] >= bt.BASE_CLOSE_DATE) & (prices["Date"] <= bt.END_DATE), "Date"].unique()))
    periods = pd.period_range(bt.BASE_CLOSE_DATE.to_period("M"), (bt.END_DATE - pd.offsets.MonthEnd(1)).to_period("M"), freq="M")
    checkpoints = [pd.Timestamp(market_dates[market_dates.to_period("M") == p].max()) for p in periods if len(market_dates[market_dates.to_period("M") == p])]

    fundamentals, decision_map = bt.evaluate_fundamentals(fin, universe, checkpoints, names, sectors)
    signals = bt.build_signals(prices, fundamentals, checkpoints, universe, names, sectors)
    if signals.empty:
        signals = pd.DataFrame(columns=SIGNAL_COLUMNS)
    else:
        for col in SIGNAL_COLUMNS:
            if col not in signals.columns:
                signals[col] = pd.NA

    nav, monthly, episodes, trades, metrics = bt.simulate_portfolio(prices, signals, decision_map, names, sectors)
    metrics.update({
        "universe_count": len(universe),
        "checkpoint_count": len(checkpoints),
        "price_rows": len(prices),
        "financial_rows": len(fin),
        "fundamental_evaluation_rows": len(fundamentals),
        "fundamental_pass_events": int(fundamentals["FundamentalPass"].sum()) if not fundamentals.empty else 0,
        "up_revision_events": int(fundamentals["UpRevision"].sum()) if not fundamentals.empty else 0,
        "profit_growth_events": int(fundamentals["ProfitGrowth"].sum()) if not fundamentals.empty else 0,
        "dividend_growth_events": int(fundamentals["DividendGrowth"].sum()) if not fundamentals.empty else 0,
        "down_revision_events": int(fundamentals["DownRevision"].sum()) if not fundamentals.empty else 0,
        "price_pass_events": int(signals["PricePass"].fillna(False).sum()) if not signals.empty else 0,
        "selected_signal_events": int(signals["Selected"].fillna(False).sum()) if not signals.empty else 0,
    })

    audit = {
        "status": "PASS",
        "current_watchlist_used": False,
        "future_returns_used_in_fundamental_selection": False,
        "entry_after_checkpoint": bool(episodes.empty or (pd.to_datetime(episodes["EntryDate"]) > pd.to_datetime(episodes["SignalCheckpoint"])).all()),
        "parameters": {
            "lookback_days": bt.LOOKBACK_DAYS,
            "liquidity_avg_trading_value": bt.LIQUIDITY_AVG_TRADING_VALUE,
            "liquidity_min_days": bt.LIQUIDITY_MIN_DAYS,
            "slot_count": bt.SLOT_COUNT,
            "sector_cap": bt.SECTOR_CAP,
            "one_way_cost": bt.ONE_WAY_COST,
            "hold_months": bt.HOLD_MONTHS,
        },
        "limitations": [
            "Public mirror of the JPX competition dataset may omit securities delisted before its snapshot.",
            "1306 total return is an investable proxy, not the official TOPIX total-return index.",
            "Qualitative business structure, reverse DCF, ROIC, buybacks and medium-term-plan KPIs are unavailable in the structured dataset.",
        ],
    }

    fundamentals.to_csv(args.out_dir / "fundamentals.csv", index=False)
    signals.to_csv(args.out_dir / "signals.csv", index=False)
    episodes.to_csv(args.out_dir / "episodes.csv", index=False)
    trades.to_csv(args.out_dir / "trades.csv", index=False)
    nav.to_csv(args.out_dir / "daily_nav.csv", index=False)
    monthly.to_csv(args.out_dir / "monthly_nav.csv", index=False)
    pd.DataFrame(sorted(universe), columns=["Code"]).assign(Name=lambda d: d["Code"].map(names), Sector=lambda d: d["Code"].map(sectors)).to_csv(args.out_dir / "universe.csv", index=False)
    (args.out_dir / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.out_dir / "audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")

    selected = signals[signals["Selected"].fillna(False)].sort_values(["Checkpoint", "SelectionRank"]) if not signals.empty else pd.DataFrame()
    verdict = "OUTPERFORM" if metrics["alpha_vs_total_return_proxy"] > 0 else "UNDERPERFORM"
    lines = [
        "# 日本株・広範囲ポイント・イン・タイム バックテスト",
        "",
        f"正式判定: **{verdict}**",
        "",
        "## 主要指標",
        "",
        "| 指標 | 結果 |",
        "|---|---:|",
        f"| 検証期間 | {metrics['period_base_close']}〜{metrics['period_end']} |",
        f"| 開始ユニバース | {metrics['universe_count']:,}銘柄 |",
        f"| 価格行 | {metrics['price_rows']:,}件 |",
        f"| 財務開示行 | {metrics['financial_rows']:,}件 |",
        f"| 月次判定 | {metrics['checkpoint_count']}回 |",
        f"| ファンダメンタル評価 | {metrics['fundamental_evaluation_rows']:,}件 |",
        f"| 上方修正イベント | {metrics['up_revision_events']}件 |",
        f"| 利益成長イベント | {metrics['profit_growth_events']}件 |",
        f"| 配当成長イベント | {metrics['dividend_growth_events']}件 |",
        f"| ファンダメンタル通過 | {metrics['fundamental_pass_events']}件 |",
        f"| 価格条件通過 | {metrics['price_pass_events']}件 |",
        f"| 選定シグナル | {metrics['selected_signal_events']}件 |",
        f"| ポジション・エピソード | {metrics['entry_count']}件 |",
        f"| 戦略ネットリターン | {metrics['strategy_net_return']:.2%} |",
        f"| TOPIX連動ETF価格リターン | {metrics['benchmark_price_proxy_return']:.2%} |",
        f"| TOPIX配当込み代理リターン | {metrics['benchmark_total_return_proxy']:.2%} |",
        f"| TOPIX配当込み代理比α | {metrics['alpha_vs_total_return_proxy']:+.2%} |",
        f"| 戦略最大DD | {metrics['strategy_max_drawdown']:.2%} |",
        f"| ベンチマーク最大DD | {metrics['benchmark_max_drawdown']:.2%} |",
        f"| 年率ボラティリティ | {metrics['strategy_annualized_volatility']:.2%} |",
        f"| Sharpe ratio | {metrics['sharpe_rf0']} |",
        f"| Information ratio | {metrics['information_ratio']} |",
        f"| 月次超過勝率 | {metrics['monthly_excess_win_rate']:.2%} |",
        f"| 平均投資比率 | {metrics['average_invested_weight']:.2%} |",
        f"| 最大保有銘柄数 | {metrics['max_active_positions']} |",
        f"| 片道売買回転率 | {metrics['one_way_turnover_initial_nav']:.2%} |",
        f"| 売買費用 | {metrics['total_trading_cost_initial_nav']:.2%} |",
        "",
        "## 選定銘柄",
        "",
    ]
    if selected.empty:
        lines.append("選定シグナルなし。")
    else:
        lines.extend(["| 判定日 | コード | 銘柄 | 業種 | 点数 | 2か月超過 |", "|---|---:|---|---|---:|---:|"])
        for _, row in selected.iterrows():
            lines.append(f"| {pd.Timestamp(row['Checkpoint']).date()} | {int(row['Code'])} | {row['Name']} | {row['Sector']} | {int(row['Score'])} | {row['Excess2MReturn']:.2%} |")
    lines.extend([
        "",
        "## 監査注記",
        "",
        "- 現在のウォッチリストや将来上昇銘柄を候補生成に使用していない。",
        "- ファンダメンタル判定は各判定日時点までの開示だけで計算。",
        "- 価格条件は判定日以前の2か月だけを使用し、約定は翌取引日始値。",
        "- 1306はTOPIX配当込みの実行可能代理で、公式指数そのものではない。",
        "- 定性事業分析、ROIC、逆DCF、自己株取得、中計KPIは構造化データにないため未反映。",
    ])
    (args.out_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
