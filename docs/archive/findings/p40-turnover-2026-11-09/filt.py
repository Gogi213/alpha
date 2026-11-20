import csv,collections
R=[r for r in csv.DictReader(open("out/contract_median.csv",encoding="utf-8")) if r["in_set"]=="1"]
TH={"binance_futures":7e5,"bybit_futures":5e5}
def keep(r):
    t=TH.get(r["ex"],0); m=r["median_usd"]
    return float(m)>=t if m!="" else t==0
K=[r for r in R if keep(r)]
A=collections.defaultdict(list)
for r in K: A[r["asset"]].append(r)
cls={r["asset"]:r["class"] for r in R}
A2={a:v for a,v in A.items() if len(v)>=2}
def agg(A_,lab):
    c=[r for v in A_.values() for r in v]
    nd=[r for r in c if r["downloaded"]=="0"]
    print(lab,"активов",len(A_),"контрактов",len(c),"скачано",len(c)-len(nd),"к докачке",len(nd),"ГБ",round(sum(float(r["gb"]) for r in nd),1))
    byc=collections.Counter(cls[a] for a in A_); print("  классы",dict(byc))
    byx=collections.defaultdict(lambda:[0,0.0])
    for r in nd: byx[r["ex"]][0]+=1; byx[r["ex"]][1]+=float(r["gb"])
    print("  докачка по биржам",{k:(v[0],round(v[1])) for k,v in byx.items()})
print("всего in_set контрактов",len(R),"активов",len({r["asset"] for r in R}))
print("после порога: контрактов",len(K),"активов с >=1:",len(A))
agg(A,"A(>=1 контракт)"); agg(A2,"B(>=2 бирж)")
# выпавшие
print("порог срезал: binance",sum(1 for r in R if r["ex"]=="binance_futures" and not keep(r)),"bybit",sum(1 for r in R if r["ex"]=="bybit_futures" and not keep(r)))
# сколько ранее скачанных активов выпало из B
dl={r["asset"] for r in R if r["downloaded"]=="1"}
print("активов с уже скачанным контрактом:",len(dl),"из них в B:",len(dl&set(A2)))
