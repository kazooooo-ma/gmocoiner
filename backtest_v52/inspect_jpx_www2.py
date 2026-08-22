import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
u='https://www2.jpx.co.jp/tseHpFront/JJK010010Action.do?Show=Show'
sess=requests.Session(); r=sess.get(u,timeout=30); print('GET',r.status_code,r.url)
s=BeautifulSoup(r.text,'html.parser')
for i,f in enumerate(s.find_all('form')):
    print('FORM',i,f.get('method'),f.get('action'))
    for x in f.find_all(['input','select','button']):
        print(' ',x.name,x.get('type'),x.get('name'),x.get('value'),[o.get('value') for o in x.find_all('option')[:10]] if x.name=='select' else '')
print('LINKS')
for a in s.find_all('a',href=True):
    h=a['href']
    if 'Action' in h or 'JJK' in h or 'tseHp' in h: print(urljoin(r.url,h),a.get_text(' ',strip=True)[:80])
