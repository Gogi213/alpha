import subprocess,glob,collections,sys
D0=1794700800
eps=[("14:14:20",210),("15:28:00",140),("18:08:50",270),("18:44:50",230),("19:54:00",270),("20:11:10",130),("21:28:20",120)]
def sec(h): a,b,c=map(int,h.split(":")); return D0+a*3600+b*60+c
W=[]
for h,d in eps:
    s=sec(h); W.append(("stall "+h,(s-30)*1000,(s+d)*1000)); W.append(("ctrl  "+h+"-1h",(s-3600-30)*1000,(s-3600+d)*1000))
ids={w[0]:collections.defaultdict(list) for w in W}
lates={w[0]:0 for w in W}; cnt={w[0]:0 for w in W}
for f in sorted(glob.glob(sys.argv[1]+"/trade*-*.tsv.zst")):
    p=subprocess.Popen(["zstdcat",f],stdout=subprocess.PIPE,text=True,bufsize=1<<20)
    rx=0; last={}
    for ln in p.stdout:
        c=ln.split("\t")
        try: rx+=int(c[1])
        except: continue
        if c[0]!="t": continue
        sym=c[4]; da=int(c[5])
        a=da if sym not in last else last[sym]+1+da
        last[sym]=a
        E=rx//1000000-int(c[2])
        for name,lo,hi in W:
            if lo<=E<hi:
                ids[name][sym].append(a); cnt[name]+=1
                if int(c[2])>2000: lates[name]+=1
    p.wait()
for name,lo,hi in W:
    miss=0; dup=0
    for sym,v in ids[name].items():
        v.sort()
        for x,y in zip(v,v[1:]):
            if y==x: dup+=1
            elif y>x+1: miss+=y-x-1
    print(f"{name:22s} trades {cnt[name]:7d} late>2s {lates[name]:6d} missing_ids {miss:5d} ({100*miss/max(1,cnt[name]+miss):.3f}%) dups {dup}")
