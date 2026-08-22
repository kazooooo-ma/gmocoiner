import re, requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
url='https://www.jpx.co.jp/listing/co-search/index.html'
t=requests.get(url,timeout=20).text
s=BeautifulSoup(t,'html.parser')
print('IFRAMES')
for x in s.find_all('iframe'): print(x)
print('FORMS')
for x in s.find_all('form'): print(x)
print('OBJECT/EMBED')
for x in s.find_all(['object','embed']): print(x)
print('URLS')
for u in sorted(set(re.findall(r'https?://[^\"\'\s<>]+',t))): print(u)
print('COSEARCH LINES')
for i,line in enumerate(t.splitlines(),1):
    if any(k.lower() in line.lower() for k in ['co-search','company','iframe','form','input','tsehp','stock']):
        print(i,line[:3000])
