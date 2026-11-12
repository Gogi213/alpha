# TK-375: stiki sutok po id (idmin(d+1) == idmax(d)+1) po log/dl.tsv; na calc posle tk375-summary.py (chitaet /tmp/tk375-units.csv, schitaet neg-monety)
import csv, collections
root="/srv/alpha-sda/tk372-gate"
dl={}
for l in open(root+"/log/dl.tsv"):
    p=l.rstrip("\n").split("\t"); dl[(p[0],p[1],p[2])]=p
rows=list(csv.DictReader(open("/tmp/tk375-units.csv")))
neg=collections.Counter(); 
for r in rows:
    if r["market"]=="fut" and r["trades_vs_candles_rel"] and float(r["trades_vs_candles_rel"])<-0.001: neg[r["symbol"]]+=1
print("neg coins", dict(neg))
D=["2026-10-15","2026-10-16","2026-10-17","2026-10-18","2026-10-19","2026-10-20","2026-10-21"]
tot=collections.Counter(); bad=collections.defaultdict(list)
syms={(r["market"],r["symbol"]) for r in rows}
for mk,s in sorted(syms):
    for a,b in zip(D,D[1:]):
        x=dl.get((mk,s,a)); y=dl.get((mk,s,b))
        if not x or not y or x[3]!="ok" or y[3]!="ok" or not x[7] or not y[6]: continue
        d=int(y[6])-int(x[7])-1; tot[mk]+=1
        if d!=0: bad[mk].append((d,s,a))
for mk in ("fut","spot"):
    print(mk,"seams",tot[mk],"nonzero",len(bad[mk]),"sum",sum(abs(d) for d,_,_ in bad[mk]),sorted(bad[mk],key=lambda t:-abs(t[0]))[:6])
