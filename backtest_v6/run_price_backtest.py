from __future__ import annotations

import argparse, csv, math, time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, asdict
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from dateutil.relativedelta import relativedelta

UA={"User-Agent":"Mozilla/5.0"}
START=pd.Timestamp("2023-08-01"); END=pd.Timestamp("2024-07-31"); COST=0.002

@dataclass
class Trade:
    Date:str; Code:int; Side:str; Price:float; Units:float; Notional:float; Cost:float; Reason:str; CoreScore:int; Overheat:int


def yahoo_chart(symbol,start="2023-01-01",end="2024-08-03"):
    p1=int(pd.Timestamp(start,tz="UTC").timestamp());p2=int(pd.Timestamp(end,tz="UTC").timestamp())
    url=f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?period1={p1}&period2={p2}&interval=1d&events=div%2Csplits&includeAdjustedClose=true"
    r=requests.get(url,headers=UA,timeout=45);r.raise_for_status();j=r.json();res=(j.get("chart",{}).get("result") or [None])[0]
    if not res: raise RuntimeError((j.get("chart",{}).get("error") or {}).get("description") or "no chart result")
    ts=res.get("timestamp") or [];q=((res.get("indicators") or {}).get("quote") or [{}])[0];adj=(((res.get("indicators") or {}).get("adjclose") or [{}])[0].get("adjclose") or [None]*len(ts))
    rows=[]
    for i,t in enumerate(ts):
        dt=pd.to_datetime(t,unit="s",utc=True).tz_convert("Asia/Tokyo").normalize().tz_localize(None)
        close=(q.get("close") or [None]*len(ts))[i];op=(q.get("open") or [None]*len(ts))[i];vol=(q.get("volume") or [None]*len(ts))[i];ac=adj[i]
        if close is None or op is None or ac is None:continue
        fac=ac/close if close else 1.0
        rows.append((dt,float(op),float(close),float(ac),float(op*fac),float(vol or 0)))
    if not rows:raise RuntimeError("empty price rows")
    df=pd.DataFrame(rows,columns=["Date","Open","Close","AdjClose","AdjOpen","Volume"]).drop_duplicates("Date").set_index("Date").sort_index()
    return df


def fetch_prices(codes):
    out={};errors={}
    syms={c:f"{int(c)}.T" for c in codes};syms[-1]="998405.T";syms[-2]="1306.T"
    def one(k,s):
        err=None
        for n in range(3):
            try:return k,yahoo_chart(s),None
            except Exception as e:err=repr(e);time.sleep(0.7*(n+1))
        return k,None,err
    with ThreadPoolExecutor(max_workers=8) as ex:
        fs=[ex.submit(one,k,s) for k,s in syms.items()]
        for n,f in enumerate(as_completed(fs),1):
            k,df,e=f.result()
            if e:errors[k]=e
            else:out[k]=df
            if n%25==0 or n==len(fs):print("prices",n,"of",len(fs),"errors",len(errors))
    return out,errors


def prior_row(df,dt):
    x=df[df.index<dt]
    return None if x.empty else x.iloc[-1]

def prior_pos(df,dt):
    idx=df.index[df.index<dt]
    return len(idx)-1

def ret_n(df,dt,n,col="AdjClose"):
    x=df[df.index<dt]
    if len(x)<=n:return None
    return float(x[col].iloc[-1]/x[col].iloc[-1-n]-1)

def avg_value20(df,dt):
    x=df[df.index<dt].tail(20)
    if len(x)<15:return None
    return float((x["Close"]*x["Volume"]).mean())

def next_open(df,dt):
    x=df[df.index>dt]
    if x.empty:return None,None
    d=x.index[0];return d,float(x.loc[d,"AdjOpen"])

def open_on_or_after(df,dt):
    x=df[df.index>=dt]
    if x.empty:return None,None
    d=x.index[0];return d,float(x.loc[d,"AdjOpen"])

def close_on_or_before(df,dt):
    x=df[df.index<=dt]
    if x.empty:return None,None
    d=x.index[-1];return d,float(x.loc[d,"AdjClose"])

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--fundamentals",type=Path,required=True);ap.add_argument("--universe",type=Path,required=True);ap.add_argument("--out-dir",type=Path,required=True);args=ap.parse_args();args.out_dir.mkdir(parents=True,exist_ok=True)
    f=pd.read_csv(args.fundamentals);u=pd.read_csv(args.universe);f["EventDate"]=pd.to_datetime(f["EventDate"])
    hold=f[(f.EventDate>=START)&(f.EventDate<=END)].copy();passes=hold[hold.FundamentalPass==1].copy();codes=sorted(passes.Code.astype(int).unique().tolist())
    print("fundamental_pass_events",len(passes),"candidate_codes",len(codes))
    prices,perr=fetch_prices(codes)
    if -1 not in prices:
        print("TOPIX 998405 unavailable; price gate uses 1306 proxy")
    gate_bench=prices.get(-1) or prices.get(-2);tr_bench=prices.get(-2)
    if gate_bench is None or tr_bench is None:raise SystemExit("benchmark price unavailable")
    u["Code"]=u.Code.astype(int);industry=dict(zip(u.Code,u.get("Industry202307",pd.Series([""]*len(u)))))
    # Price gate. NovelText and ownership are frozen as 0 when machine extraction is unavailable; they only affect ties.
    sig=[]
    for _,r in passes.iterrows():
        c=int(r.Code);dt=r.EventDate;df=prices.get(c)
        if df is None:continue
        r20=ret_n(df,dt,20);r42=ret_n(df,dt,42);r126=ret_n(df,dt,126);b42=ret_n(gate_bench,dt,42);b126=ret_n(gate_bench,dt,126);liq=avg_value20(df,dt)
        if None in (r42,b42,liq) or r42<=0 or r42-b42<=0 or liq<300_000_000:continue
        sig.append({**r.to_dict(),"Ret20":r20,"Ret42":r42,"Excess42":r42-b42,"Excess126":None if r126 is None or b126 is None else r126-b126,"AvgValue20":liq,"NovelText":0,"UnderreactionPrior":0,"Industry":industry.get(c,"")})
    s=pd.DataFrame(sig)
    if not s.empty:s=s.sort_values(["EventDate","CoreScore","NovelText","UnderreactionPrior","Excess42","Excess126","AvgValue20","Code"],ascending=[True,False,False,False,False,False,False,True])
    s.to_csv(args.out_dir/"V6_Signals.csv",index=False,encoding="utf-8-sig")
    print("price_pass_events",len(s))
    # Daily event-driven simulation using total-return-adjusted prices. 1306 adjusted price is the total-return benchmark proxy.
    dates=tr_bench[(tr_bench.index>=pd.Timestamp("2023-07-31"))&(tr_bench.index<=END)].index
    if len(dates)<2:raise SystemExit("benchmark date range empty")
    cash=100.0;pos={};trades=[];latest_score=defaultdict(int);extend_checked=set();turnover=0.0;realized=defaultdict(float);lots_cost=defaultdict(float)
    pending_add=[]
    signals_by_date={d:g for d,g in s.groupby("EventDate")} if not s.empty else {}
    events_by_date={d:g for d,g in hold.groupby("EventDate")}
    nav_rows=[]
    def price_open(c,d):
        df=prices[c];x=df[df.index>=d]
        return (None,None) if x.empty else (x.index[0],float(x.iloc[0].AdjOpen))
    def price_close(c,d):
        df=prices[c];x=df[df.index<=d]
        return None if x.empty else float(x.iloc[-1].AdjClose)
    def nav_at(d,open_mode=False):
        total=cash
        for c,p in pos.items():
            px=None
            if open_mode:
                x=prices[c][prices[c].index>=d]
                if not x.empty and x.index[0]==d:px=float(x.iloc[0].AdjOpen)
            if px is None:px=price_close(c,d)
            if px is not None:total+=p["units"]*px
        return total
    def sell(c,d,reason,score):
        nonlocal cash,turnover
        if c not in pos:return
        od,px=price_open(c,d)
        if od!=d or px is None:return
        p=pos.pop(c);gross=p["units"]*px;cost=gross*COST;cash+=gross-cost;turnover+=gross
        pnl=(gross-cost)-p["book"];realized[c]+=pnl
        trades.append(Trade(str(d.date()),c,"SELL",px,p["units"],gross,cost,reason,int(score),0))
    def buy(c,d,weight,reason,score,overheat):
        nonlocal cash,turnover
        if c in pos:return False
        od,px=price_open(c,d)
        if od!=d or px is None:return False
        nav=nav_at(d,True);target=nav*weight;gross=min(target,cash/(1+COST))
        if gross<=0:return False
        cost=gross*COST;units=gross/px;cash-=gross+cost;turnover+=gross;lots_cost[c]+=gross+cost
        pos[c]={"units":units,"book":gross+cost,"entry":d,"score":int(score),"extended":False,"industry":industry.get(c,"")}
        trades.append(Trade(str(d.date()),c,"BUY",px,units,gross,cost,reason,int(score),int(overheat)))
        return True
    for d in dates[1:]:
        # Apply primary negative information from prior calendar day(s) at next tradable open.
        evdates=[ed for ed in events_by_date if ed<d and (d-pd.Timestamp(ed)).days<=5]
        for ed in sorted(evdates):
            g=events_by_date[ed]
            for _,r in g.iterrows():
                c=int(r.Code);latest_score[c]=int(r.CoreScore)
                if c in pos and (int(r.BlockMinus5)==1 or int(r.CoreScore)<=-3):sell(c,d,"EARLY_THESIS_BREAK",r.CoreScore)
        # Six/12 month checks at the first portfolio trading open at/after calendar threshold.
        for c in list(pos):
            p=pos.get(c)
            if not p:continue
            age6=p["entry"]+relativedelta(months=6);age12=p["entry"]+relativedelta(months=12)
            if not p["extended"] and d>=age6 and c not in extend_checked:
                ex=ret_n(prices[c],d,42);be=ret_n(gate_bench,d,42);ok=(latest_score[c]>=4 and ex is not None and be is not None and ex-be>0)
                extend_checked.add(c)
                if ok:p["extended"]=True
                else:sell(c,d,"SIX_MONTH_EXIT",latest_score[c]);continue
            if c in pos and pos[c]["extended"] and d>=age12:sell(c,d,"TWELVE_MONTH_EXIT",latest_score[c])
        # Half-size confirmation adds.
        for item in list(pending_add):
            c,add_date,score=item
            if d<add_date:continue
            pending_add.remove(item)
            if c not in pos:continue
            df=prices[c];x=df[df.index<d]
            if len(x)<6:continue
            r5=float(x.AdjClose.iloc[-1]/x.AdjClose.iloc[-6]-1);ex=ret_n(df,d,42);be=ret_n(gate_bench,d,42)
            if r5>=0 and ex is not None and be is not None and ex-be>0:
                od,px=price_open(c,d)
                if od==d:
                    nav=nav_at(d,True);gross=min(nav*0.0625,cash/(1+COST));cost=gross*COST;units=gross/px if gross>0 else 0
                    if units>0:
                        cash-=gross+cost;turnover+=gross;pos[c]["units"]+=units;pos[c]["book"]+=gross+cost;lots_cost[c]+=gross+cost;trades.append(Trade(str(d.date()),c,"ADD",px,units,gross,cost,"OVERHEAT_CONFIRM",score,1))
        # New disclosures from prior date -> next trading day. Determine whether d is each stock's first tradable day after event.
        eligible=[]
        for ed,g in signals_by_date.items():
            if ed>=d:continue
            for _,r in g.iterrows():
                c=int(r.Code);df=prices.get(c)
                if df is None or c in pos:continue
                nd,_=next_open(df,ed)
                if nd==d:eligible.append(r)
        if eligible:
            eg=pd.DataFrame(eligible).sort_values(["CoreScore","NovelText","UnderreactionPrior","Excess42","Excess126","AvgValue20","Code"],ascending=[False,False,False,False,False,False,True])
            for _,r in eg.iterrows():
                c=int(r.Code);score=int(r.CoreScore);latest_score[c]=score;ind=industry.get(c,"")
                if sum(1 for p in pos.values() if p.get("industry")==ind)>=3 and ind:continue
                if len(pos)>=8:
                    weak=min(pos,key=lambda k:(latest_score[k],k));wp=pos[weak];wex=ret_n(prices[weak],d,42);bex=ret_n(gate_bench,d,42);age=(d-wp["entry"]).days
                    if not (score>=latest_score[weak]+2 and age>=20 and wex is not None and bex is not None and wex-bex<=0):continue
                    sell(weak,d,"REPLACED_BY_STRONGER",latest_score[weak])
                over=bool((r.Ret20 is not None and not pd.isna(r.Ret20) and r.Ret20>0.25) or r.Excess42>0.30);w=0.0625 if over else 0.125
                if buy(c,d,w,"NEW_SIGNAL",score,over) and over:
                    idx=dates.get_indexer([d])[0];ai=min(idx+5,len(dates)-1);pending_add.append((c,dates[ai],score))
        nav=nav_at(d);invested=nav-cash;nav_rows.append((d,nav,cash,invested/nav if nav else 0,len(pos)))
    # End mark-to-market, per-code unrealized contribution.
    end_nav=nav_at(END)
    for c,p in pos.items():
        px=price_close(c,END);realized[c]+=p["units"]*px-p["book"] if px is not None else 0
    navdf=pd.DataFrame(nav_rows,columns=["Date","NAV","Cash","InvestedPct","Holdings"]).set_index("Date")
    b=tr_bench[tr_bench.index>=dates[0]].copy();base=float(b.AdjClose.iloc[0]);bnav=100*b.AdjClose/base;bnav=bnav.reindex(navdf.index).ffill();navdf["Benchmark1306TRProxy"]=bnav
    navdf.to_csv(args.out_dir/"V6_NAV.csv",encoding="utf-8-sig")
    tdf=pd.DataFrame([asdict(t) for t in trades]);tdf.to_csv(args.out_dir/"V6_Trades.csv",index=False,encoding="utf-8-sig")
    r=navdf.NAV.pct_change().dropna();br=navdf.Benchmark1306TRProxy.pct_change().reindex(r.index).fillna(0);cum=end_nav/100-1;bcum=float(navdf.Benchmark1306TRProxy.iloc[-1]/100-1);dd=float((navdf.NAV/navdf.NAV.cummax()-1).min());bdd=float((navdf.Benchmark1306TRProxy/navdf.Benchmark1306TRProxy.cummax()-1).min());vol=float(r.std()*math.sqrt(252));bvol=float(br.std()*math.sqrt(252));active=r-br;ir=float(active.mean()/active.std()*math.sqrt(252)) if active.std()>0 else np.nan
    m=navdf[["NAV","Benchmark1306TRProxy"]].resample("ME").last().pct_change().dropna();mwin=float((m.NAV>m.Benchmark1306TRProxy).mean()) if len(m) else np.nan
    posp=[x for x in realized.values() if x>0];top3=sum(sorted(posp,reverse=True)[:3])/sum(posp) if posp else np.nan
    metrics=[("StrategyNetReturn",cum),("Benchmark1306TRProxy",bcum),("ExcessReturn",cum-bcum),("MaxDD",dd),("BenchmarkMaxDD",bdd),("AnnualVol",vol),("BenchmarkAnnualVol",bvol),("InformationRatio",ir),("MonthlyExcessWinRate",mwin),("AverageInvestedPct",float(navdf.InvestedPct.mean())),("TurnoverVsInitialNAV",turnover/100),("TradeRows",len(tdf)),("PricePassEvents",len(s)),("FundamentalPassEvents",len(passes)),("PriceCoverageErrors",len(perr)),("Top3PositiveProfitContribution",top3),("FinalNAV",end_nav)]
    pd.DataFrame(metrics,columns=["Metric","Value"]).to_csv(args.out_dir/"V6_Results.csv",index=False,encoding="utf-8-sig")
    pd.DataFrame([{"Code":k,"Error":v} for k,v in perr.items()]).to_csv(args.out_dir/"V6_PriceErrors.csv",index=False,encoding="utf-8-sig")
    pd.DataFrame([{"Code":c,"PnL":p} for c,p in sorted(realized.items(),key=lambda z:z[1],reverse=True)]).to_csv(args.out_dir/"V6_Contribution.csv",index=False,encoding="utf-8-sig")
    print("RESULT strategy",cum,"benchmark",bcum,"excess",cum-bcum,"maxdd",dd,"IR",ir,"trades",len(tdf),"price_errors",len(perr))
if __name__=="__main__":main()
