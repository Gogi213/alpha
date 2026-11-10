#!/usr/bin/python3
# TK-522 (E-021): сверка L1 суток «было» (кэш /data/p40l1c/p40l1) и «стало» (carry+посев, /data/p40l1-carry): по часам — первое состояние, спред на старте, число состояний.
# usage: cmp-carry522.py <ex>/<sym>/<day>
import sys,struct,subprocess
sys.path.insert(0,"/root")
OLD="/data/p40l1c/p40l1"; NEW="/data/p40l1-carry"
def dec(p):
    b=subprocess.run(["zstd","-dc",p],capture_output=True).stdout
    return [struct.unpack_from("<qqddB",b,i*33) for i in range(len(b)//33)]
def states(rows):
    bid=ask=None; out=[]; prev=None
    for et,fu,px,qt,fl in rows:
        k=(et,fu)
        if prev is not None and k!=prev: out.append((prev[0],bid,ask))
        prev=k
        if qt>0:
            if fl&2: bid=px
            else: ask=px
    if prev is not None: out.append((prev[0],bid,ask))
    return out
def spr(b,a): return None if b is None or a is None or b<=0 else (a-b)/((a+b)/2)*1e4
key=sys.argv[1]
for h in range(24):
    r=[]
    for root in (OLD,NEW):
        try: r.append(states(dec(f"{root}/{key}/{h:02d}.l1.bin.zst")))
        except Exception as e: r.append([])
    o,n=r
    if not o or not n: print(h,"нет файла",len(o),len(n)); continue
    f=lambda s:(s[0][0]%3600000,round(spr(s[0][1],s[0][2]) or -1,1),s[0][1],s[0][2])
    same=o==n
    print(f"{h:02d} {'=' if same else 'РАЗНО'} было сост {len(o)} старт(мс от часа, спред bps, bid, ask) {f(o)} | стало сост {len(n)} {f(n)}")
