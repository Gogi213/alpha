# Замер наличия и размера по выборке: инструменты видео + BTC/ETH × биржа. CHFT (час, parquet), Bybit (сутки, zip), Gate (час, csv.gz), спот Bybit.
# Запуск на srv: python3 probe_video.py universe-assets.csv > probe_video.json
import csv,json,sys,urllib.request,urllib.error,concurrent.futures as cf
env=dict(l.strip().split('=',1) for l in open('/root/.chft.env') if '=' in l); K=env['CRYPTOHFTDATA_API_KEY']
A='https://api.cryptohftdata.com'
VID=['BRETT','TA','PORT3','LAB','WCT','H','LPT','BROCCOLI','ONT','APE','ZHIPU','MINIMAX','TENCENT','LENOVO','XIAOMI','SHEIN','BTC','ETH']
class NR(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*a,**k): return None
op=urllib.request.build_opener(NR)
def req(u,h=None,m='GET'):
    r=urllib.request.Request(u,headers=h or {},method=m)
    try:
        x=op.open(r,timeout=40); return x.status,dict(x.headers)
    except urllib.error.HTTPError as e: return e.code,dict(e.headers)
    except Exception as e: return -1,{}
def size_of(url,h=None):
    c,hd=req(url,dict(h or {},Range='bytes=0-0'))
    cr=hd.get('Content-Range') or hd.get('content-range')
    if cr and '/' in cr: return int(cr.split('/')[1])
    return int(hd.get('Content-Length') or hd.get('content-length') or 0) if c in(200,206) else 0
def chft(ex,sym,day,hour,typ='orderbook'):
    c,hd=req(f'{A}/v1/download?file={ex}/{day}/{hour}/{sym}_{typ}.parquet',{'X-API-Key':K})
    if c!=302: return c,0
    return 302,size_of(hd.get('Location') or hd.get('location'))
def bybit(sym,day):
    u=f'https://quote-saver.bycsi.com/orderbook/linear/{sym}/{day}_{sym}_ob200.data.zip'
    return req(u,m='HEAD')[0],size_of(u)
def gate(c,day,hour):
    d=day.replace('-',''); fn=f'{c}-{d}{hour}.csv.gz'; u=f'https://download.gatedata.org/futures_usdt/orderbooks/{d[:6]}/{fn}'
    s=size_of(u); return (200 if s else 404),s
DAYS=['2026-09-17','2026-09-24','2026-10-01','2026-10-08','2026-10-15','2026-10-22']
SZ=[('2026-10-01','04'),('2026-10-01','12'),('2026-10-01','20'),('2026-10-15','04'),('2026-10-15','12'),('2026-10-15','20')]
rows=[r for r in csv.DictReader(open(sys.argv[1])) if r['asset'] in VID]
tasks=[]
for r in rows:
    for ex in ['binance_futures','okx_futures','bitget_futures','aster_futures','hyperliquid_futures','bybit_futures','gate_futures']:
        s=r[ex]
        if not s: continue
        if ex=='hyperliquid_futures' and s.startswith('HYNA:'): s=s[5:]
        tasks.append((r['asset'],ex,s))
    if r['asset'] in('BRETT','TA'): tasks.append((r['asset'],'bybit_spot',r['bybit_futures']))
def run(t):
    a,ex,s=t; out={'asset':a,'ex':ex,'sym':s,'avail':{}, 'sz':{}}
    if ex=='bybit_futures':
        for d in DAYS: c,n=bybit(s,d); out['avail'][d]=c==200; out['sz'][d]=n
    elif ex=='gate_futures':
        for d in DAYS: c,n=gate(s,d,'12'); out['avail'][d]=c==200
        for d,h in SZ: out['sz'][d+'T'+h]=gate(s,d,h)[1]
    else:
        for d in DAYS: c,n=chft(ex,s,d,'12'); out['avail'][d]=c==302
        for d,h in SZ: out['sz'][d+'T'+h]=chft(ex,s,d,h)[1]
    return out
with cf.ThreadPoolExecutor(24) as p: res=list(p.map(run,tasks))
json.dump(res,sys.stdout,ensure_ascii=False)
