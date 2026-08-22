#!/usr/bin/env bash
set -euo pipefail
mkdir -p /tmp/jpx_inspect
cd /tmp/jpx_inspect
curl -L --fail --silent --show-error 'https://www.jpx.co.jp/listing/co-search/index.html' -o index.html
printf '%s\n' '=== HTML refs ==='
grep -oE '(src|href)="[^"]+"' index.html | sed 's/^[^=]*="//;s/"$//' | grep -E '\.js|co-search|tseHp|company' | head -200 || true
python - <<'PY'
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import requests,re
html=open('index.html',encoding='utf-8',errors='ignore').read()
s=BeautifulSoup(html,'html.parser')
urls=[]
for tag in s.find_all(['script','link']):
    u=tag.get('src') or tag.get('href')
    if u and ('.js' in u or 'co-search' in u):
        urls.append(urljoin('https://www.jpx.co.jp/listing/co-search/index.html',u))
for i,u in enumerate(dict.fromkeys(urls)):
    print('FETCH',u)
    try:
        t=requests.get(u,timeout=20).text
    except Exception as e:
        print('ERR',e);continue
    open(f'asset_{i}.txt','w',encoding='utf-8').write(t)
    for line in t.splitlines():
        if re.search(r'(tseHpFront|co-search|ajax|search|company|stock|issue|api)',line,re.I):
            print(line[:2000])
PY
