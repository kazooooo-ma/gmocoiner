from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup

JPX_SEARCH = 'https://www2.jpx.co.jp/tseHpFront/JJK010010Action.do?Show=Show'
JPX_DETAIL = 'https://www2.jpx.co.jp/tseHpFront/JJK010030Action.do'
MIN_DISC_DATE = pd.Timestamp('2022-05-01')
MAX_DISC_DATE = pd.Timestamp('2026-06-30')
WINDOWS = [
    ('WF-05', pd.Timestamp('2022-06-30'), pd.Timestamp('2023-06-30')),
    ('WF-06', pd.Timestamp('2023-06-30'), pd.Timestamp('2024-06-28')),
    ('WF-07', pd.Timestamp('2024-06-28'), pd.Timestamp('2025-06-30')),
    ('WF-08', pd.Timestamp('2025-06-30'), pd.Timestamp('2026-06-30')),
]
LOOKBACK_DAYS = 45
LIQ_THRESHOLD = 300_000_000.0
LIQ_MIN_DAYS = 15
SLOT_COUNT = 12
SECTOR_CAP = 3
ONE_WAY_COST = 0.002
HOLD_MONTHS = 6
BENCHMARK_CODE = 1306
THREADS_DETAIL = 10
THREADS_IXBRL = 14
THREADS_PRICE = 8

_thread = threading.local()


def get_session() -> requests.Session:
    s = getattr(_thread, 'session', None)
    if s is None:
        s = requests.Session()
        s.headers.update({'User-Agent': 'Mozilla/5.0 v5.2 walk-forward research; contact via GitHub repository'})
        _thread.session = s
    return s


def get_retry(url: str, *, method: str = 'GET', data: dict[str, str] | None = None, tries: int = 4, timeout: int = 40) -> requests.Response:
    last = None
    for i in range(tries):
        try:
            s = get_session()
            r = s.get(url, timeout=timeout) if method == 'GET' else s.post(url, data=data, timeout=timeout)
            if r.status_code == 200 and len(r.content) > 100:
                return r
            last = RuntimeError(f'{r.status_code} {url}')
        except Exception as e:
            last = e
        time.sleep(0.4 * (2 ** i))
    raise RuntimeError(f'fetch failed {url}: {last}')


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def num_text(text: str | None, scale: str | None = None, sign: str | None = None) -> float | None:
    if text is None:
        return None
    t = text.strip().replace(',', '').replace('，', '').replace('％', '').replace('%', '')
    t = t.replace('△', '-').replace('▲', '-').replace('－', '-').replace('−', '-')
    if t in {'', '-', '—', '―'}:
        return None
    t = re.sub(r'[^0-9eE+\-.]', '', t)
    if not t or t in {'-', '.', '-.'}:
        return None
    try:
        v = float(t)
    except ValueError:
        return None
    try:
        if scale not in (None, ''):
            v *= 10 ** int(scale)
    except Exception:
        pass
    if sign == '-' and v > 0:
        v = -v
    return v


def is_consolidated(ctx: str) -> bool:
    low = ctx.lower()
    return 'consolidatedmember' in low and 'nonconsolidatedmember' not in low


def metric_match(name: str, kind: str) -> bool:
    n = name.lower().replace(':', '')
    if 'changein' in n or 'ratio' in n:
        return False
    if kind == 'op':
        return ('operatingincome' in n or 'operatingprofit' in n) and 'cashflows' not in n
    if kind == 'ordinary':
        return 'ordinaryincome' in n or 'ordinaryprofit' in n
    if kind == 'profit_parent':
        return 'profitattributabletoownersofparent' in n
    if kind == 'netincome':
        return ('netincome' in n or n.endswith('profit')) and 'per' not in n and 'change' not in n
    if kind == 'eps':
        return 'basicearningspershare' in n or 'netincomepershare' in n or 'earningspershare' in n
    if kind == 'dividend':
        return n.endswith('dividendpershare') or 'dividendpershare' in n
    return False


def context_dates(soup: BeautifulSoup) -> dict[str, dict[str, pd.Timestamp | None]]:
    out: dict[str, dict[str, pd.Timestamp | None]] = {}
    for tag in soup.find_all(lambda x: x.name and x.name.lower().endswith('context')):
        cid = tag.get('id')
        if not cid:
            continue
        def find_date(suffix: str) -> pd.Timestamp | None:
            z = tag.find(lambda x: x.name and x.name.lower().endswith(suffix))
            if not z:
                return None
            v = pd.to_datetime(z.get_text(strip=True), errors='coerce')
            return None if pd.isna(v) else pd.Timestamp(v)
        out[cid] = {'start': find_date('startdate'), 'end': find_date('enddate'), 'instant': find_date('instant')}
    return out


def facts_from_soup(soup: BeautifulSoup) -> list[dict[str, Any]]:
    facts = []
    for tag in soup.find_all(lambda x: x.name and x.name.lower().endswith('nonfraction')):
        ctx = tag.get('contextref', '')
        name = tag.get('name', '')
        val = num_text(tag.get_text(' ', strip=True), tag.get('scale'), tag.get('sign'))
        if val is not None:
            facts.append({'name': name, 'ctx': ctx, 'value': val})
    return facts


def period_type_from_title(title: str) -> str:
    t = title.replace('１','1').replace('２','2').replace('３','3')
    if '第1四半期' in t or '第１四半期' in title:
        return '1Q'
    if '第2四半期' in t or '中間期' in title or '第２四半期' in title:
        return '2Q'
    if '第3四半期' in t or '第３四半期' in title:
        return '3Q'
    return 'FY'


def context_period_ok(ctx: str, period: str, current: bool = True) -> bool:
    low = ctx.lower()
    if current and 'current' not in low:
        return False
    if not current and 'prior' not in low:
        return False
    if 'resultmember' not in low:
        return False
    if period == '1Q':
        return 'q1' in low
    if period == '2Q':
        return 'q2' in low or 'interim' in low
    if period == '3Q':
        return 'q3' in low
    return 'currentyearduration' in low if current else 'prioryearduration' in low


def choose_metric(facts: list[dict[str, Any]], ctxs: set[str], kind: str) -> float | None:
    candidates = [f for f in facts if f['ctx'] in ctxs and metric_match(f['name'], kind)]
    if not candidates:
        return None
    candidates.sort(key=lambda f: (0 if is_consolidated(f['ctx']) else 1, len(f['name'])))
    return float(candidates[0]['value'])


def best_profit(facts: list[dict[str, Any]], ctxs: set[str]) -> tuple[float | None, str | None]:
    for kind in ['op', 'ordinary', 'profit_parent', 'netincome']:
        v = choose_metric(facts, ctxs, kind)
        if v is not None:
            return v, kind
    return None, None


def matching_profit(facts: list[dict[str, Any]], ctxs: set[str], kind: str | None) -> float | None:
    if kind is None:
        return None
    return choose_metric(facts, ctxs, kind)


def best_forecast_contexts(facts: list[dict[str, Any]], cdates: dict[str, dict[str, pd.Timestamp | None]], current_end: pd.Timestamp | None) -> set[str]:
    ctxs = {f['ctx'] for f in facts if 'forecastmember' in f['ctx'].lower()}
    rows = []
    for ctx in ctxs:
        end = cdates.get(ctx, {}).get('end')
        if end is None:
            continue
        if current_end is not None and end <= current_end:
            continue
        rows.append((0 if is_consolidated(ctx) else 1, end, ctx))
    if not rows:
        return set()
    min_end = min(x[1] for x in rows)
    preferred = [x for x in rows if x[1] == min_end]
    best_cons = min(x[0] for x in preferred)
    return {x[2] for x in preferred if x[0] == best_cons}


def extract_dividend(facts: list[dict[str, Any]], cdates: dict[str, dict[str, pd.Timestamp | None]], forecast_end: pd.Timestamp | None, forecast: bool) -> float | None:
    candidates = []
    for f in facts:
        low = f['ctx'].lower()
        if not metric_match(f['name'], 'dividend') or 'annualmember' not in low:
            continue
        if forecast and 'forecastmember' not in low:
            continue
        if (not forecast) and 'resultmember' not in low:
            continue
        end = cdates.get(f['ctx'], {}).get('end')
        if end is None:
            continue
        candidates.append((end, f['value'], f['ctx']))
    if not candidates:
        return None
    if forecast_end is not None:
        exact = [x for x in candidates if x[0] == forecast_end]
        if exact:
            return float(exact[0][1])
        if forecast:
            fut = [x for x in candidates if x[0] >= forecast_end]
            if fut:
                return float(sorted(fut)[0][1])
        else:
            prev = [x for x in candidates if x[0] < forecast_end]
            if prev:
                return float(sorted(prev)[-1][1])
    return float(sorted(candidates)[-1][1])


def parse_ixbrl(link: dict[str, Any]) -> dict[str, Any] | None:
    url = link['url']
    try:
        r = get_retry(url)
        r.encoding = r.apparent_encoding or 'utf-8'
        soup = BeautifulSoup(r.text, 'html.parser')
        cdates = context_dates(soup)
        facts = facts_from_soup(soup)
        period = period_type_from_title(link['title'])
        current_ctxs = {c for c in cdates if context_period_ok(c, period, True)}
        prior_ctxs = {c for c in cdates if context_period_ok(c, period, False)}
        if not current_ctxs:
            return None
        current_end_vals = [cdates[c].get('end') for c in current_ctxs if cdates[c].get('end') is not None]
        current_end = max(current_end_vals) if current_end_vals else None
        current_profit, p_kind = best_profit(facts, current_ctxs)
        prior_profit = matching_profit(facts, prior_ctxs, p_kind)
        yoy = None
        if current_profit is not None and prior_profit is not None:
            if prior_profit > 0:
                yoy = current_profit / prior_profit - 1.0
            elif prior_profit <= 0 < current_profit:
                yoy = 1.0
            elif current_profit <= 0 < prior_profit:
                yoy = -1.0
        fctxs = best_forecast_contexts(facts, cdates, current_end)
        fends = [cdates[c].get('end') for c in fctxs if cdates[c].get('end') is not None]
        fy_end = min(fends) if fends else None
        fstarts = [cdates[c].get('start') for c in fctxs if cdates[c].get('start') is not None]
        fy_start = min(fstarts) if fstarts else None
        forecast_op = choose_metric(facts, fctxs, 'op')
        forecast_ord = choose_metric(facts, fctxs, 'ordinary')
        forecast_profit = choose_metric(facts, fctxs, 'profit_parent')
        if forecast_profit is None:
            forecast_profit = choose_metric(facts, fctxs, 'netincome')
        forecast_eps = choose_metric(facts, fctxs, 'eps')
        forecast_div = extract_dividend(facts, cdates, fy_end, True)
        prior_actual_div = extract_dividend(facts, cdates, fy_end, False)
        return {
            'Code': int(link['code']), 'DisclosureDate': pd.Timestamp(link['date']), 'Title': link['title'], 'URL': url,
            'PeriodType': period, 'CurrentPeriodEnd': current_end, 'FiscalYearStart': fy_start, 'FiscalYearEnd': fy_end,
            'CurrentProfit': current_profit, 'PriorComparableProfit': prior_profit, 'ProfitYoY': yoy,
            'ForecastOperatingProfit': forecast_op, 'ForecastOrdinaryProfit': forecast_ord,
            'ForecastProfit': forecast_profit, 'ForecastEPS': forecast_eps,
            'ForecastDividendAnnual': forecast_div, 'PriorActualDividendAnnual': prior_actual_div,
        }
    except Exception as e:
        return {'Code': int(link['code']), 'DisclosureDate': pd.Timestamp(link['date']), 'Title': link['title'], 'URL': url, 'ParseError': repr(e)}


def parse_link_date(href: str) -> pd.Timestamp | None:
    m = re.search(r'-(20\d{6})\d{5,7}-ixbrl', href)
    if not m:
        return None
    d = pd.to_datetime(m.group(1), format='%Y%m%d', errors='coerce')
    return None if pd.isna(d) else pd.Timestamp(d)


def direct_detail(code: int, hist_flag: str) -> BeautifulSoup | None:
    s = get_session()
    try:
        s.get(JPX_SEARCH, timeout=25)
        data = {'BaseJh':'BaseJh','mgrCd':f'{code}0','jjHisiFlg':hist_flag,'lstDspPg':'1','dspGs':'200','souKnsu':'1','sniMtGmnId':'JJK010010','dspJnKbn':'0','dspJnKmkNo':'0'}
        r = s.post(JPX_DETAIL, data=data, timeout=35)
        if r.status_code != 200 or len(r.content) < 5000:
            return None
        r.encoding = 'utf-8'
        return BeautifulSoup(r.text, 'html.parser')
    except Exception:
        return None


def fetch_detail_links(code: int) -> tuple[int, list[dict[str, Any]], dict[str, Any]]:
    soup = direct_detail(code, '1')
    flag = '1'
    links = []
    if soup is not None:
        links = [a for a in soup.find_all('a', href=True) if 'ixbrl' in a['href'].lower()]
    if not links:
        soup = direct_detail(code, '2')
        flag = '2'
        links = [] if soup is None else [a for a in soup.find_all('a', href=True) if 'ixbrl' in a['href'].lower()]
    out = []
    if soup is not None:
        for a in links:
            href = urljoin('https://www2.jpx.co.jp', a['href'])
            dt = parse_link_date(href)
            if dt is None or dt < MIN_DISC_DATE or dt > MAX_DISC_DATE:
                continue
            tr = a.find_parent('tr')
            title = tr.get_text(' ', strip=True) if tr else ''
            title = re.sub(r'^\d{4}/\d{2}/\d{2}\s*', '', title).strip()
            out.append({'code': code, 'date': dt, 'title': title, 'url': href})
    dedup = {x['url']: x for x in out}
    return code, list(dedup.values()), {'Code': code, 'HistoryFlag': flag, 'DetailOK': soup is not None, 'IXBRLLinks': len(dedup)}


def prepare_event_revisions(events: pd.DataFrame) -> pd.DataFrame:
    if events.empty:
        return events
    events = events.sort_values(['Code','DisclosureDate','CurrentPeriodEnd','URL']).copy()
    events['UpRevision'] = False; events['DownRevision'] = False
    events['UpRevisionChange'] = np.nan; events['DownRevisionChange'] = np.nan
    events['DividendGrowth'] = False; events['DividendChange'] = np.nan
    metric_cols = ['ForecastOperatingProfit','ForecastOrdinaryProfit','ForecastProfit','ForecastEPS']
    previous: dict[tuple[int, str, str], float] = {}
    previous_div: dict[tuple[int, str], float] = {}
    for idx, row in events.iterrows():
        code = int(row['Code']); fy = row.get('FiscalYearEnd')
        fy_key = '' if pd.isna(fy) else str(pd.Timestamp(fy).date())
        ups=[]; downs=[]
        if fy_key:
            for col in metric_cols:
                cur = row.get(col)
                if pd.notna(cur):
                    key=(code,fy_key,col); prev=previous.get(key)
                    if prev is not None and abs(prev)>1e-12:
                        ch=(float(cur)-prev)/abs(prev); ups.append(ch); downs.append(ch)
                    previous[key]=float(cur)
            div=row.get('ForecastDividendAnnual')
            if pd.notna(div):
                key=(code,fy_key); prevd=previous_div.get(key)
                candidates=[]
                if prevd is not None and abs(prevd)>1e-12:
                    candidates.append((float(div)-prevd)/abs(prevd))
                pa=row.get('PriorActualDividendAnnual')
                if pd.notna(pa) and abs(float(pa))>1e-12:
                    candidates.append((float(div)-float(pa))/abs(float(pa)))
                if candidates:
                    best=max(candidates); events.at[idx,'DividendChange']=best; events.at[idx,'DividendGrowth']=best>=0.10
                previous_div[key]=float(div)
        if ups:
            mx=max(ups); mn=min(downs)
            events.at[idx,'UpRevisionChange']=mx; events.at[idx,'DownRevisionChange']=mn
            events.at[idx,'UpRevision']=mx>=0.05; events.at[idx,'DownRevision']=mn<=-0.05
    return events


def xtks_checkpoints(base: pd.Timestamp, end: pd.Timestamp) -> list[pd.Timestamp]:
    import exchange_calendars as xcals
    cal=xcals.get_calendar('XTKS')
    sessions=cal.sessions_in_range(base - pd.Timedelta(days=5), end)
    sessions=pd.DatetimeIndex(sessions).tz_localize(None)
    periods=pd.period_range(base.to_period('M'), (end-pd.offsets.MonthEnd(1)).to_period('M'), freq='M')
    out=[]
    for p in periods:
        xs=sessions[sessions.to_period('M')==p]
        if len(xs): out.append(pd.Timestamp(xs.max()))
    return out


def evaluate(events: pd.DataFrame, cohort: pd.DataFrame, checkpoints: list[pd.Timestamp]) -> tuple[pd.DataFrame, dict[pd.Timestamp, dict[int,dict[str,Any]]]]:
    rows=[]; maps={}; cohort_codes=set(cohort['Code'].astype(int))
    for cp in checkpoints:
        start=cp-pd.Timedelta(days=LOOKBACK_DAYS-1)
        # Conservative: public JPX issuer page has date but not exact time, so same-day records are excluded.
        win=events[(events['DisclosureDate']>=start)&(events['DisclosureDate']<cp)&events['Code'].isin(cohort_codes)]
        hist=events[(events['DisclosureDate']<cp)&events['Code'].isin(cohort_codes)]
        cmap={}
        for code,g in win.groupby('Code'):
            code=int(code); g=g.sort_values('DisclosureDate'); h=hist[hist['Code']==code].sort_values('DisclosureDate')
            up=bool(g['UpRevision'].fillna(False).any()); down=bool(g['DownRevision'].fillna(False).any())
            stm=g[g['ProfitYoY'].notna()]
            yoy=float(stm.iloc[-1]['ProfitYoY']) if not stm.empty else None
            pg=yoy is not None and yoy>=0.15; pdn=yoy is not None and yoy<=-0.20
            div=bool(g['DividendGrowth'].fillna(False).any())
            hp=h[h['CurrentProfit'].notna()]
            latest_profit=float(hp.iloc[-1]['CurrentProfit']) if not hp.empty else None
            black=latest_profit is not None and latest_profit>0
            score=(3 if up else 0)+(2 if pg and not down else 0)+(1 if div else 0)-(4 if down else 0)-(2 if pdn else 0)
            passed=score>=3 and black and not down
            info={'Checkpoint':cp,'Code':code,'Score':score,'UpRevision':up,'DownRevision':down,'ProfitYoY':yoy,'ProfitGrowth':pg,'ProfitDecline':pdn,'DividendGrowth':div,'LatestProfitBlack':black,'LatestProfitValue':latest_profit,'FundamentalPass':passed,'WindowStart':start,'WindowEnd':cp,'DisclosureCount':len(g)}
            rows.append(info); cmap[code]=info
        maps[cp]=cmap
    return pd.DataFrame(rows), maps


def stage_fundamentals(stock_list_path: Path, out_dir: Path) -> None:
    out_dir.mkdir(parents=True,exist_ok=True)
    sl=pd.read_csv(stock_list_path,low_memory=False)
    sl['SecuritiesCode']=pd.to_numeric(sl['SecuritiesCode'],errors='coerce').astype('Int64')
    prime=sl[(sl['NewMarketSegment'].astype(str)=='Prime Market') & sl['SecuritiesCode'].notna()].copy()
    prime=prime[~prime['Section/Products'].astype(str).str.contains('ETF|ETN|REIT|Preferred|Foreign',case=False,regex=True,na=False)]
    prime=prime.drop_duplicates('SecuritiesCode').copy()
    cohort=pd.DataFrame({'Code':prime['SecuritiesCode'].astype(int),'Name':prime['Name'].astype(str),'Sector':prime['33SectorName'].fillna('Unknown').astype(str)})
    cohort.to_csv(out_dir/'universe_fixed_20211230_prime.csv',index=False)
    print('COHORT',len(cohort),flush=True)
    details=[]; links=[]
    with ThreadPoolExecutor(max_workers=THREADS_DETAIL) as ex:
        futs={ex.submit(fetch_detail_links,int(c)):int(c) for c in cohort['Code']}
        for i,f in enumerate(as_completed(futs),1):
            code,ls,a=f.result(); links.extend(ls); details.append(a)
            if i%100==0: print('DETAIL',i,'links',len(links),flush=True)
    pd.DataFrame(details).to_csv(out_dir/'jpx_detail_audit.csv',index=False)
    pd.DataFrame(links).to_csv(out_dir/'ixbrl_links.csv',index=False)
    print('IXBRL LINKS',len(links),flush=True)
    parsed=[]
    with ThreadPoolExecutor(max_workers=THREADS_IXBRL) as ex:
        futs=[ex.submit(parse_ixbrl,x) for x in links]
        for i,f in enumerate(as_completed(futs),1):
            r=f.result()
            if r is not None: parsed.append(r)
            if i%500==0: print('IXBRL',i,'/',len(futs),flush=True)
    events=pd.DataFrame(parsed)
    if 'ParseError' in events.columns:
        events[events['ParseError'].notna()].to_csv(out_dir/'ixbrl_parse_errors.csv',index=False)
    required=['Code','DisclosureDate','Title','URL','PeriodType','CurrentPeriodEnd','FiscalYearStart','FiscalYearEnd','CurrentProfit','PriorComparableProfit','ProfitYoY','ForecastOperatingProfit','ForecastOrdinaryProfit','ForecastProfit','ForecastEPS','ForecastDividendAnnual','PriorActualDividendAnnual']
    for c in required:
        if c not in events.columns: events[c]=np.nan
    events=events[events['CurrentProfit'].notna() | events[['ForecastOperatingProfit','ForecastOrdinaryProfit','ForecastProfit','ForecastEPS']].notna().any(axis=1)].copy()
    events=prepare_event_revisions(events)
    events.to_csv(out_dir/'financial_events.csv',index=False)
    all_cps=sorted(set(cp for _,b,e in WINDOWS for cp in xtks_checkpoints(b,e)))
    fundamentals,maps=evaluate(events,cohort,all_cps)
    fundamentals.to_csv(out_dir/'fundamentals_preprice.csv',index=False)
    pass_codes=sorted(fundamentals.loc[fundamentals['FundamentalPass'].fillna(False),'Code'].astype(int).unique()) if not fundamentals.empty else []
    pd.DataFrame({'Code':pass_codes}).to_csv(out_dir/'fundamental_pass_codes.csv',index=False)
    standalone=[]
    for _,a in pd.DataFrame(links).iterrows() if links else []:
        pass
    manifest={'stage':'fundamentals_preprice','cohort_count':len(cohort),'detail_ok':int(pd.DataFrame(details)['DetailOK'].sum()) if details else 0,'ixbrl_link_count':len(links),'event_rows':len(events),'fundamental_rows':len(fundamentals),'fundamental_pass_events':int(fundamentals['FundamentalPass'].sum()) if not fundamentals.empty else 0,'pass_code_count':len(pass_codes),'sha256':{}}
    for fn in ['universe_fixed_20211230_prime.csv','jpx_detail_audit.csv','ixbrl_links.csv','financial_events.csv','fundamentals_preprice.csv','fundamental_pass_codes.csv']:
        p=out_dir/fn
        if p.exists(): manifest['sha256'][fn]=sha256_file(p)
    (out_dir/'preprice_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(manifest,ensure_ascii=False,indent=2),flush=True)


def fetch_yf(code: int, start: str, end: str) -> tuple[int,pd.DataFrame|None,str|None]:
    import yfinance as yf
    ticker=f'{code}.T'
    try:
        d=yf.download(ticker,start=start,end=end,auto_adjust=False,actions=True,progress=False,threads=False,timeout=30)
        if d.empty: return code,None,'empty'
        if isinstance(d.columns,pd.MultiIndex): d.columns=d.columns.get_level_values(0)
        d=d.reset_index(); d['Date']=pd.to_datetime(d['Date']).dt.tz_localize(None)
        for c in ['Open','High','Low','Close','Volume','Dividends','Stock Splits']:
            if c not in d.columns: d[c]=0.0
        splits=pd.to_numeric(d['Stock Splits'],errors='coerce').fillna(0.0)
        ratio=splits.where(splits>0,1.0)
        future=ratio.iloc[::-1].cumprod().iloc[::-1]/ratio
        for c in ['Open','High','Low','Close']:
            d['Adj'+c]=pd.to_numeric(d[c],errors='coerce')/future
        d['AdjExpectedDividend']=pd.to_numeric(d['Dividends'],errors='coerce').fillna(0.0)/future
        d['TradingValue']=pd.to_numeric(d['Close'],errors='coerce')*pd.to_numeric(d['Volume'],errors='coerce')
        d['SecuritiesCode']=code; d['SupervisionFlag']=False; d['Month']=d['Date'].dt.to_period('M')
        return code,d,None
    except Exception as e:
        return code,None,repr(e)


def build_signals(prices: pd.DataFrame, fundamentals: pd.DataFrame, cohort: pd.DataFrame, checkpoints: list[pd.Timestamp], benchmark_code: int=BENCHMARK_CODE) -> pd.DataFrame:
    monthly_last=prices.sort_values('Date').groupby(['SecuritiesCode','Month'],as_index=False).tail(1)
    close=monthly_last.set_index(['SecuritiesCode','Month'])['AdjClose'].to_dict()
    liq=prices.groupby(['SecuritiesCode','Month']).agg(AvgTradingValue=('TradingValue','mean'),TradingDays=('TradingValue','count')).to_dict('index')
    sector=cohort.set_index('Code')['Sector'].to_dict(); name=cohort.set_index('Code')['Name'].to_dict()
    rows=[]
    for cp in checkpoints:
        p=cp.to_period('M'); p2=p-2; lp=p-1
        b0=close.get((benchmark_code,p2)); b1=close.get((benchmark_code,p)); br=None if b0 is None or b1 is None else b1/b0-1
        cand=[]
        fp=fundamentals[(pd.to_datetime(fundamentals['Checkpoint'])==cp)&fundamentals['FundamentalPass'].fillna(False)]
        for _,f in fp.iterrows():
            c=int(f['Code']); a0=close.get((c,p2)); a1=close.get((c,p)); ar=None if a0 is None or a1 is None else a1/a0-1
            ex=None if ar is None or br is None else ar-br
            q=liq.get((c,lp),{}); tv=q.get('AvgTradingValue'); days=int(q.get('TradingDays',0) or 0)
            lpass=tv is not None and float(tv)>=LIQ_THRESHOLD and days>=LIQ_MIN_DAYS
            ppass=ar is not None and ar>0 and ex is not None and ex>0 and lpass
            cand.append({'Checkpoint':cp,'Code':c,'Name':name.get(c,str(c)),'Sector':sector.get(c,'Unknown'),'Score':int(f['Score']),'Abs2MReturn':ar,'Benchmark2MReturn':br,'Excess2MReturn':ex,'AvgTradingValuePrevMonth':tv,'TradingDaysPrevMonth':days,'LiquidityPass':lpass,'PricePass':ppass,'Selected':False,'SelectionRank':np.nan})
        ranked=sorted([r for r in cand if r['PricePass']],key=lambda r:(-r['Score'],-r['Excess2MReturn'],-r['AvgTradingValuePrevMonth'],r['Code']))
        sc=defaultdict(int); selected=[]
        for r in ranked:
            sec=r['Sector'] or 'Unknown'
            if sc[sec]>=SECTOR_CAP: continue
            selected.append(r['Code']); sc[sec]+=1
            if len(selected)>=SLOT_COUNT: break
        for r in cand:
            if r['Code'] in selected: r['Selected']=True; r['SelectionRank']=selected.index(r['Code'])+1
            rows.append(r)
    return pd.DataFrame(rows)


@dataclass
class Episode:
    code:int; name:str; sector:str; checkpoint:pd.Timestamp; entry_date:pd.Timestamp; entry_price:float; exit_date:pd.Timestamp|None=None; exit_price:float|None=None; exit_reason:str|None=None; dividends:float=0.0


def simulate_window(prices:pd.DataFrame, signals:pd.DataFrame, fundamentals:pd.DataFrame, cohort:pd.DataFrame, base:pd.Timestamp, end:pd.Timestamp) -> tuple[pd.DataFrame,pd.DataFrame,pd.DataFrame,dict[str,Any]]:
    name=cohort.set_index('Code')['Name'].to_dict(); sector=cohort.set_index('Code')['Sector'].to_dict()
    px=prices[(prices['Date']>=base)&(prices['Date']<=end)].copy(); bench=px[px['SecuritiesCode']==BENCHMARK_CODE].sort_values('Date')
    market_dates=pd.DatetimeIndex(bench['Date'].unique()); lookup=px.set_index(['Date','SecuritiesCode'])
    bycode={int(c):g.sort_values('Date') for c,g in px.groupby('SecuritiesCode')}
    trade_dates={c:pd.DatetimeIndex(g.loc[g['AdjOpen'].notna(),'Date'].unique()) for c,g in bycode.items()}
    last_dates={c:pd.Timestamp(g.loc[g['AdjClose'].notna(),'Date'].max()) for c,g in bycode.items() if g['AdjClose'].notna().any()}
    def getp(d,c,col):
        try:
            v=lookup.loc[(d,c),col]; v=v.iloc[-1] if isinstance(v,pd.Series) else v
            return None if pd.isna(v) else float(v)
        except KeyError:return None
    def nextd(c,after):
        ds=trade_dates.get(c,pd.DatetimeIndex([])); i=ds.searchsorted(after,side='right'); return pd.Timestamp(ds[i]) if i<len(ds) else None
    entry=defaultdict(list)
    if not signals.empty:
        for _,r in signals[signals['Selected'].fillna(False)].iterrows():
            d=nextd(int(r.Code),pd.Timestamp(r.Checkpoint));
            if d is not None and d<=end: entry[d].append(r.to_dict())
    neg=defaultdict(list)
    for _,r in fundamentals.iterrows():
        if bool(r.get('DownRevision',False)) or int(r.get('Score',0))<=-3:
            d=nextd(int(r.Code),pd.Timestamp(r.Checkpoint));
            if d is not None and d<=end: neg[d].append(int(r.Code))
    positions={}; expiries={}; active={}; episodes=[]; trades=[]; cash=100.0; total_turn=0.0; total_cost=0.0; nav_rows=[]; last_close={}
    bbase=getp(base,BENCHMARK_CODE,'AdjClose')
    if bbase is None:
        b0=bench[bench['Date']<=base]
        if b0.empty: raise RuntimeError('benchmark base missing')
        bbase=float(b0.iloc[-1]['AdjClose'])
    bprev=bbase; btr=100.0; prev_nav=100.0
    nav_rows.append({'Date':base,'NAV':100.0,'Cash':100.0,'PositionValue':0.0,'InvestedWeight':0.0,'ActiveCount':0,'BenchmarkPriceNAV':100.0,'BenchmarkTRNAV':100.0,'DailyReturn':0.0})
    for d in market_dates[market_dates>base]:
        d=pd.Timestamp(d)
        # dividends and update last closing marks for securities with a quote on this date
        divcash=0.0
        for c,q in list(positions.items()):
            dv=getp(d,c,'AdjExpectedDividend') or 0.0; divcash+=q*dv
        cash+=divcash
        for c,g in bycode.items():
            v=getp(d,c,'AdjClose')
            if v is not None: last_close[c]=v
        # executable forced data-end exit at the last available open when the series ends before window end
        forced=[]
        for c in list(positions):
            ld=last_dates.get(c)
            if ld is not None and d==ld and ld<end-pd.Timedelta(days=3) and getp(d,c,'AdjOpen') is not None:
                forced.append(c)
        exits={c:'data_end_or_delist' for c in forced}
        for c,exd in list(expiries.items()):
            if d>=exd and c in positions and getp(d,c,'AdjOpen') is not None: exits[c]='six_month_horizon'
        for c in neg.get(d,[]):
            if c in positions and getp(d,c,'AdjOpen') is not None: exits[c]='fundamental_break'
        current={}; nav_open=cash
        for c,q in positions.items():
            op=getp(d,c,'AdjOpen'); mark=op if op is not None else last_close.get(c)
            if mark is not None: current[c]=q*mark; nav_open+=q*mark
        targets=[c for c in positions if c not in exits]; sc=defaultdict(int)
        for c in targets: sc[sector.get(c,'Unknown')]+=1
        entries=[]
        for e in sorted(entry.get(d,[]),key=lambda x:(x.get('SelectionRank',999),x['Code'])):
            c=int(e['Code']); sec=sector.get(c,'Unknown')
            if c in targets or len(targets)>=SLOT_COUNT or sc[sec]>=SECTOR_CAP or getp(d,c,'AdjOpen') is None: continue
            targets.append(c); sc[sec]+=1; entries.append(e)
        turn=cost=0.0
        if exits or entries:
            target_each=nav_open/SLOT_COUNT
            for _ in range(4):
                turn=sum(abs((target_each if c in targets else 0.0)-current.get(c,0.0)) for c in set(current)|set(targets)); cost=turn*ONE_WAY_COST; target_each=max(nav_open-cost,0)/SLOT_COUNT
            for c,reason in exits.items():
                if c in active:
                    ep=active.pop(c); ep.exit_date=d; ep.exit_price=getp(d,c,'AdjOpen'); ep.exit_reason=reason; episodes.append(ep)
                expiries.pop(c,None)
            newpos={}
            for c in targets:
                op=getp(d,c,'AdjOpen')
                if op is not None and op>0: newpos[c]=target_each/op
                elif c in positions: newpos[c]=positions[c]
            # value allocated to tradable targets; residual stays cash
            allocated=sum((newpos[c]*(getp(d,c,'AdjOpen') or last_close.get(c,0))) for c in newpos)
            cash=max(nav_open-cost-allocated,0.0); positions=newpos; total_turn+=turn; total_cost+=cost
            for e in entries:
                c=int(e['Code']); op=getp(d,c,'AdjOpen')
                if c in positions and c not in active and op is not None:
                    active[c]=Episode(c,name.get(c,str(c)),sector.get(c,'Unknown'),pd.Timestamp(e['Checkpoint']),d,op)
                    target=d+pd.DateOffset(months=HOLD_MONTHS); ds=trade_dates.get(c,pd.DatetimeIndex([])); i=ds.searchsorted(target,side='left'); expiries[c]=pd.Timestamp(ds[i]) if i<len(ds) else end+pd.Timedelta(days=1)
            trades.append({'Date':d,'Entries':'|'.join(str(int(e['Code'])) for e in entries),'Exits':'|'.join(str(c) for c in exits),'Active':'|'.join(str(c) for c in sorted(positions)),'TurnoverNotional':turn,'TradingCost':cost})
        posval=0.0
        for c,q in positions.items():
            cl=getp(d,c,'AdjClose') or last_close.get(c)
            if cl is not None: posval+=q*cl
        nav=cash+posval; dret=nav/prev_nav-1 if prev_nav else 0
        bc=getp(d,BENCHMARK_CODE,'AdjClose') or bprev; bd=getp(d,BENCHMARK_CODE,'AdjExpectedDividend') or 0.0; btr*=1+(bc+bd)/bprev-1; bp=100*bc/bbase; bprev=bc
        nav_rows.append({'Date':d,'NAV':nav,'Cash':cash,'PositionValue':posval,'InvestedWeight':posval/nav if nav else 0,'ActiveCount':len(positions),'BenchmarkPriceNAV':bp,'BenchmarkTRNAV':btr,'DailyReturn':dret}); prev_nav=nav
    for c,ep in active.items():
        mark=last_close.get(c) or getp(end,c,'AdjClose')
        ep.exit_date=end; ep.exit_price=mark; ep.exit_reason='horizon_mark'; episodes.append(ep)
    nav=pd.DataFrame(nav_rows); nav['Peak']=nav.NAV.cummax(); nav['Drawdown']=nav.NAV/nav.Peak-1; nav['BPeak']=nav.BenchmarkTRNAV.cummax(); nav['BenchmarkDrawdown']=nav.BenchmarkTRNAV/nav.BPeak-1
    ret=nav.NAV.iloc[-1]/100-1; bret=nav.BenchmarkTRNAV.iloc[-1]/100-1; daily=nav.DailyReturn.iloc[1:]; excess=(nav.NAV.pct_change()-nav.BenchmarkTRNAV.pct_change()).dropna(); vol=float(daily.std(ddof=1)*math.sqrt(252)) if len(daily)>1 else np.nan; sharpe=float(daily.mean()*252/vol) if vol and vol>0 else np.nan; te=float(excess.std(ddof=1)*math.sqrt(252)) if len(excess)>1 else np.nan; ir=float(excess.mean()*252/te) if te and te>0 else np.nan
    monthly=nav.set_index('Date')[['NAV','BenchmarkTRNAV']].resample('ME').last().pct_change().dropna(); win=float((monthly.NAV>monthly.BenchmarkTRNAV).mean()) if len(monthly) else np.nan
    metrics={'period_base_close':str(base.date()),'period_end':str(end.date()),'strategy_net_return':ret,'benchmark_total_return_proxy':bret,'alpha_vs_total_return_proxy':ret-bret,'strategy_max_drawdown':float(nav.Drawdown.min()),'benchmark_max_drawdown':float(nav.BenchmarkDrawdown.min()),'strategy_annualized_volatility':vol,'sharpe_rf0':sharpe,'tracking_error':te,'information_ratio':ir,'monthly_excess_win_rate':win,'average_invested_weight':float(nav.InvestedWeight.mean()),'max_active_positions':int(nav.ActiveCount.max()),'entry_count':len(episodes),'one_way_turnover_initial_nav':total_turn/100,'total_trading_cost_initial_nav':total_cost/100}
    epdf=pd.DataFrame([e.__dict__ for e in episodes]); return nav,pd.DataFrame(trades),epdf,metrics


def stage_prices_run(pre_dir:Path,out_dir:Path)->None:
    out_dir.mkdir(parents=True,exist_ok=True)
    cohort=pd.read_csv(pre_dir/'universe_fixed_20211230_prime.csv'); fundamentals=pd.read_csv(pre_dir/'fundamentals_preprice.csv',parse_dates=['Checkpoint','WindowStart','WindowEnd']); events=pd.read_csv(pre_dir/'financial_events.csv')
    pass_codes=sorted(pd.read_csv(pre_dir/'fundamental_pass_codes.csv')['Code'].astype(int).tolist()) if (pre_dir/'fundamental_pass_codes.csv').stat().st_size>5 else []
    codes=sorted(set(pass_codes+[BENCHMARK_CODE])); price_frames=[]; audit=[]
    start='2022-03-01'; end='2026-07-02'
    with ThreadPoolExecutor(max_workers=THREADS_PRICE) as ex:
        futs={ex.submit(fetch_yf,c,start,end):c for c in codes}
        for i,f in enumerate(as_completed(futs),1):
            c,d,e=f.result(); audit.append({'Code':c,'OK':d is not None,'Error':e,'Rows':0 if d is None else len(d)})
            if d is not None: price_frames.append(d)
            if i%50==0: print('PRICE',i,'/',len(codes),flush=True)
    prices=pd.concat(price_frames,ignore_index=True) if price_frames else pd.DataFrame()
    prices.to_csv(out_dir/'prices.csv',index=False); pd.DataFrame(audit).to_csv(out_dir/'price_audit.csv',index=False)
    names=cohort.set_index('Code')['Name'].to_dict(); sectors=cohort.set_index('Code')['Sector'].to_dict()
    window_rows=[]; all_signals=[]; all_nav=[]; all_trades=[]; all_eps=[]
    for wid,base,w_end in WINDOWS:
        cps=xtks_checkpoints(base,w_end); f=fundamentals[fundamentals['Checkpoint'].isin(cps)].copy(); sig=build_signals(prices,f,cohort,cps)
        nav,tr,ep,m=simulate_window(prices,sig,f,cohort,base,w_end); m.update({'window':wid,'fixed_cohort_count':len(cohort),'price_code_count':prices.SecuritiesCode.nunique(),'fundamental_pass_events':int(f.FundamentalPass.fillna(False).sum()),'price_pass_events':int(sig.PricePass.fillna(False).sum()) if not sig.empty else 0,'selected_signal_events':int(sig.Selected.fillna(False).sum()) if not sig.empty else 0}); window_rows.append(m)
        sig['Window']=wid; nav['Window']=wid; tr['Window']=wid; ep['Window']=wid
        all_signals.append(sig); all_nav.append(nav); all_trades.append(tr); all_eps.append(ep)
    summary=pd.DataFrame(window_rows); summary.to_csv(out_dir/'late_window_metrics.csv',index=False); pd.concat(all_signals,ignore_index=True).to_csv(out_dir/'signals.csv',index=False); pd.concat(all_nav,ignore_index=True).to_csv(out_dir/'daily_nav.csv',index=False); pd.concat(all_trades,ignore_index=True).to_csv(out_dir/'trades.csv',index=False); pd.concat(all_eps,ignore_index=True).to_csv(out_dir/'episodes.csv',index=False)
    # aggregate descriptive metrics; each annual window remains independent NAV100.
    alphas=summary.alpha_vs_total_return_proxy.astype(float); strategy_chain=float(np.prod(1+summary.strategy_net_return.astype(float))-1); bench_chain=float(np.prod(1+summary.benchmark_total_return_proxy.astype(float))-1)
    aggregate={'windows':len(summary),'strategy_chained_return':strategy_chain,'benchmark_chained_return':bench_chain,'chained_alpha':strategy_chain-bench_chain,'median_window_alpha':float(alphas.median()),'positive_alpha_window_rate':float((alphas>0).mean()),'mean_information_ratio':float(summary.information_ratio.mean()),'mean_monthly_excess_win_rate':float(summary.monthly_excess_win_rate.mean()),'mean_invested_weight':float(summary.average_invested_weight.mean()),'mean_one_way_turnover':float(summary.one_way_turnover_initial_nav.mean()),'total_entries':int(summary.entry_count.sum()),'price_source':'Yahoo Finance via yfinance after pre-price fundamental freeze','fundamental_source':'official JPX listed-company service TDnet iXBRL','cohort':'2021-12-30 Prime Market fixed cohort'}
    (out_dir/'late_aggregate.json').write_text(json.dumps(aggregate,ensure_ascii=False,indent=2),encoding='utf-8')
    (out_dir/'preprice_manifest_copy.json').write_text((pre_dir/'preprice_manifest.json').read_text(encoding='utf-8'),encoding='utf-8')
    print(summary.to_string(index=False),flush=True); print(json.dumps(aggregate,ensure_ascii=False,indent=2),flush=True)


def main():
    ap=argparse.ArgumentParser(); sub=ap.add_subparsers(dest='cmd',required=True)
    p1=sub.add_parser('fundamentals'); p1.add_argument('--stock-list',type=Path,required=True); p1.add_argument('--out-dir',type=Path,required=True)
    p2=sub.add_parser('prices-run'); p2.add_argument('--pre-dir',type=Path,required=True); p2.add_argument('--out-dir',type=Path,required=True)
    a=ap.parse_args()
    if a.cmd=='fundamentals': stage_fundamentals(a.stock_list,a.out_dir)
    else: stage_prices_run(a.pre_dir,a.out_dir)

if __name__=='__main__': main()
