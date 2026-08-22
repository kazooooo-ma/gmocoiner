import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
base='https://www2.jpx.co.jp/tseHpFront/JJK010010Action.do?Show=Show'
sess=requests.Session()
r=sess.get(base,timeout=30)
s=BeautifulSoup(r.text,'html.parser')
f=s.find('form')
a=urljoin(r.url,f.get('action'))
# construct only successful controls; ListShow is the likely action dispatch key
data={}
for x in f.find_all('input'):
    if x.get('name') and x.get('type')=='hidden': data[x['name']]=x.get('value','')
data.update({'eqMgrCd':'7203','mgrMiTxtBx':'','dspSsuPd':'200','ListShow':'ListShow'})
# remove dispatch controls that may conflict
for k in ['Show','Switch']:
    data.pop(k,None)
print('POST',a,data)
rr=sess.post(a,data=data,timeout=30)
print('RESP',rr.status_code,rr.url,len(rr.text),rr.apparent_encoding)
rr.encoding=rr.apparent_encoding
ss=BeautifulSoup(rr.text,'html.parser')
print('TITLE',ss.title.get_text(' ',strip=True) if ss.title else '')
print('TEXT',ss.get_text(' ',strip=True)[:3000])
print('LINKS')
for x in ss.find_all('a',href=True):
    h=x['href']; txt=x.get_text(' ',strip=True)
    if 'JJK' in h or 'Action' in h or '7203' in h or txt:
        print(urljoin(rr.url,h),txt[:200])
print('FORMS')
for i,ff in enumerate(ss.find_all('form')):
    print('FORM',i,ff.get('method'),ff.get('action'))
    for x in ff.find_all(['input','select'])[:80]: print(' ',x.name,x.get('type'),x.get('name'),x.get('value'))
