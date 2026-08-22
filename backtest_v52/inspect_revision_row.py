import requests,re
from bs4 import BeautifulSoup
s=requests.Session(); s.get('https://www2.jpx.co.jp/tseHpFront/JJK010010Action.do?Show=Show',timeout=30)
data={'BaseJh':'BaseJh','mgrCd':'72030','jjHisiFlg':'1','lstDspPg':'1','dspGs':'200','souKnsu':'1','sniMtGmnId':'JJK010010','dspJnKbn':'0','dspJnKmkNo':'0'}
r=s.post('https://www2.jpx.co.jp/tseHpFront/JJK010030Action.do',data=data,timeout=30); r.encoding='utf-8'; bs=BeautifulSoup(r.text,'html.parser')
for tr in bs.find_all('tr'):
    txt=tr.get_text(' ',strip=True)
    if '業績予想の修正' in txt or '配当予想の修正' in txt:
        print('ROW',txt)
        print(tr.prettify()[:12000])
