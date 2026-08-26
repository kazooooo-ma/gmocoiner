from __future__ import annotations

import argparse
import csv
import re
import time
from dataclasses import dataclass, asdict
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urljoin

import pandas as pd
import requests
from bs4 import BeautifulSoup

SEARCH_URL = "https://www2.jpx.co.jp/tseHpFront/JJK010010Action.do?Show=Show"
DISC_BASE = "https://www2.jpx.co.jp"
ROW_ID_RE = re.compile(r"^(110[1-4])_(\d+)$")
DATE_RE = re.compile(r"^\d{4}/\d{2}/\d{2}$")
XBRL_ZIP_RE = re.compile(r"'(/\d+/\d+\.zip)'" )

CATEGORY = {
    "1101": "financial_results",
    "1102": "decision_or_occurrence",
    "1103": "other_timely_disclosure",
    "1104": "other_information",
}

@dataclass
class DisclosureRow:
    code: int
    jpx_code5: str
    name_jp: str
    market_at_fetch: str
    industry_at_fetch: str
    disclosure_date: str
    category: str
    title: str
    pdf_url: str
    ixbrl_url: str
    qualitative_url: str
    xbrl_zip_url: str
    source_detail_url: str
    fetched_at_utc: str


def hidden_inputs(form) -> dict[str, str]:
    return {
        x.get("name"): x.get("value", "")
        for x in form.find_all("input")
        if x.get("name") and x.get("type") == "hidden" and not x.has_attr("disabled")
    }


def fetch_detail(session: requests.Session, code: int) -> tuple[BeautifulSoup, dict[str, str]]:
    r = session.get(SEARCH_URL, timeout=45)
    r.raise_for_status(); r.encoding = "utf-8"
    s = BeautifulSoup(r.text, "html.parser")
    f = s.find("form")
    if not f:
        raise RuntimeError("JPX search form not found")
    payload = hidden_inputs(f)
    payload.update({"eqMgrCd": str(code), "mgrMiTxtBx": "", "dspSsuPd": "200", "ListShow": "ListShow"})
    for k in ["Show", "Switch"]:
        payload.pop(k, None)
    rr = session.post(urljoin(r.url, f.get("action")), data=payload, timeout=45)
    rr.raise_for_status(); rr.encoding = "utf-8"
    ss = BeautifulSoup(rr.text, "html.parser")
    rf = ss.find("form")
    if not rf:
        raise RuntimeError(f"JPX result form not found for {code}")

    # Match exact 4-digit code; JPX internally appends a zero to domestic common stock codes.
    target = None
    for b in rf.find_all("input", attrs={"type": "button"}):
        onclick = b.get("onclick", "")
        m = re.search(r"gotoBaseJh\('(\d+)'\s*,\s*'(\d+)'\)", onclick)
        if m and m.group(1).startswith(str(code)):
            target = (m.group(1), m.group(2))
            break
    if not target:
        raise RuntimeError(f"JPX exact search result not found for {code}")

    jpx_code5, hist_flag = target
    rd = hidden_inputs(rf)
    rd.update({"mgrCd": jpx_code5, "jjHisiFlg": hist_flag, "BaseJh": "BaseJh"})
    for k in ["Transition", "Show", "Return", "Sort"]:
        rd.pop(k, None)
    d = session.post(urljoin(rr.url, rf.get("action")), data=rd, timeout=60)
    d.raise_for_status(); d.encoding = "utf-8"
    ds = BeautifulSoup(d.text, "html.parser")

    result_meta = {}
    for name in ["eqMgrNm", "szkbuNm", "gyshDspNm"]:
        x = rf.find("input", attrs={"name": re.compile(re.escape(name) + r"$")})
        result_meta[name] = x.get("value", "") if x else ""
    result_meta["jpx_code5"] = jpx_code5
    result_meta["detail_url"] = d.url
    return ds, result_meta


def extract_rows(ds: BeautifulSoup, code: int, meta: dict[str, str], start: date, end: date) -> list[DisclosureRow]:
    rows: list[DisclosureRow] = []
    fetched = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    seen: set[tuple[str, str, str]] = set()
    for tr in ds.find_all("tr", id=ROW_ID_RE):
        rid = str(tr.get("id"))
        cat_code = rid.split("_", 1)[0]
        cells = tr.find_all("td", recursive=False)
        if len(cells) < 2:
            continue
        date_text = cells[0].get_text(" ", strip=True)
        if not DATE_RE.match(date_text):
            continue
        d = datetime.strptime(date_text, "%Y/%m/%d").date()
        if d < start or d > end:
            continue
        title_link = cells[1].find("a", href=True)
        title = title_link.get_text(" ", strip=True) if title_link else cells[1].get_text(" ", strip=True)
        pdf_url = ""
        if title_link and title_link.get("href", "").lower().endswith(".pdf"):
            pdf_url = urljoin(DISC_BASE, title_link["href"])
        ixbrl = ""
        qualitative = ""
        xbrl_zip = ""
        for a in tr.find_all("a", href=True):
            href = a["href"]
            full = urljoin(DISC_BASE, href)
            low = href.lower()
            if "ixbrl.htm" in low:
                ixbrl = full
            elif "_qualitative.htm" in low:
                qualitative = full
            elif low.endswith(".pdf") and not pdf_url:
                pdf_url = full
        for img in tr.find_all("img"):
            onclick = img.get("onclick", "")
            m = XBRL_ZIP_RE.search(onclick)
            if m:
                xbrl_zip = urljoin(DISC_BASE + "/disc", m.group(1))
                break
        key = (date_text, title, pdf_url)
        if key in seen:
            continue
        seen.add(key)
        rows.append(DisclosureRow(
            code=code,
            jpx_code5=meta.get("jpx_code5", ""),
            name_jp=meta.get("eqMgrNm", ""),
            market_at_fetch=meta.get("szkbuNm", ""),
            industry_at_fetch=meta.get("gyshDspNm", ""),
            disclosure_date=d.isoformat(),
            category=CATEGORY.get(cat_code, cat_code),
            title=title,
            pdf_url=pdf_url,
            ixbrl_url=ixbrl,
            qualitative_url=qualitative,
            xbrl_zip_url=xbrl_zip,
            source_detail_url=meta.get("detail_url", ""),
            fetched_at_utc=fetched,
        ))
    return sorted(rows, key=lambda r: (r.disclosure_date, r.category, r.title, r.pdf_url))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--start", default="2023-08-01")
    ap.add_argument("--end", default="2024-07-31")
    ap.add_argument("--sleep", type=float, default=0.20)
    ap.add_argument("--limit", type=int, default=0, help="diagnostic only")
    args = ap.parse_args()
    start = date.fromisoformat(args.start); end = date.fromisoformat(args.end)
    uni = pd.read_csv(args.universe)
    code_col = "Code" if "Code" in uni.columns else "code"
    codes = [int(x) for x in uni[code_col].dropna().astype(int).tolist()]
    if args.limit > 0:
        codes = codes[: args.limit]

    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; v6-research-audit/1.0)"})
    all_rows: list[DisclosureRow] = []
    errors: list[dict[str, str]] = []
    for i, code in enumerate(codes, 1):
        try:
            ds, meta = fetch_detail(session, code)
            rs = extract_rows(ds, code, meta, start, end)
            all_rows.extend(rs)
            print(f"{i}/{len(codes)} {code} disclosures={len(rs)}")
        except Exception as e:
            errors.append({"Code": str(code), "Error": repr(e)})
            print(f"ERROR {code} {e!r}")
        time.sleep(args.sleep)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    fields = list(asdict(all_rows[0]).keys()) if all_rows else list(DisclosureRow.__annotations__.keys())
    with args.out.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader()
        for r in all_rows: w.writerow(asdict(r))
    err_path = args.out.with_name(args.out.stem + "_errors.csv")
    with err_path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["Code", "Error"]); w.writeheader(); w.writerows(errors)
    print("codes", len(codes), "rows", len(all_rows), "errors", len(errors))
    if errors:
        raise SystemExit("Disclosure ledger incomplete: errors must be resolved before V6 fundamentals are frozen.")


if __name__ == "__main__":
    main()
