import sys,struct,zstandard,collections
f=open(sys.argv[1],'rb'); h=f.read(25); tick=struct.unpack('<q',h[5:13])[0]; mx=struct.unpack('<I',h[21:25])[0]
d=zstandard.ZstdDecompressor()
BID=1|(1<<29)|(1<<30); ASK=1|(1<<28)|(1<<30); BIDS=4|(1<<29)|(1<<30); ASKS=4|(1<<28)|(1<<30)
BUY=2|(1<<29)|(1<<30); SELL=2|(1<<28)|(1<<30)
def uv(b,i):
    r=0;s=0
    while True:
        x=b[i];i+=1;r|=(x&127)<<s;s+=7
        if x<128:return r,i
def zz(b,i):
    r,i=uv(b,i);return (r>>1)^-(r&1),i
msgs=[]  # (exch_ns, kind, entries) kind: 'd' depth(side,snap) / 't'
while True:
    l=f.read(4)
    if len(l)<4:break
    n=struct.unpack('<I',l)[0]; b=d.decompress(f.read(n),max_output_size=1<<28)
    ep=struct.unpack('<q',b[:8])[0];i=8;p=q=0
    while i<len(b):
        ev,i=uv(b,i);ed,i=zz(b,i);ld,i=zz(b,i);at,i=uv(b,i);c,i=uv(b,i)
        ents=[]
        for _ in range(c):
            pd,i=zz(b,i);qd,i=zz(b,i);p+=pd;q+=qd;ents.append((p,q))
        msgs.append((ep+ed,ev,at,ents))
print('msgs',len(msgs),'tick',tick,'sample',msgs[0][:3],msgs[1000][:3] if len(msgs)>1000 else '')
book={BID:{},ASK:{}};held=set();pend=collections.defaultdict(list);viol=[];tot=0;inspan=0;rpi=0;block=0
delays=[]
def span():
    b=book[BID];a=book[ASK]
    return (min(b),max(a)) if b and a else None
for ts,ev,at,ents in msgs:
    if ev in (BID,ASK,BIDS,ASKS):
        side=BID if ev in (BID,BIDS) else ASK
        bk=book[side]
        if ev in (BIDS,ASKS): bk.clear()
        for p,q in ents:
            if q<=0: bk.pop(p,None)
            else:
                bk[p]=q
                if p not in held:
                    held.add(p)
                    if p in pend:
                        for v in pend.pop(p): delays.append((ts-v[0])/1e6)
    elif ev in (BUY,SELL):
        for p,q in ents:
            tot+=1
            if p in held: continue
            sp=span()
            if sp is None or not(sp[0]<=p<=sp[1]): continue
            inspan+=1; r=bool(at&2); rpi+=r; block+=bool(at&1)
            v=(ts,p,ev==BUY,r); viol.append(v); pend[p].append(v)
never=sum(len(x) for x in pend.values())
print('trades',tot,'violations',len(viol),'ppm',round(1e6*len(viol)/tot),'rpi',rpi,'block',block)
import bisect
delays.sort(); n=len(viol)
def cnt(lo,hi): return bisect.bisect_left(delays,hi)-bisect.bisect_left(delays,lo)
print('later-appear <0ms',cnt(-1e18,0),' 0-100',cnt(0,100),' 100-300',cnt(100,300),' 300-1000',cnt(300,1000),' 1s-60s',cnt(1000,60000),' >60s',cnt(60000,1e18),' never',never)
