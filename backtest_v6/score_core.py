from __future__ import annotations

import argparse, csv, re, subprocess, tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, asdict
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup

UA={"User-Agent":"Mozilla/5.0 (compatible; v6-research-audit/1.1)"}

PROFIT_KEYS=["OperatingIncomeIFRS","OperatingIncome","OrdinaryIncome","ProfitBeforeTaxIFRS","ProfitBeforeTax","ProfitAttributableToOwnersOfParentIFRS","ProfitAttributableToOwnersOfParent","NetIncome"]
NET_PROFIT_KEYS=["ProfitAttributableToOwnersOfParentIFRS","ProfitAttributableToOwnersOfParent","ProfitIFRS","NetIncome"]
CHANGE_KEYS=["ChangeInOperatingIncomeIFRS","ChangeInOperatingIncome","ChangeInOrdinaryIncome","ChangeInProfitBeforeTaxIFRS","ChangeInProfitBeforeTax","ChangeInProfitAttributableToOwnersOfParentIFRS","ChangeInNetIncome"]
EPS_KEYS=["BasicEarningsPerShareIFRS","EarningsPerShare","NetIncomePerShare"]
DIV_KEY="DividendPerShare"
ZEN=str.maketrans("０１２３４５６７８９", "0123456789")

@dataclass
class EventScore:
    Code:int; EventDate:str; CoreScore:int; FundamentalPass:int; BlockMinus5:int
    LatestProfitPositive:int; LatestNetProfit:float|None; ForecastPeriod:str
    Plus4Forecast:int; Plus2AProfitGrowth:int; Plus2BBuyback:int; Plus1ADividend:int
    Plus2CCapitalReallocation:int; Plus2DRestructuring:int; Plus1BKPI:int
    Minus5ForecastOrAudit:int; Minus3Dilution:int; Minus2AProfitDecline:int; Minus2BExecutionGap:int
    YoYProfitChange:float|None; ForecastChangePct:float|None; DividendChangePct:float|None; BuybackPct:float|None
    PrimaryTrigger:int; SourceTitles:str; SourceURLs:str; Notes:str

def get(url,timeout=45):
    r=requests.get(url,timeout=timeout,headers=UA); r.raise_for_status(); return r

def text_num(s):
    s=(s or "").replace(",","").replace("−","-").replace("△","-").strip()
    if not s or s in {"-","―","－"}: return None
    try:return float(s)
    except:return None

def ix_facts(url):
    if not url:return []
    r=get(url); r.encoding="utf-8"; soup=BeautifulSoup(r.text,"html.parser")
    out=[]
    for tag in soup.find_all(True):
        n=(tag.name or "").lower()
        if not (n.endswith("nonfraction") or n.endswith("nonnumeric")):continue
        name=tag.get("name",""); ctx=tag.get("contextref",tag.get("contextRef","")); val=tag.get_text(" ",strip=True)
        scale=tag.get("scale"); x=text_num(val)
        if x is not None and scale is not None:
            try:x*=10**int(scale)
            except:pass
        out.append((name.split(":")[-1],ctx,x,val))
    return out

def forecast_period(facts):
    for name,ctx,x,raw in facts:
        if name=="TitleForForecasts" and raw:
            s=str(raw).translate(ZEN)
            m=re.search(r"(20\d{2})年\s*(\d{1,2})月期",s)
            if m:return f"{m.group(1)}-{int(m.group(2)):02d}"
    return ""

def pick_forecast(facts,keys):
    candidates=[]
    for name,ctx,x,raw in facts:
        if x is None or name not in keys:continue
        if "ForecastMember" not in ctx or "LowerMember" in ctx or "UpperMember" in ctx:continue
        if "YearDuration" not in ctx:continue
        score=(3 if "NextYearDuration" in ctx else 2 if "CurrentYearDuration" in ctx else 1)+(2 if "ConsolidatedMember" in ctx else 0)
        candidates.append((score,x))
    return max(candidates,default=(None,None),key=lambda z:z[0])[1]

def pick_current_profit(facts):
    candidates=[]
    for name,ctx,x,raw in facts:
        if x is None or name not in NET_PROFIT_KEYS or "ResultMember" not in ctx:continue
        score=4 if "CurrentAccumulated" in ctx else 3 if "CurrentYearDuration" in ctx else 1
        if "ConsolidatedMember" in ctx:score+=2
        candidates.append((score,x))
    return max(candidates,default=(None,None),key=lambda z:z[0])[1]

def pick_change(facts):
    candidates=[]
    for name,ctx,x,raw in facts:
        if x is None or name not in CHANGE_KEYS or "ResultMember" not in ctx:continue
        score=3 if "CurrentAccumulated" in ctx else 2 if "CurrentYearDuration" in ctx else 1
        if "ConsolidatedMember" in ctx:score+=2
        candidates.append((score,x))
    return max(candidates,default=(None,None),key=lambda z:z[0])[1]

def pick_dividend(facts):
    vals=[]
    for name,ctx,x,raw in facts:
        if name!=DIV_KEY or x is None or "AnnualMember" not in ctx or "ForecastMember" not in ctx or "LowerMember" in ctx or "UpperMember" in ctx:continue
        score=3 if "NextYearDuration" in ctx else 2 if "CurrentYearDuration" in ctx else 1
        vals.append((score,x))
    return max(vals,default=(None,None),key=lambda z:z[0])[1]

def pdf_text(url):
    if not url:return ""
    r=get(url,60)
    with tempfile.TemporaryDirectory() as td:
        p=Path(td)/"a.pdf"; t=Path(td)/"a.txt"; p.write_bytes(r.content)
        subprocess.run(["pdftotext","-layout",str(p),str(t)],check=False,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        return t.read_text(encoding="utf-8",errors="replace") if t.exists() else ""

def buyback_pct(rows):
    best=None
    for _,r in rows.iterrows():
        title=str(r.get("title",r.get("Title","")))
        if not ("自己株式" in title and ("取得に係る事項の決定" in title or "取得の決定" in title or "取得及び" in title)):continue
        try:txt=pdf_text(str(r.get("pdf_url",r.get("PDFURL",""))))
        except:txt=""
        for pat in [r"発行済株式総数[^\n]{0,160}?に対する割合[^0-9]{0,40}([0-9]+(?:\.[0-9]+)?)\s*[％%]",r"取得し得る株式の総数[^\n]{0,250}?([0-9]+(?:\.[0-9]+)?)\s*[％%]"]:
            m=re.search(pat,txt,re.S)
            if m:
                x=float(m.group(1));best=x if best is None else max(best,x)
    return best

def detect_dilution(rows):
    return int(any(any(k in str(r.get("title",r.get("Title",""))) for k in ["第三者割当による新株式","公募増資","新株式発行","転換社債型新株予約権付社債"]) for _,r in rows.iterrows()))

def semantic_points(rows):
    p2c=p2d=p1b=gap=0;texts=[]
    for _,r in rows.iterrows():
        title=str(r.get("title",r.get("Title","")))
        if not any(k in title for k in ["中期経営","経営計画","資本コスト","政策保有","事業ポートフォリオ","構造改革","事業再編","固定費","受注","月次","稼働率","単価"]):continue
        try:txt=pdf_text(str(r.get("pdf_url",r.get("PDFURL",""))))[:200000]
        except:txt=""
        texts.append(title+"\n"+txt)
    alltxt="\n".join(texts)
    if alltxt:
        quant_cap=bool(re.search(r"(?:ROE|ROIC|FCF|フリー.?キャッシュ.?フロー)[^\n]{0,80}?(?:[0-9]+(?:\.[0-9]+)?\s*[%％]|[0-9,]+\s*億円)",alltxt,re.I));action_cap=any(k in alltxt for k in ["政策保有株式","売却","撤退","事業ポートフォリオ","資産圧縮","投資配分","不採算事業"]);p2c=2 if quant_cap and action_cap else 0
        restruct=any(k in alltxt for k in ["構造改革","事業再編","固定費削減","コスト削減"]);quant_effect=bool(re.search(r"(?:営業利益|事業利益|FCF|フリー.?キャッシュ.?フロー)[^\n]{0,100}?(?:改善|効果|削減)[^\n]{0,80}?[0-9,]+\s*億円",alltxt));p2d=2 if restruct and quant_effect else 0
        m=re.search(r"Book[- ]?to[- ]?Bill[^0-9]{0,30}([0-9]+(?:\.[0-9]+)?)",alltxt,re.I);p1b=1 if m and float(m.group(1))>1.05 else 0
    return p2c,p2d,p1b,gap

def score_code(code,df):
    df=df.sort_values(["disclosure_date","category","title"]).copy();fin=df[(df["category"]=="financial_results")&df["ixbrl_url"].fillna("").ne("")].copy();facts_cache={}
    for idx,r in fin.iterrows():
        try:facts_cache[idx]=ix_facts(r["ixbrl_url"])
        except:facts_cache[idx]=[]
    prev_fc_by_period={};prev_div_by_period={};latest_profit=None;events=[]
    for dt,day in df.groupby("disclosure_date",sort=True):
        fday=fin[fin["disclosure_date"]==dt];current_facts=facts_cache.get(fday.index[-1],[]) if not fday.empty else []
        period=forecast_period(current_facts) if current_facts else "";fc=pick_forecast(current_facts,PROFIT_KEYS+EPS_KEYS) if current_facts else None;yoy=pick_change(current_facts) if current_facts else None;div=pick_dividend(current_facts) if current_facts else None;cur_profit=pick_current_profit(current_facts) if current_facts else None
        if cur_profit is not None:latest_profit=cur_profit
        prev_fc=prev_fc_by_period.get(period) if period else None;prev_div=prev_div_by_period.get(period) if period else None
        fchg=(fc/prev_fc-1)*100 if fc is not None and prev_fc not in (None,0) else None;dchg=(div/prev_div-1)*100 if div is not None and prev_div not in (None,0) else None
        p4=4 if fchg is not None and fchg>=5 else 0;m5=5 if fchg is not None and fchg<=-5 else 0;p2a=2 if yoy is not None and yoy>=15 and (fchg is None or fchg>=-0.01) else 0;m2a=2 if yoy is not None and yoy<=-20 else 0;p1a=1 if dchg is not None and dchg>=10 else 0
        bp=buyback_pct(day);p2b=2 if bp is not None and bp>=1 and not detect_dilution(day) else 0;m3=3 if detect_dilution(day) else 0;p2c,p2d,p1b,gap=semantic_points(day);titles=" | ".join(day["title"].astype(str).tolist())
        if any(k in titles for k in ["継続企業の前提","監査意見","不適切会計","粉飾","訂正有価証券報告書"]):m5=max(m5,5)
        score=p4+p2a+p2b+p1a+p2c+p2d+p1b-m5-m3-m2a-gap;primary=1 if any([p4,p2a,p2b,p2c,p2d]) else 0;profit_positive=int(latest_profit is not None and latest_profit>0);passed=int(score>=4 and m5==0 and primary==1 and profit_positive==1)
        urls=" | ".join([u for u in day["pdf_url"].fillna("").astype(str).tolist()+day["ixbrl_url"].fillna("").astype(str).tolist() if u])
        events.append(EventScore(int(code),dt,int(score),passed,int(m5>0),profit_positive,latest_profit,period,p4,p2a,p2b,p1a,p2c,p2d,p1b,m5,m3,m2a,gap,yoy,fchg,dchg,bp,primary,titles,urls,"Same-fiscal-period forecast revisions only; latest reported net profit must be positive; C/D/KPI use strict primary-text extraction"))
        if period and fc is not None:prev_fc_by_period[period]=fc
        if period and div is not None:prev_div_by_period[period]=div
    return events

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--disclosures",type=Path,required=True);ap.add_argument("--out",type=Path,required=True);ap.add_argument("--workers",type=int,default=8);args=ap.parse_args();df=pd.read_csv(args.disclosures)
    if df.empty:args.out.parent.mkdir(parents=True,exist_ok=True);pd.DataFrame(columns=list(EventScore.__annotations__)).to_csv(args.out,index=False);return
    all_events=[];groups=list(df.groupby("code"))
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs={ex.submit(score_code,c,g):c for c,g in groups}
        for n,f in enumerate(as_completed(futs),1):
            try:all_events.extend(f.result())
            except Exception as e:print("SCORE_ERROR",futs[f],repr(e))
            if n%25==0 or n==len(futs):print("scored",n,"of",len(futs),"events",len(all_events))
    all_events.sort(key=lambda x:(x.EventDate,x.Code));args.out.parent.mkdir(parents=True,exist_ok=True)
    with args.out.open("w",newline="",encoding="utf-8-sig") as f:w=csv.DictWriter(f,fieldnames=list(EventScore.__annotations__.keys()));w.writeheader();[w.writerow(asdict(x)) for x in all_events]
    print("event_rows",len(all_events),"fundamental_pass",sum(x.FundamentalPass for x in all_events))
if __name__=="__main__":main()
