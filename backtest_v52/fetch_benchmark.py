from pathlib import Path
import argparse
import pandas as pd
import yfinance as yf

parser = argparse.ArgumentParser()
parser.add_argument('--price-file', type=Path, required=True)
args = parser.parse_args()

prices = pd.read_csv(args.price_file, low_memory=False)
prices['SecuritiesCode'] = pd.to_numeric(prices['SecuritiesCode'], errors='coerce')
prices['Date'] = pd.to_datetime(prices['Date'], errors='coerce')
if (prices['SecuritiesCode'] == 1306).any():
    print('1306 already present')
    raise SystemExit(0)

# Use only dates on which the JPX stock dataset records at least one ordinary-equity trade.
# This removes benchmark-only rows such as the 2020-10-01 TSE full-day system outage.
stock_dates = set(prices.loc[prices['SecuritiesCode'].ne(1306) & prices['Close'].notna(), 'Date'].dropna().dt.normalize())

raw = yf.download(
    '1306.T', start='2019-01-01', end='2021-12-06',
    auto_adjust=False, actions=True, progress=False, threads=False,
)
if raw.empty:
    raise RuntimeError('yfinance returned no 1306.T data')
if isinstance(raw.columns, pd.MultiIndex):
    raw.columns = raw.columns.get_level_values(0)
raw = raw.reset_index()
raw['Date'] = pd.to_datetime(raw['Date'], errors='coerce').dt.tz_localize(None).dt.normalize()
raw = raw[raw['Date'].isin(stock_dates)].copy()
for col in ['Open','High','Low','Close','Volume','Dividends','Stock Splits']:
    if col not in raw.columns:
        raw[col] = 0.0
bench = pd.DataFrame({
    'RowId': raw['Date'].dt.strftime('%Y%m%d') + '_1306',
    'Date': raw['Date'].dt.strftime('%Y-%m-%d'),
    'SecuritiesCode': 1306,
    'Open': raw['Open'],
    'High': raw['High'],
    'Low': raw['Low'],
    'Close': raw['Close'],
    'Volume': raw['Volume'],
    'AdjustmentFactor': 1.0,
    'ExpectedDividend': raw['Dividends'].fillna(0.0),
    'SupervisionFlag': False,
})
prices['Date'] = prices['Date'].dt.strftime('%Y-%m-%d')
for col in prices.columns:
    if col not in bench.columns:
        bench[col] = pd.NA
bench = bench[prices.columns]
combined = pd.concat([prices, bench], ignore_index=True)
combined.to_csv(args.price_file, index=False)
print(f'Appended {len(bench)} rows for 1306.T on JPX stock trading dates; {bench.Date.min()} to {bench.Date.max()}')
