from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import score_core as base

ZEN=str.maketrans("０１２３４５６７８９．－＋％", "0123456789.-+%")
ORIG=base.score_code

def standalone_revision_profit_pct(rows):
    """Read the latest full-year forecast revision table from a primary TDnet PDF.
    Deterministic conservative rule: use the last 増減率 block; first numeric column is sales,
    remaining numeric columns are profit/EPS fields. Mixed +5%/-5% directions are treated as a block, not a buy.
    """
    ups=[];downs=[]
    for _,r in rows.iterrows():
        title=str(r.get("title",r.get("Title","")))
        if "業績予想" not in title or not any(k in title for k in ["修正","変更"]):continue
        if any(k in title for k in ["第1四半期","第１四半期","第2四半期","第２四半期","中間期"]) and "通期" not in title:continue
        try:txt=base.pdf_text(str(r.get("pdf_url",r.get("PDFURL",""))))
        except:continue
        blocks=[];lines=txt.translate(ZEN).splitlines()
        for i,line in enumerate(lines):
            if "増減率" not in line:continue
            block=" ".join(lines[i:i+3]);vals=[]
            for x in re.findall(r"(?<![A-Za-z0-9])[-+]?\d+(?:\.\d+)?(?=\s*[%％]?(?:\s|$))",block):
                try:
                    v=float(x)
                    if -1000<=v<=5000:vals.append(v)
                except:pass
            if len(vals)>=2:blocks.append(vals)
        if not blocks:continue
        vals=blocks[-1]
        profit_vals=vals[1:] if len(vals)>=2 else vals
        if profit_vals:
            ups.append(max(profit_vals));downs.append(min(profit_vals))
    return (max(ups) if ups else None, min(downs) if downs else None)

def patched_score_code(code,df):
    events=ORIG(code,df);by_date={e.EventDate:e for e in events}
    for dt,day in df.groupby("disclosure_date",sort=True):
        e=by_date.get(dt)
        if e is None:continue
        up,down=standalone_revision_profit_pct(day)
        if up is None and down is None:continue
        # Conservative conflict handling: a material cut in another profit line blocks the +4 trigger.
        if down is not None and down<=-5:
            e.Minus5ForecastOrAudit=max(e.Minus5ForecastOrAudit,5);e.BlockMinus5=1;e.Plus4Forecast=0
        elif up is not None and up>=5:
            e.Plus4Forecast=4
        e.CoreScore=(e.Plus4Forecast+e.Plus2AProfitGrowth+e.Plus2BBuyback+e.Plus1ADividend+e.Plus2CCapitalReallocation+e.Plus2DRestructuring+e.Plus1BKPI-e.Minus5ForecastOrAudit-e.Minus3Dilution-e.Minus2AProfitDecline-e.Minus2BExecutionGap)
        e.PrimaryTrigger=int(any([e.Plus4Forecast,e.Plus2AProfitGrowth,e.Plus2BBuyback,e.Plus2CCapitalReallocation,e.Plus2DRestructuring]))
        e.FundamentalPass=int(e.CoreScore>=4 and e.Minus5ForecastOrAudit==0 and e.PrimaryTrigger==1 and e.LatestProfitPositive==1)
        e.Notes += f"; standalone forecast-revision PDF profit range down={down} up={up}"
    return events

base.score_code=patched_score_code
if __name__=="__main__":
    base.main()
