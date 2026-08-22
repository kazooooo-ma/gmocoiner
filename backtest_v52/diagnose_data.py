from pathlib import Path
import argparse
import pandas as pd
import run_backtest as bt

parser = argparse.ArgumentParser()
parser.add_argument('--data-dir', type=Path, required=True)
args = parser.parse_args()
prices, fin, stock_list = bt.load_data(args.data_dir)
print('PRICE_RANGE', prices['Date'].min(), prices['Date'].max())
print('PRICE_ROWS', len(prices), 'PRICE_CODES', prices['SecuritiesCode'].nunique())
print('FIN_RANGE', fin['DisclosureDT'].min(), fin['DisclosureDT'].max())
print('FIN_ROWS', len(fin), 'FIN_CODES', fin['SecuritiesCode'].nunique())
print('STOCK_LIST_ROWS', len(stock_list), 'STOCK_LIST_CODES', stock_list['SecuritiesCode'].nunique())
base_codes = set(prices.loc[(prices['Date'] == bt.BASE_CLOSE_DATE) & prices['AdjClose'].notna(), 'SecuritiesCode'].astype(int))
prebase_codes = set(fin.loc[fin['DisclosureDT'] <= bt.BASE_CLOSE_DATE + pd.Timedelta(hours=18), 'SecuritiesCode'].astype(int))
print('BASE_CODES', len(base_codes), 'PREBASE_FIN_CODES', len(prebase_codes), 'INTERSECTION', len(base_codes & prebase_codes))
market_dates = pd.DatetimeIndex(sorted(prices.loc[(prices['Date'] >= bt.BASE_CLOSE_DATE) & (prices['Date'] <= bt.END_DATE), 'Date'].unique()))
print('MARKET_DATES', len(market_dates), 'FIRST_LAST', market_dates.min() if len(market_dates) else None, market_dates.max() if len(market_dates) else None)
periods = pd.period_range(bt.BASE_CLOSE_DATE.to_period('M'), (bt.END_DATE - pd.offsets.MonthEnd(1)).to_period('M'), freq='M')
checkpoints = []
for period in periods:
    dates = market_dates[market_dates.to_period('M') == period]
    if len(dates):
        checkpoints.append(pd.Timestamp(dates.max()))
print('CHECKPOINTS', [str(x.date()) for x in checkpoints])
universe = base_codes & prebase_codes
for cp in checkpoints:
    cutoff = cp + pd.Timedelta(hours=18)
    start = cp - pd.Timedelta(days=bt.LOOKBACK_DAYS - 1)
    raw_count = len(fin[(fin['DisclosureDT'] >= start) & (fin['DisclosureDT'] <= cutoff)])
    uni_count = len(fin[(fin['DisclosureDT'] >= start) & (fin['DisclosureDT'] <= cutoff) & fin['SecuritiesCode'].isin(universe)])
    print('WINDOW', cp.date(), 'RAW', raw_count, 'UNIVERSE', uni_count)
print('FIN_SAMPLE_DATES')
print(fin[['DisclosedDate','DisclosedTime','DisclosureDT','SecuritiesCode']].head(10).to_string(index=False))
