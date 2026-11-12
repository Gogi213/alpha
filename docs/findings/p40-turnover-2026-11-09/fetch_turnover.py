# Дневной оборот USD по контрактам за 17.09–22.10.2026 по дневным свечам REST. Запуск на srv: python3 -I fetch_turnover.py contracts.csv out_dir
import csv,json,sys,time,threading,urllib.request,urllib.parse,datetime as dt,collections
S=int(dt.datetime(2026,9,17,tzinfo=dt.timezone.utc).timestamp()*1000); E=int(dt.datetime(2026,10,23,tzinfo=dt.timezone.utc).timestamp()*1000)-1
DAY=86400000
def get(u,data=None):
    last=None
    for i in range(6):
        try:
            r=urllib.request.Request(u,data=data,headers={'User-Agent':'Mozilla/5.0','Content-Type':'application/json'})
            return json.load(urllib.request.urlopen(r,timeout=40))
        except urllib.error.HTTPError as e:
            last=f'HTTP{e.code}'
            if e.code in(400,404): break
            time.sleep(2+3*i)
        except Exception as e: last=str(e)[:80]; time.sleep(2+3*i)
    raise RuntimeError(last)
def day(ts): return dt.datetime.fromtimestamp(ts/1000,dt.timezone.utc).strftime('%Y-%m-%d')
def q(**k): return urllib.parse.urlencode(k)
def binlike(host):
    def f(s):
        j=get(f'{host}/fapi/v1/klines?'+q(symbol=s,interval='1d',startTime=S,endTime=E,limit=60))
        return [(int(x[0]),float(x[7])) for x in j]
    return f
def bybit(s):
    j=get('https://api.bybit.com/v5/market/kline?'+q(category='linear',symbol=s,interval='D',start=S,end=E,limit=200))
    if j['retCode']!=0: raise RuntimeError(j['retMsg'])
    return [(int(x[0]),float(x[6])) for x in j['result']['list']]
def okx(s):
    j=get('https://www.okx.com/api/v5/market/history-candles?'+q(instId=s,bar='1Dutc',after=E+1,before=S-1,limit=100))
    if j['code']!='0': raise RuntimeError(j['msg'])
    return [(int(x[0]),float(x[7])) for x in j['data']]
def bitget(s):
    j=get('https://api.bitget.com/api/v2/mix/market/candles?'+q(symbol=s,productType='USDT-FUTURES',granularity='1Dutc',startTime=S,endTime=E,limit=200))
    if j['code']!='00000': raise RuntimeError(j['msg'])
    return [(int(x[0]),float(x[6])) for x in j['data']]
def gate(s):
    j=get('https://api.gateio.ws/api/v4/futures/usdt/candlesticks?'+q(contract=s,interval='1d',**{'from':S//1000,'to':E//1000}))
    return [(int(x['t'])*1000,float(x['sum'])) for x in j]
def hl(s):
    c=s.lower().split(':')[0]+':'+s.split(':',1)[1] if ':' in s else s
    j=get('https://api.hyperliquid.xyz/info',json.dumps({'type':'candleSnapshot','req':{'coin':c,'interval':'1d','startTime':S,'endTime':E}}).encode())
    return [(int(x['t']),float(x['v'])*(float(x['h'])+float(x['l'])+float(x['c']))/3) for x in j]
F={'binance_futures':(binlike('https://fapi.binance.com'),0.15),'aster_futures':(binlike('https://fapi.asterdex.com'),0.15),'bybit_futures':(bybit,0.05),
   'okx_futures':(okx,0.3),'bitget_futures':(bitget,0.1),'gate_futures':(gate,0.12),'hyperliquid_futures':(hl,1.2)}
rows=list(csv.DictReader(open(sys.argv[1],encoding='utf-8'))); out=sys.argv[2]
by=collections.defaultdict(list)
for r in rows: by[r['ex']].append(r)
lock=threading.Lock(); fo=open(out+'/turnover.csv','w',newline='',encoding='utf-8'); wo=csv.writer(fo); wo.writerow(['asset','ex','sym','date','turnover_usd'])
fs=open(out+'/status.csv','w',newline='',encoding='utf-8'); ws=csv.writer(fs); ws.writerow(['ex','sym','n_days','err'])
def run(ex):
    f,pause=F[ex]
    for r in by[ex]:
        try:
            c=f(r['sym']); err=''
        except Exception as e: c=[]; err=str(e)
        c=sorted({day(t):v for t,v in c if S<=t<=E}.items())
        with lock:
            for d,v in c: wo.writerow([r['asset'],ex,r['sym'],d,round(v,2)])
            ws.writerow([ex,r['sym'],len(c),err]); fo.flush(); fs.flush()
        time.sleep(pause)
th=[threading.Thread(target=run,args=(ex,)) for ex in by]
[t.start() for t in th]; [t.join() for t in th]
open(out+'/DONE','w').write('ok')
