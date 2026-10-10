import asyncio,json,sys,time,collections,websockets
SYM=sys.argv[1]; DUR=int(sys.argv[2])
class Book:
    def __init__(s): s.b={};s.a={};s.held=set();s.t=0
    def apply(s,m):
        d=m['data']; sn=m['type']=='snapshot'
        if sn: s.b.clear(); s.a.clear()
        for side,k in((s.b,'b'),(s.a,'a')):
            for p,q in d[k]:
                if float(q)==0: side.pop(p,None)
                else: side[p]=q
        for p in list(s.b)+list(s.a): s.held.add(p)
        s.t=m['ts']
    def span(s):
        if not s.b or not s.a: return None
        return min(map(float,s.b)),max(map(float,s.a))
books={'orderbook.200':Book(),'orderbook.50':Book(),'orderbook.1':Book()}
trades=[]; pend=[]; ts_log=collections.Counter()
res=collections.Counter()
async def main():
    async with websockets.connect('wss://stream.bybit.com/v5/public/linear',max_size=None) as ws:
        await ws.send(json.dumps({'op':'subscribe','args':[f'orderbook.200.{SYM}',f'orderbook.50.{SYM}',f'orderbook.1.{SYM}',f'publicTrade.{SYM}']}))
        t0=time.time()
        while time.time()-t0<DUR:
            m=json.loads(await asyncio.wait_for(ws.recv(),30))
            tp=m.get('topic','')
            if tp.startswith('orderbook.'):
                name=tp.rsplit('.',1)[0]; bk=books[name]; bk.apply(m); ts_log[name]+=1
                for e in list(pend):
                    if name=='orderbook.200' and e['p'] in bk.held:
                        res['ob200_viol_later_300ms']+=1; pend.remove(e)
                    elif m['ts']-e['ts']>300:
                        res['ob200_viol_never_300ms']+=1; pend.remove(e)
            elif tp.startswith('publicTrade'):
                for t in m['data']:
                    p=t['p']; pf=float(p); res['trades']+=1
                    o=books['orderbook.200']; sp=o.span()
                    if sp is None or not(sp[0]<=pf<=sp[1]): continue
                    res['in_span200']+=1
                    if p in o.held: continue
                    res['viol200']+=1
                    if p in books['orderbook.50'].held: res['viol200_seen_in_ob50']+=1
                    if p in books['orderbook.1'].held: res['viol200_seen_in_ob1']+=1
                    if t.get('RPI'): res['viol200_rpi']+=1
                    pend.append({'p':p,'ts':t['T']})
                    s50=books['orderbook.50'].span()
                    res['viol200_in_span50']+= int(bool(s50 and s50[0]<=pf<=s50[1]))
                o50=books['orderbook.50']; sp=o50.span()
                for t in m['data']:
                    pf=float(t['p'])
                    if sp and sp[0]<=pf<=sp[1]:
                        res['in_span50']+=1
                        if t['p'] not in o50.held: res['viol50']+=1
asyncio.run(main())
print(SYM,DUR,'s msgs',dict(ts_log)); print(dict(res))
r=res
if r['in_span200']: print('ppm200',round(1e6*r['viol200']/r['in_span200']),'ppm50',round(1e6*r['viol50']/max(r['in_span50'],1)))
