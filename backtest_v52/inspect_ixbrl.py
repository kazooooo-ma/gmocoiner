import requests,re
from bs4 import BeautifulSoup
urls=[
'https://www2.jpx.co.jp/disc/72030/081220231017567975_tse-qcedifsm-72030-20231101372030-ixbrl.htm',
'https://www2.jpx.co.jp/disc/72030/081220240424575411_tse-acedifsm-72030-20240508372030-ixbrl.htm',
]
for u in urls:
    r=requests.get(u,timeout=30); print('\nURL',u,r.status_code,len(r.text),r.encoding,r.apparent_encoding)
    r.encoding=r.apparent_encoding
    s=BeautifulSoup(r.text,'html.parser')
    facts=[]
    for tag in s.find_all(lambda t: t.name and (t.name.lower().endswith('nonfraction') or t.name.lower().endswith('nonnumeric'))):
        name=tag.get('name',''); val=tag.get_text(' ',strip=True); ctx=tag.get('contextref',''); scale=tag.get('scale',''); sign=tag.get('sign','')
        if re.search(r'operat|ordinary|profit|earnings|forecast|dividend|income|sales|revenue',name,re.I):
            facts.append((name,val,ctx,scale,sign))
    print('FACTS',len(facts))
    for x in facts[:400]: print('\t'.join(x))
