from __future__ import annotations
import argparse, json, math
from pathlib import Path
import numpy as np
import pandas as pd

WINDOWS=[('WF-01','official_oos'),('WF-02','official_oos'),('WF-03','official_oos'),('WF-04','supplemental_oos')]

def main():
    p=argparse.ArgumentParser(); p.add_argument('--root',type=Path,required=True); a=p.parse_args()
    rows=[]; daily=[]; episodes=[]
    for window,classification in WINDOWS:
        d=a.root/window; m=json.loads((d/'metrics.json').read_text())
        alpha=m['strategy_net_return']-m['benchmark_total_return_proxy']
        rows.append({'Window':window,'Classification':classification,'BaseDate':m['period_base_close'],'EndDate':m['period_end'],'Universe':m['universe_count'],'Checkpoints':m['checkpoint_count'],'FundamentalEvaluations':m['fundamental_evaluation_rows'],'FundamentalPass':m['fundamental_pass_events'],'PricePass':m['price_pass_events'],'SelectedSignals':m['selected_signal_events'],'Entries':m['entry_count'],'StrategyReturn':m['strategy_net_return'],'BenchmarkTRReturn':m['benchmark_total_return_proxy'],'Alpha':alpha,'StrategyMaxDD':m['strategy_max_drawdown'],'BenchmarkMaxDD':m['benchmark_max_drawdown'],'InformationRatio':m['information_ratio'],'MonthlyExcessWinRate':m['monthly_excess_win_rate'],'AverageInvestedWeight':m['average_invested_weight'],'OneWayTurnover':m['one_way_turnover_initial_nav']})
        if classification=='official_oos':
            nav=pd.read_csv(d/'daily_nav.csv').iloc[1:].copy(); nav['Window']=window; daily.append(nav)
            ep=pd.read_csv(d/'episodes.csv'); ep['Window']=window; episodes.append(ep)
    summary=pd.DataFrame(rows); summary.to_csv(a.root/'walk_forward_windows.csv',index=False)
    official=summary[summary.Classification=='official_oos'].copy()
    chained_strategy=float(np.prod(1+official.StrategyReturn)-1); chained_benchmark=float(np.prod(1+official.BenchmarkTRReturn)-1); chained_alpha=chained_strategy-chained_benchmark
    median_alpha=float(official.Alpha.median()); positive_rate=float((official.Alpha>0).mean())
    daily_all=pd.concat(daily,ignore_index=True); excess=daily_all.DailyReturn-daily_all.BenchmarkDailyReturn; te=float(excess.std(ddof=1)*math.sqrt(252)); combined_ir=None if te==0 else float(excess.mean()*252/te)
    monthly_wins=monthly_count=0
    for _,f in daily_all.groupby('Window'):
        f=f.copy(); f['Date']=pd.to_datetime(f.Date); f=f.set_index('Date')
        s=f.DailyReturn.resample('ME').apply(lambda x:(1+x).prod()-1); b=f.BenchmarkDailyReturn.resample('ME').apply(lambda x:(1+x).prod()-1)
        monthly_wins+=int((s>b).sum()); monthly_count+=len(s)
    monthly_win=monthly_wins/monthly_count if monthly_count else None
    eps=pd.concat(episodes,ignore_index=True) if episodes else pd.DataFrame(); concentration=None
    if not eps.empty and 'NetReturnApprox' in eps.columns:
        wins=pd.to_numeric(eps.NetReturnApprox,errors='coerce').dropna(); wins=wins[wins>0].sort_values(ascending=False); concentration=None if wins.sum()<=0 else float(wins.head(5).sum()/wins.sum())
    avg_invested=float(official.AverageInvestedWeight.mean()); dd_gap=float((official.StrategyMaxDD-official.BenchmarkMaxDD).min()); mean_turnover=float(official.OneWayTurnover.mean())
    checks={'median_alpha_at_least_5pp':median_alpha>=.05,'positive_window_rate_at_least_60pct':positive_rate>=.60,'combined_information_ratio_at_least_0_50':combined_ir is not None and combined_ir>=.50,'worst_dd_gap_within_5pp':dd_gap>=-.05,'monthly_excess_win_rate_at_least_55pct':monthly_win is not None and monthly_win>=.55,'top5_profit_concentration_below_50pct':concentration is not None and concentration<.50,'average_invested_weight_at_least_60pct':avg_invested>=.60,'mean_one_way_turnover_at_most_600pct':mean_turnover<=6.0}
    result={'status':'PASS' if all(checks.values()) else 'FAIL','official_windows':official.Window.tolist(),'chained_strategy_return':chained_strategy,'chained_benchmark_total_return_proxy':chained_benchmark,'chained_alpha':chained_alpha,'median_annual_window_alpha':median_alpha,'positive_alpha_window_rate':positive_rate,'combined_information_ratio':combined_ir,'aggregate_monthly_excess_win_rate':monthly_win,'average_invested_weight':avg_invested,'worst_strategy_minus_benchmark_drawdown_gap':dd_gap,'top5_positive_episode_profit_concentration':concentration,'mean_one_way_turnover':mean_turnover,'total_entries':int(official.Entries.sum()),'pass_checks':checks,'reference_window_excluded':'2020-07-31 to 2021-07-30','supplemental_window':'WF-04'}
    (a.root/'walk_forward_summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    lines=['# v5.2 固定ルール型ウォークフォワード','',f"正式判定: **{result['status']}**",'','| 窓 | 区分 | 戦略 | TOPIX配当込み代理 | α | 最大DD | IR | 組入れ |','|---|---|---:|---:|---:|---:|---:|---:|']
    for r in summary.itertuples(): lines.append(f'| {r.Window} | {r.Classification} | {r.StrategyReturn:.2%} | {r.BenchmarkTRReturn:.2%} | {r.Alpha:+.2%} | {r.StrategyMaxDD:.2%} | {r.InformationRatio:.2f} | {int(r.Entries)} |')
    lines+=['','## 正式OOS合算','',f'- 連鎖戦略リターン: {chained_strategy:.2%}',f'- 連鎖TOPIX配当込み代理: {chained_benchmark:.2%}',f'- 連鎖α: {chained_alpha:+.2%}',f'- 年次窓中央値α: {median_alpha:+.2%}',f'- αプラス窓率: {positive_rate:.2%}',f'- 合算Information ratio: {combined_ir if combined_ir is not None else "N/A"}',f'- 月次超過勝率: {monthly_win:.2%}' if monthly_win is not None else '- 月次超過勝率: N/A',f'- 平均投資比率: {avg_invested:.2%}',f'- 最大DD悪化の最悪値: {dd_gap:+.2%}',f'- 上位5利益集中: {concentration:.2%}' if concentration is not None else '- 上位5利益集中: N/A','','## 採否チェック','']
    lines.extend(f"- {k}: {'PASS' if v else 'FAIL'}" for k,v in checks.items()); lines+=['','基準期間2020-07-31〜2021-07-30は確認済みのため正式OOS合算から除外した。']
    (a.root/'walk_forward_report.md').write_text('\n'.join(lines)); print('\n'.join(lines))
if __name__=='__main__': main()
