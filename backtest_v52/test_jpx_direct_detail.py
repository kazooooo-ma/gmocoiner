import requests
from bs4 import BeautifulSoup
s=requests.Session()
r=s.get('https://www2.jpx.co.jp/tseHpFront/JJK010010Action.do?Show=Show',timeout=30)
data={'BaseJh':'BaseJh','mgrCd':'72030','jjHisiFlg':'1','lstDspPg':'1','dspGs':'200','souKnsu':'1','sniMtGmnId':'JJK010010','dspJnKbn':'0','dspJnKmkNo':'0'}
d=s.post('https://www2.jpx.co.jp/tseHpFront/JJK010030Action.do',data=data,timeout=30); d.encoding='utf-8'
ss=BeautifulSoup(d.text,'html.parser')
print(d.status_code,len(d.text),ss.title.get_text(' ',strip=True) if ss.title else '')
print(ss.get_text(' ',strip=True)[:1000])
print('ixbrl',len([a for a in ss.find_all('a',href=True) if 'ixbrl' in a['href']]))
