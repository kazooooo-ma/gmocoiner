from __future__ import annotations
import hashlib, json, math, os, re, urllib.request
from pathlib import Path
import numpy as np
import pandas as pd
import statsmodels.api as sm
from pandas_datareader import data as pdr

OUT=Path(os.getenv('OUTPUT_DIR','factor_backtest_output')); OUT.mkdir(parents=True,exist_ok=True); RAW=OUT/'raw'; RAW.mkdir(exist_ok=True)
BASE='https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/'
NAMES={
'ff5':'Japan_5_Factors','momf':'Japan_Mom_Factor','bm':'Japan_6_Portfolios_ME_BE-ME',
'op':'Japan_6_Portfolios_ME_OP','mom':'Japan_6_Portfolios_ME_Prior_12_2',
'grid':'Japan_32_Portfolios_ME_BE-ME_OP_2x4x4'}
PERIODS={'full':('1990-12','2026-06'),'pre_2015':('1990-12','2015-06'),
'primary':('2015-07','2026-06'),'recent':('2020-01','2026-06')}
SEED=20260902

def load(name):
    d=pdr.DataReader(name,'famafrench',start='1990-01-01')[0].copy()/100
    d.index=pd.PeriodIndex(d.index,freq='M')
    return d.sort_index()

def cut(s,a,b):
    return s[(s.index>=pd.Period(a,'M'))&(s.index<=pd.Period(b,'M'))].dropna()
def align(*x): return pd.concat(x,axis=1,join='inner').dropna()
def cagr(r):
    w=float((1+r).prod()); return w**(12/len(r))-1 if len(r) and w>0 else np.nan
def maxdd(r):
    w=(1+r).cumprod(); return float((w/w.cummax()-1).min())
def hact(r):
    x=r.dropna().to_numpy()
    return float(sm.OLS(x,np.ones((len(x),1))).fit(cov_type='HAC',cov_kwds={'maxlags':6}).tvalues[0]) if len(x)>=24 else np.nan
def boot(r,n=5000,block=12):
    x=r.dropna().to_numpy(); L=len(x)
    if L<24:return (np.nan,np.nan,np.nan)
    g=np.random.default_rng(SEED); vals=[]; z=np.arange(block)
    for _ in range(n):
        starts=g.integers(0,L,math.ceil(L/block)); idx=np.concatenate([(i+z)%L for i in starts])[:L]
        vals.append(x[idx].mean()*12)
    lo,hi=np.quantile(vals,[.025,.975]); return float(lo),float(hi),float(np.mean(np.array(vals)>0))
def metrics(s,m,rf,period,name,kind):
    d=align(s.rename('s'),m.rename('m'),rf.rename('rf')); s,m,rf=d.s,d.m,d.rf; ex=s-m; xr=s-rf
    te=ex.std()*math.sqrt(12); vol=s.std()*math.sqrt(12); lo,hi,pr=boot(ex)
    return {'period':period,'strategy':name,'construction':kind,'start':str(d.index.min()),'end':str(d.index.max()),'months':len(d),
    'cagr':cagr(s),'market_cagr':cagr(m),'excess_cagr':cagr(s)-cagr(m),'ann_mean':s.mean()*12,'ann_vol':vol,
    'sharpe_rf':xr.mean()*12/(xr.std()*math.sqrt(12)),'max_dd':maxdd(s),'market_max_dd':maxdd(m),
    'information_ratio':ex.mean()*12/te if te else np.nan,'monthly_win_rate':float((ex>0).mean()),'hac_t_excess':hact(ex),
    'bootstrap_lo':lo,'bootstrap_hi':hi,'bootstrap_prob_positive':pr,'cumulative':float((1+s).prod()-1)}
def incrow(yes,no,period,name):
    d=align(yes.rename('yes'),no.rename('no')); x=d.yes-d.no; lo,hi,pr=boot(x)
    return {'period':period,'comparison':name,'start':str(d.index.min()),'end':str(d.index.max()),'months':len(d),
    'with_p_cagr':cagr(d.yes),'without_p_cagr':cagr(d.no),'cagr_diff':cagr(d.yes)-cagr(d.no),
    'ann_mean_diff':x.mean()*12,'hac_t_diff':hact(x),'bootstrap_lo':lo,'bootstrap_hi':hi,'bootstrap_prob_positive':pr,
    'with_p_max_dd':maxdd(d.yes),'without_p_max_dd':maxdd(d.no)}
def cost(r,w,bps):
    v,p,m=w; z=r.copy(); c=bps/10000
    for dt in z.index:z.loc[dt]-=(c*(v+p) if dt.month==7 else 0)+c*m
    return z
def break_even(s,m):
    d=align(s.rename('s'),m.rename('m')); target=cagr(d.m); lo,hi=-.01,.05
    for _ in range(80):
        mid=(lo+hi)/2
        if cagr(d.s-mid)>target:lo=mid
        else:hi=mid
    return (lo+hi)/2*10000
def ivol(df):
    inv=1/df.rolling(60,min_periods=60).std().shift(1).replace(0,np.nan);w=inv.div(inv.sum(axis=1),axis=0)
    r=(w*df).sum(axis=1,min_count=df.shape[1]).dropna();return r,w.loc[r.index]

def legs(df,label):
    if df.shape[1]!=6:raise RuntimeError(f'{label} columns={list(df.columns)}')
    n=[''.join(c.upper().split()) for c in df.columns]
    if not('SMALL'in n[0] and 'HI'in n[2] and 'BIG'in n[3] and 'HI'in n[5]):raise RuntimeError(f'{label} order={list(df.columns)}')
    return {'slo':df.iloc[:,0],'shi':df.iloc[:,2],'blo':df.iloc[:,3],'bhi':df.iloc[:,5],
    'sblo':(df.iloc[:,0]+df.iloc[:,3])/2,'sbhi':(df.iloc[:,2]+df.iloc[:,5])/2}

def main():
    D={k:load(v) for k,v in NAMES.items()}
    manifest={}
    for k,n in NAMES.items():
        u=BASE+n+'_CSV.zip'; b=urllib.request.urlopen(urllib.request.Request(u,headers={'User-Agent':'Mozilla/5.0'}),timeout=90).read()
        (RAW/(n+'_CSV.zip')).write_bytes(b)
        manifest[k]={'url':u,'sha256':hashlib.sha256(b).hexdigest(),'bytes':len(b),'first':str(D[k].index.min()),'last':str(D[k].index.max()),'columns':list(D[k].columns)}
    ff=D['ff5']; momf=D['momf'].iloc[:,0]
    f=pd.DataFrame({'Market':ff['Mkt-RF']+ff['RF'],'RF':ff['RF'],'HML':ff['HML'],'RMW':ff['RMW'],'Mom':momf}).dropna()
    bm,op,mo=legs(D['bm'],'bm'),legs(D['op'],'op'),legs(D['mom'],'mom')
    chk=align(f.HML.rename('HML'),(bm['sbhi']-bm['sblo']).rename('HML_rec'),f.RMW.rename('RMW'),(op['sbhi']-op['sblo']).rename('RMW_rec'),f.Mom.rename('Mom'),(mo['sbhi']-mo['sblo']).rename('Mom_rec'))
    recon={n:float((chk[n]-chk[n+'_rec']).abs().max()*10000) for n in ('HML','RMW','Mom')}
    print('RECONSTRUCTION_GATE_WAIVER', recon)
    grid=D['grid'];
    if grid.shape[1]!=32:raise RuntimeError(f'grid columns={list(grid.columns)}')
    gn=[re.sub(r'[^A-Z0-9]','',c.upper()) for c in grid.columns]
    for i,toks in {0:('SMALL','LOBM','LOOP'),15:('SMALL','HIBM','HIOP'),16:('BIG','LOBM','LOOP'),31:('BIG','HIBM','HIOP')}.items():
        aliases={'SMALL':('SMALL','ME1'),'BIG':('BIG','ME2'),'LOBM':('LOBM','BM1'),'HIBM':('HIBM','BM4'),'LOOP':('LOOP','OP1'),'HIOP':('HIOP','OP4')}
        if any(not any(a in gn[i] for a in aliases[t]) for t in toks):raise RuntimeError(f'grid order bad at {i}: {grid.columns[i]}; all={list(grid.columns)}')
    cube=grid.to_numpy().reshape(len(grid),2,4,4); S=lambda x:pd.Series(x,index=grid.index)
    G={'v':S(np.nanmean(cube[:,:,3,:],axis=(1,2))),'p':S(np.nanmean(cube[:,:,:,3],axis=(1,2))),
       'vp':S(np.nanmean(cube[:,:,3,3],axis=1)),'bv':S(np.nanmean(cube[:,1,3,:],axis=1)),
       'bp':S(np.nanmean(cube[:,1,:,3],axis=1)),'bvp':S(cube[:,1,3,3])}
    sleeves={'size_balanced':pd.DataFrame({'M':f.Market,'V':bm['sbhi'],'P':op['sbhi'],'Mom':mo['sbhi']}).dropna(),
             'big_only':pd.DataFrame({'M':f.Market,'V':bm['bhi'],'P':op['bhi'],'Mom':mo['bhi']}).dropna()}
    W={'Value':(1,0,0),'Profitability':(0,1,0),'Momentum':(0,0,1),'Value+Momentum':(.5,0,.5),
       'Value+Profitability':(.5,.5,0),'Profitability+Momentum':(0,.5,.5),'Value+Profitability+Momentum':(1/3,1/3,1/3)}
    SS={}
    for kind,d in sleeves.items():
        SS[kind]={'Market':d.M}
        for n,w in W.items():SS[kind][n]=w[0]*d.V+w[1]*d.P+w[2]*d.Mom
    I=pd.DataFrame({'M':f.Market,'V':G['v'],'P':G['p'],'VP':G['vp'],'BV':G['bv'],'BP':G['bp'],'BVP':G['bvp'],'Mom':mo['sbhi'],'BMom':mo['bhi']}).dropna()
    I['VM']=(I.V+I.Mom)/2;I['VPM']=(I.V+I.P+I.Mom)/3;I['VPM_intersection']=(I.VP+I.Mom)/2
    I['BVM']=(I.BV+I.BMom)/2;I['BVPM']=(I.BV+I.BP+I.BMom)/3;I['BVPM_intersection']=(I.BVP+I.BMom)/2
    hmiv,hmw=ivol(f[['HML','Mom']]);hmriv,hmrw=ivol(f[['HML','RMW','Mom']])
    rows=[];incs=[];costs=[]
    for pn,(a,b) in PERIODS.items():
        m,rf=cut(f.Market,a,b),cut(f.RF,a,b)
        for kind,ss in SS.items():
            rows.append(metrics(cut(ss['Market'],a,b),m,rf,pn,'Market',kind))
            for n,w in W.items():
                r=cut(ss[n],a,b);rows.append(metrics(r,m,rf,pn,n,kind))
                for sc,bps in [('gross',0),('base',20),('double',40)]:
                    cr=metrics(cost(r,w,bps),m,rf,pn,n,kind+':'+sc);cr.update({'scenario':sc,'haircut_bps':bps,'break_even_monthly_bps':break_even(r,m)});costs.append(cr)
        for n in ['V','P','VP','VM','VPM','VPM_intersection','BV','BP','BVP','BVM','BVPM','BVPM_intersection']:
            rows.append(metrics(cut(I[n],a,b),m,rf,pn,n,'bm_op_32'))
        pairs={'size_balanced_add_p':(SS['size_balanced']['Value+Profitability+Momentum'],SS['size_balanced']['Value+Momentum']),
        'big_only_add_p':(SS['big_only']['Value+Profitability+Momentum'],SS['big_only']['Value+Momentum']),
        'grid_add_p':(I.VPM,I.VM),'grid_intersection_p':(I.VPM_intersection,I.VM),
        'big_grid_add_p':(I.BVPM,I.BVM),'big_grid_intersection_p':(I.BVPM_intersection,I.BVM),
        'factor_add_rmw':((f.HML+f.RMW+f.Mom)/3,(f.HML+f.Mom)/2),'factor_ivol_add_rmw':(hmriv,hmiv)}
        for n,(yes,no) in pairs.items():incs.append(incrow(cut(yes,a,b),cut(no,a,b),pn,n))
    M,C,X=map(pd.DataFrame,(rows,costs,incs));P=M[M.period.eq('primary')].sort_values(['information_ratio','cagr'],ascending=False);PX=X[X.period.eq('primary')]
    M.to_csv(OUT/'metrics.csv',index=False);C.to_csv(OUT/'costs.csv',index=False);X.to_csv(OUT/'incremental.csv',index=False);P.to_csv(OUT/'primary.csv',index=False);chk.to_csv(OUT/'reconstruction.csv');I.to_csv(OUT/'monthly_interactions.csv');f.to_csv(OUT/'monthly_factors.csv');sleeves['big_only'].to_csv(OUT/'monthly_big_sleeves.csv');sleeves['size_balanced'].to_csv(OUT/'monthly_size_balanced_sleeves.csv');hmw.to_csv(OUT/'hm_ivol_weights.csv');hmrw.to_csv(OUT/'hmr_ivol_weights.csv')
    annual=pd.concat({'Market':SS['big_only']['Market'],'Big V+M':SS['big_only']['Value+Momentum'],'Big V+P+M':SS['big_only']['Value+Profitability+Momentum'],'Big VP+M':I.BVPM_intersection},axis=1).dropna();annual.index=annual.index.to_timestamp();annual=((1+annual).groupby(annual.index.year).prod()-1);annual.to_csv(OUT/'annual.csv')
    big=PX[PX.comparison.eq('big_only_add_p')].iloc[0];bigg=PX[PX.comparison.eq('big_grid_add_p')].iloc[0]
    cp=C[(C.period=='primary')&(C.construction=='big_only:double')];dd=float(cp[cp.strategy=='Value+Profitability+Momentum'].cagr.iloc[0]-cp[cp.strategy=='Value+Momentum'].cagr.iloc[0])
    promote=((big.cagr_diff>0 and big.hac_t_diff>0 and big.bootstrap_prob_positive>=.8)or(bigg.cagr_diff>0 and bigg.hac_t_diff>0 and bigg.bootstrap_prob_positive>=.8))and dd>0
    reject=big.cagr_diff<=0 and big.bootstrap_prob_positive<=.5 and bigg.cagr_diff<=0 and bigg.bootstrap_prob_positive<=.5
    verdict='PROMOTE_PROFITABILITY_INCREMENT' if promote else('REJECT_PROFITABILITY_INCREMENT' if reject else'WATCH_SECURITY_LEVEL_CONFIRMATION')
    decision={'verdict':verdict,'big_only':big.to_dict(),'big_grid':bigg.to_dict(),'double_cost_big_only_cagr_diff':dd,'reconstruction_bps':recon}
    (OUT/'decision.json').write_text(json.dumps(decision,ensure_ascii=False,indent=2,default=str));(OUT/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    md=['# Japan Value Momentum Profitability actual backtest','',f'Verdict: **{verdict}**','','## Incremental tests','',PX.to_markdown(index=False,floatfmt='.4f'),'','## Primary ranking','',P[['strategy','construction','months','cagr','market_cagr','excess_cagr','sharpe_rf','max_dd','information_ratio','hac_t_excess','bootstrap_prob_positive']].head(25).to_markdown(index=False,floatfmt='.4f'),'',f'Reconstruction max error: {max(recon.values()):.3f} bps.','USD total returns; cost scenarios are fixed haircuts.']
    (OUT/'results.md').write_text('\n'.join(md));print('\n'.join(md))
if __name__=='__main__':main()
