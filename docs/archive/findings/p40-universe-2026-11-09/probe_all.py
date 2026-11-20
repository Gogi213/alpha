# Замер наличия (6 дат) и размера (2 часа 12:00) по ВСЕЙ вселенной: актив × биржа. CHFT (час, parquet), Bybit (сутки, zip), Gate (час, csv.gz), спот Bybit.
# Запуск на srv: python3 probe_all.py universe-assets.csv > probe_all.jsonl (строка на пару; метка .done)
import csv,json,sys,threading,urllib.request,urllib.error,concurrent.futures as cf
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
SZD=('2026-10-01','2026-10-15')
rows=list(csv.DictReader(open(sys.argv[1])))
tasks=[]
for r in rows:
    for ex in ['binance_futures','okx_futures','bitget_futures','aster_futures','hyperliquid_futures','bybit_futures','gate_futures']:
        s=r[ex]
        if not s: continue
        if ex=='hyperliquid_futures' and s.startswith('HYNA:'): s=s[5:]
        tasks.append((r['asset'],r['class'],ex,s))
def run(t):
    a,cl,ex,s=t; out={'asset':a,'class':cl,'ex':ex,'sym':s,'avail':{},'sz':{}}
    if ex=='bybit_futures':
        for d in DAYS:
            c,n=bybit(s,d) if d in SZD else (req(f'https://quote-saver.bycsi.com/orderbook/linear/{s}/{d}_{s}_ob200.data.zip',m='HEAD')[0],0)
            out['avail'][d]=c==200
            if d in SZD: out['sz'][d]=n
    elif ex=='gate_futures':
        for d in DAYS:
            c,n=gate(s,d,'12'); out['avail'][d]=c==200
            if d in SZD: out['sz'][d+'T12']=n
    else:
        for d in DAYS:
            if d in SZD: c,n=chft(ex,s,d,'12'); out['sz'][d+'T12']=n
            else: c,n=req(f'{A}/v1/download?file={ex}/{d}/12/{s}_orderbook.parquet',{'X-API-Key':K})[0],0
            out['avail'][d]=c==302
    return out
wl=threading.Lock()
with cf.ThreadPoolExecutor(24) as p, open(sys.argv[2],'w') as f:
    for o in p.map(run,tasks):
        f.write(json.dumps(o,ensure_ascii=False)+chr(10)); f.flush()
open(sys.argv[2]+'.done','w').write(str(len(tasks)))
