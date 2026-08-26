from __future__ import annotations

import argparse
import csv
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, asdict
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urljoin

import pandas as pd
import requests
from bs4 import BeautifulSoup

SEARCH_URL = "https://www2.jpx.co.jp/tseHpFront/JJK010010Action.do?Show=Show"
DETAIL_URL = "https://www2.jpx.co.jp/tseHpFront/JJK010030Action.do"
DISC_BASE = "https://www2.jpx.co.jp"
ROW_ID_RE = re.compile(r"^(110[1-4])_(\d+)$")
DATE_RE = re.compile(r"^\d{4}/\d{2}/\d{2}$")
XBRL_ZIP_RE = re.compile(r"'(/\d+/\d+\.zip)'")
CATEGORY = {"1101":"financial_results","1102":"decision_or_occurrence","1103":"other_timely_disclosure","1104":"other_information"}
_tls=threading.local()

@dataclass
class DisclosureRow:
    code:int; jpx_code5:str; name_jp:str; market_at_fetch:str; industry_at_fetch:str
    disclosure_date:str; category:str; title:str; pdf_url:str; ixbrl_url:str
    qualitative_url:str; xbrl_zip_url:str; source_detail_url:str; fetched_at_utc:str

def session():
    s=getattr(_tls,"session",None)
    if s is None:
        s=requests.Session();s.headers.update({"User-Agent":"Mozilla/5.0 (compatible; v6-research-audit/1.2)"})
        # Establish the JPX session once per worker. Subsequent issuer-detail requests are one POST per company.
        r=s.get(SEARCH_URL,timeout=45);r.raise_for_status();_tls.session=s
    return s

def hidden_inputs(form):
    return {x.get("name"):x.get("value","") for x in form.find_all("input") if x.get("name") and x.get("type")=="hidden" and not x.has_attr("disabled")}

def direct_detail(code:int):
    s=session();jpx_code5=f"{int(code)}0"
    data={"BaseJh":"BaseJh","mgrCd":jpx_code5,"jjHisiFlg":"1","lstDspPg":"1","dspGs":"200","souKnsu":"1","sniMtGmnId":"JJK010010","dspJnKbn":"0","dspJnKmkNo":"0"}
    d=s.post(DETAIL_URL,data=data,timeout=60);d.raise_for_status();d.encoding="utf-8";ds=BeautifulSoup(d.text,"html.parser")
    text=ds.get_text(" ",strip=True)
    if "上場会社詳細" not in text or not re.search(rf"(?:^|\s){code}0?(?:\s|$)",text):
        raise RuntimeError(f"Direct JPX detail validation failed for {code}")
    # Metadata is informational; PIT market/industry come only from the July-2023 quotation table.
    return ds,{"jpx_code5":jpx_code5,"eqMgrNm":"","szkbuNm":"","gyshDspNm":"","detail_url":d.url}

def search_detail_fallback(code:int):
    s=session();r=s.get(SEARCH_URL,timeout=45);r.raise_for_status();r.encoding="utf-8";f=BeautifulSoup(r.text,"html.parser").find("form")
    if not f:raise RuntimeError("JPX search form not found")
    payload=hidden_inputs(f);payload.update({"eqMgrCd":str(code),"mgrMiTxtBx":"","dspSsuPd":"200","ListShow":"ListShow"})
    for k in ["Show","Switch"]:payload.pop(k,None)
    rr=s.post(urljoin(r.url,f.get("action")),data=payload,timeout=45);rr.raise_for_status();rr.encoding="utf-8";rf=BeautifulSoup(rr.text,"html.parser").find("form")
    if not rf:raise RuntimeError(f"JPX result form not found for {code}")
    target=None
    for b in rf.find_all("input",attrs={"type":"button"}):
        m=re.search(r"gotoBaseJh\('(\d+)'\s*,\s*'(\d+)'\)",b.get("onclick",""))
        if m and m.group(1)==f"{code}0":target=(m.group(1),m.group(2));break
    if not target:raise RuntimeError(f"JPX exact search result not found for {code}")
    jpx_code5,hist_flag=target;rd=hidden_inputs(rf);rd.update({"mgrCd":jpx_code5,"jjHisiFlg":hist_flag,"BaseJh":"BaseJh"})
    for k in ["Transition","Show","Return","Sort"]:rd.pop(k,None)
    d=s.post(urljoin(rr.url,rf.get("action")),data=rd,timeout=60);d.raise_for_status();d.encoding="utf-8";ds=BeautifulSoup(d.text,"html.parser")
    return ds,{"jpx_code5":jpx_code5,"eqMgrNm":"","szkbuNm":"","gyshDspNm":"","detail_url":d.url}

def fetch_detail(code:int):
    try:return direct_detail(code)
    except Exception as first:
        try:return search_detail_fallback(code)
        except Exception as second:raise RuntimeError(f"direct={first!r}; fallback={second!r}")

def extract_rows(ds,code,meta,start,end):
    rows=[];fetched=datetime.utcnow().replace(microsecond=0).isoformat()+"Z";seen=set()
    for tr in ds.find_all("tr",id=ROW_ID_RE):
        cat_code=str(tr.get("id")).split("_",1)[0];cells=tr.find_all("td",recursive=False)
        if len(cells)<2:continue
        date_text=cells[0].get_text(" ",strip=True)
        if not DATE_RE.match(date_text):continue
        dt=datetime.strptime(date_text,"%Y/%m/%d").date()
        if dt<start or dt>end:continue
        title_link=cells[1].find("a",href=True);title=title_link.get_text(" ",strip=True) if title_link else cells[1].get_text(" ",strip=True)
        pdf_url=urljoin(DISC_BASE,title_link["href"]) if title_link and title_link.get("href","").lower().endswith(".pdf") else "";ixbrl=qualitative=xbrl_zip=""
        for a in tr.find_all("a",href=True):
            href=a["href"];full=urljoin(DISC_BASE,href);low=href.lower()
            if "ixbrl.htm" in low:ixbrl=full
            elif "_qualitative.htm" in low:qualitative=full
            elif low.endswith(".pdf") and not pdf_url:pdf_url=full
        for img in tr.find_all("img"):
            m=XBRL_ZIP_RE.search(img.get("onclick",""))
            if m:xbrl_zip=urljoin(DISC_BASE+"/disc",m.group(1));break
        key=(date_text,title,pdf_url)
        if key in seen:continue
        seen.add(key);rows.append(DisclosureRow(code,meta.get("jpx_code5",""),meta.get("eqMgrNm",""),meta.get("szkbuNm",""),meta.get("gyshDspNm",""),dt.isoformat(),CATEGORY.get(cat_code,cat_code),title,pdf_url,ixbrl,qualitative,xbrl_zip,meta.get("detail_url",""),fetched))
    return rows

def one(code,start,end,retries):
    err=None
    for attempt in range(retries+1):
        try:
            ds,meta=fetch_detail(code);return extract_rows(ds,code,meta,start,end),None
        except Exception as e:err=repr(e);time.sleep(0.5*(attempt+1))
    return [],err

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--universe",type=Path,required=True);ap.add_argument("--out",type=Path,required=True);ap.add_argument("--start",default="2023-08-01");ap.add_argument("--end",default="2024-07-31");ap.add_argument("--workers",type=int,default=8);ap.add_argument("--retries",type=int,default=2);ap.add_argument("--shard",type=int,default=0);ap.add_argument("--shards",type=int,default=1);ap.add_argument("--limit",type=int,default=0);ap.add_argument("--allow-errors",action="store_true");args=ap.parse_args();start=date.fromisoformat(args.start);end=date.fromisoformat(args.end)
    uni=pd.read_csv(args.universe);code_col="Code" if "Code" in uni.columns else "code";all_codes=[int(x) for x in uni[code_col].dropna().astype(int)];codes=[c for i,c in enumerate(all_codes) if i%args.shards==args.shard]
    if args.limit>0:codes=codes[:args.limit]
    all_rows=[];errors=[]
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs={ex.submit(one,c,start,end,args.retries):c for c in codes}
        for n,fut in enumerate(as_completed(futs),1):
            c=futs[fut];rs,err=fut.result();all_rows.extend(rs)
            if err:errors.append({"Code":str(c),"Error":err})
            if n%25==0 or n==len(codes):print(f"shard {args.shard}/{args.shards} {n}/{len(codes)} rows={len(all_rows)} errors={len(errors)}")
    all_rows.sort(key=lambda r:(r.code,r.disclosure_date,r.category,r.title));args.out.parent.mkdir(parents=True,exist_ok=True);fields=list(DisclosureRow.__annotations__.keys())
    with args.out.open("w",newline="",encoding="utf-8-sig") as f:w=csv.DictWriter(f,fieldnames=fields);w.writeheader();[w.writerow(asdict(r)) for r in all_rows]
    err_path=args.out.with_name(args.out.stem+"_errors.csv")
    with err_path.open("w",newline="",encoding="utf-8-sig") as f:w=csv.DictWriter(f,fieldnames=["Code","Error"]);w.writeheader();w.writerows(errors)
    print("codes",len(codes),"rows",len(all_rows),"errors",len(errors))
    if errors and not args.allow_errors:raise SystemExit("Disclosure ledger incomplete: errors must be resolved before V6 fundamentals are frozen.")
if __name__=="__main__":main()
