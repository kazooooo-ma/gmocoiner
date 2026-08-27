from __future__ import annotations
import argparse, glob
from pathlib import Path
import pandas as pd

def cat(pattern):
    fs=sorted(glob.glob(pattern)); frames=[]
    for f in fs:
        try: frames.append(pd.read_csv(f))
        except pd.errors.EmptyDataError: pass
    return pd.concat(frames,ignore_index=True) if frames else pd.DataFrame()

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--root',type=Path,required=True);ap.add_argument('--out-dir',type=Path,required=True);args=ap.parse_args();args.out_dir.mkdir(parents=True,exist_ok=True)
    d=cat(str(args.root/'**/disclosures_*.csv')); e=cat(str(args.root/'**/disclosures_*_errors.csv')); f=cat(str(args.root/'**/fundamentals_*.csv'))
    if not d.empty:
        d=d.drop_duplicates(subset=['code','disclosure_date','category','title','pdf_url'],keep='first').sort_values(['code','disclosure_date','category','title'])
    if not f.empty:
        f=f.drop_duplicates(subset=['Code','EventDate'],keep='first').sort_values(['EventDate','Code'])
    d.to_csv(args.out_dir/'V6_Disclosures.csv',index=False,encoding='utf-8-sig');e.to_csv(args.out_dir/'V6_DisclosureErrors.csv',index=False,encoding='utf-8-sig');f.to_csv(args.out_dir/'V6_Fundamentals.csv',index=False,encoding='utf-8-sig')
    print('disclosures',len(d),'errors',len(e),'fundamental_events',len(f),'passes',int(f['FundamentalPass'].sum()) if not f.empty and 'FundamentalPass' in f else 0)
    if not e.empty: print('error_codes',e['Code'].astype(str).head(100).tolist())
if __name__=='__main__':main()
