import requests,re
from bs4 import BeautifulSoup
from urllib.parse import urljoin
sess=requests.Session()
start='https://www2.jpx.co.jp/tseHpFront/JJK010010Action.do?Show=Show'
r=sess.get(start,timeout=30); s=BeautifulSoup(r.text,'html.parser'); f=s.find('form')
# Search 7203
a=urljoin(r.url,f.get('action'))
data={x.get('name'):x.get('value','') for x in f.find_all('input') if x.get('name') and x.get('type')=='hidden'}
data.update({'eqMgrCd':'7203','mgrMiTxtBx':'','dspSsuPd':'200','ListShow':'ListShow'})
for k in ['Show','Switch']: data.pop(k,None)
rr=sess.post(a,data=data,timeout=30); rr.encoding='utf-8'; ss=BeautifulSoup(rr.text,'html.parser')
rf=ss.find('form'); print('RESULT FORM',rf.get('method'),rf.get('action'))
for script in ss.find_all('script'):
    txt=script.string or script.get_text() or ''
    if 'gotoBaseJh' in txt:
        m=re.search(r'function\s+gotoBaseJh\s*\([^}]+}',txt,re.S)
        print('GOTOBASE',m.group(0)[:3000] if m else txt[:3000])
for b in rf.find_all(['input','button']):
    if b.get('type') in ['button','submit'] or b.name=='button': print('BUTTON',b.attrs)
# submit detail using same dispatch flag as the browser JS: BaseJh
rd={x.get('name'):x.get('value','') for x in rf.find_all('input') if x.get('name') and x.get('type')=='hidden'}
rd['mgrCd']='72030'; rd['jjHisiFlg']='1'; rd['BaseJh']='BaseJh'
for k in ['Transition','Show','Return','Sort']: rd.pop(k,None)
da=urljoin(rr.url,rf.get('action'))
print('DETAIL POST',da,rd)
d=sess.post(da,data=rd,timeout=30); print('DRESP',d.status_code,d.url,len(d.text)); d.encoding='utf-8'; ds=BeautifulSoup(d.text,'html.parser')
print('TEXT',ds.get_text(' ',strip=True)[:12000])
print('LINKS')
for x in ds.find_all('a',href=True):
    h=x['href']; txt=x.get_text(' ',strip=True)
    if txt or 'JJK' in h or '.pdf' in h or '.zip' in h or 'xbrl' in h.lower():
        print(urljoin(d.url,h),txt[:200])
print('FORMS')
for i,ff in enumerate(ds.find_all('form')):
    print('FORM',i,ff.get('method'),ff.get('action'))
    for x in ff.find_all(['input','select','button'])[:300]:
        print(' ',x.name,x.get('type'),x.get('name'),x.get('value'),x.attrs)
