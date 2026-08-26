from __future__ import annotations

import argparse
import csv
import re
import subprocess
from pathlib import Path

import requests

PDF_URL = "https://www.jpx.co.jp/markets/statistics-equities/price/aocfb40000004193-att/st_202307.pdf"
EXPECTED = {"P": 1833, "S": 1439}
ROW_START_RE = re.compile(r"^\s*2023/07\s+\d{4}\s+")
ROW_RE = re.compile(r"^\s*2023/07\s+(\d{4})\s+(.+?)\s+普通株式\s+(.+)$")
SECTION_RE = re.compile(r"\s(TPM|P|S|G)(?:外)?\s+(?:(?:貸借|信用|特|審|確|監|整)\s+)*100(?:\s|$)")


def download(url: str, path: Path) -> None:
    r = requests.get(url, timeout=90, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    path.write_bytes(r.content)


def extract_layout(pdf: Path, txt: Path) -> None:
    subprocess.run(["pdftotext", "-layout", str(pdf), str(txt)], check=True)


def logical_rows(text: str) -> list[str]:
    out: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if ROW_START_RE.match(line):
            if current:
                out.append(" ".join(x.strip() for x in current if x.strip()))
            current = [line]
        elif current and line.strip():
            current.append(line)
    if current:
        out.append(" ".join(x.strip() for x in current if x.strip()))
    return out


def parse_rows(text: str) -> list[dict[str, str]]:
    rows: dict[int, dict[str, str]] = {}
    diagnostics: list[str] = []
    for raw in logical_rows(text):
        m = ROW_RE.match(raw)
        if not m:
            continue
        code = int(m.group(1))
        name = m.group(2).strip()
        rest = m.group(3)
        sec_matches = list(SECTION_RE.finditer(" " + rest))
        if len(sec_matches) != 1:
            diagnostics.append(f"SECTION_PARSE[{code}] matches={len(sec_matches)} line={raw[:500]}")
            continue
        sm = sec_matches[0]
        sec = sm.group(1)
        token = sm.group(0)
        is_foreign = "外" in token
        rows[code] = {"Code": str(code), "NameJP": name, "Section202307": sec, "ForeignFlag": "1" if is_foreign else "0", "Source": PDF_URL}
    if diagnostics:
        print("\n".join(diagnostics[:100]))
    return [rows[k] for k in sorted(rows)]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--allow-count-mismatch", action="store_true")
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    pdf = args.out_dir / "st_202307.pdf"
    txt = args.out_dir / "st_202307_layout.txt"
    download(PDF_URL, pdf)
    extract_layout(pdf, txt)
    rows = parse_rows(txt.read_text(encoding="utf-8", errors="replace"))
    if not rows:
        raise SystemExit("No JPX common-stock rows parsed")
    with (args.out_dir / "jpx_202307_parsed_common_stocks.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
    pit = [r for r in rows if r["Section202307"] in {"P", "S"} and r["ForeignFlag"] == "0"]
    counts = {s: sum(r["Section202307"] == s for r in pit) for s in ("P", "S")}
    with (args.out_dir / "v6_universe_20230731_candidate.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(pit)
    print("logical_rows", len(logical_rows(txt.read_text(encoding="utf-8", errors="replace"))))
    print("parsed_common_stocks", len(rows))
    print("candidate_prime", counts["P"], "expected", EXPECTED["P"])
    print("candidate_standard", counts["S"], "expected", EXPECTED["S"])
    print("candidate_total", len(pit), "expected", sum(EXPECTED.values()))
    if counts != EXPECTED and not args.allow_count_mismatch:
        raise SystemExit("PIT universe count mismatch. Do not use formally until reconciled.")


if __name__ == "__main__":
    main()
