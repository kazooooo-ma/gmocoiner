from __future__ import annotations
import argparse, sys
from pathlib import Path
import pandas as pd
import run_backtest as bt
import run_wrapper

def main():
    p=argparse.ArgumentParser(); p.add_argument('--base',required=True); p.add_argument('--end',required=True); p.add_argument('--data-dir',type=Path,required=True); p.add_argument('--out-dir',type=Path,required=True); a=p.parse_args()
    bt.BASE_CLOSE_DATE=pd.Timestamp(a.base); bt.END_DATE=pd.Timestamp(a.end); bt.FIRST_TRADING_DATE=bt.BASE_CLOSE_DATE+pd.Timedelta(days=1)
    sys.argv=['run_wrapper.py','--data-dir',str(a.data_dir),'--out-dir',str(a.out_dir)]
    run_wrapper.main()
if __name__=='__main__': main()
