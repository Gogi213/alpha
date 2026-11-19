# TK-619: риск баз TK-599 (L c53a116125b39, S c10fbba7be522) по склейке окон как retitr_full.splice; только чтение out/. Запуск на srv: python3 tk619_audit.py
import csv,datetime as D,collections
STOCKS=set(open("/tmp/aud_tradifi.txt").read().split())  # TRADIFI_PERPETUAL (fapi exchangeInfo) ∩ symbols.txt = 108
W=[("wa","2026-06-17","2026-07-01","2026-08-25"),("wb","2026-07-25","2026-08-26","2026-10-02"),("wc","2026-09-01","2026-10-03","2026-11-13")]
VID={"long":"c53a116125b39","short":"c10fbba7be522"}
d=lambda s:D.date.fromisoformat(s)
allr={}
for side,v in VID.items():
  rows=[]
  for n,t0,lo,hi in W:
    t0d=d(t0)
    for r in csv.DictReader(open(f"/data/p42-tk599/out/{n}/{v}/{side}-trades.csv")):
      day=t0d+D.timedelta(days=int(r["entry_ms"])//86400000)
      if d(lo)<=day<=d(hi):
        r["date"]=day; r["sv"]=float(r["gross"])-float(r["fees"]); r["side"]=side
        # abs ms: entry_ms relative to T0 midnight
        base=int(D.datetime(t0d.year,t0d.month,t0d.day,tzinfo=D.timezone.utc).timestamp()*1000)
        r["e"]=base+int(r["entry_ms"]); r["x"]=base+int(r["exit_ms"]); rows.append(r)
  rows.sort(key=lambda r:r["e"]); allr[side]=rows
  n=len(rows); S=sum(r["sv"] for r in rows)
  fe=sum(float(r["fees"]) for r in rows); gr=sum(float(r["gross"]) for r in rows)
  # consecutive losers by exit order
  ro=sorted(rows,key=lambda r:r["x"]); mx=c=0; lossrun=0;worst=0
  for r in ro:
    if r["sv"]<0: c+=1; lossrun+=r["sv"]; mx=max(mx,c); worst=min(worst,lossrun)
    else: c=0; lossrun=0
  eq=pk=dd=0; ddst=None
  for r in ro:
    eq+=r["sv"]; pk=max(pk,eq); dd=min(dd,eq-pk)
  # longest time under water (days)
  eq=pk=0; pkt=ro[0]["x"]; uw=0
  for r in ro:
    eq+=r["sv"]
    if eq>=pk: pk=eq; pkt=r["x"]
    else: uw=max(uw,(r["x"]-pkt)/864e5)
  ev=sorted([(r["e"],1) for r in rows]+[(r["x"],-1) for r in rows],key=lambda t:(t[0],t[1]))
  cur=mc=0
  for t,k in ev: cur+=k; mc=max(mc,cur)
  st=[r for r in rows if r["kind"]=="stop"]
  stx=sorted(r["x"] for r in st); m24=max(sum(1 for y in stx if x<=y<x+864e5) for x in stx) if stx else 0
  hold=[ (r["x"]-r["e"])/6e4 for r in rows]
  win=[r["sv"] for r in rows if r["sv"]>0]; los=[r["sv"] for r in rows if r["sv"]<0]
  dly=collections.defaultdict(float)
  for r in rows: dly[r["date"]]+=r["sv"]
  mon=collections.defaultdict(float)
  for r in rows: mon[r["date"].month]+=r["sv"]
  print(side,"n",n,"S",round(S,2),"gross",round(gr,2),"fees",round(fe,2),"gross_bp/trade",round(gr/n/500*1e4,2),"net_bp/trade",round(S/n/500*1e4,2),"fee_bp/trade",round(fe/n/500*1e4,2))
  print(" winrate",round(len(win)/n,3),"avg win",round(sum(win)/len(win),2),"avg loss",round(sum(los)/len(los),2),"stops",len(st),"stop avg",round(sum(r['sv'] for r in st)/max(1,len(st)),2),"max stops in 24h",m24)
  print(" max consec losers",mx,"worst loss run $",round(worst,2),"maxDD $",round(dd,2),"DD % of 500",round(-dd/5,1),"max underwater days",round(uw,1),"max concurrent",mc,"max hold min",round(max(hold)),"worst day",round(min(dly.values()),2),"best day",round(max(dly.values()),2),"days",len(dly))
  print(" month",{k:round(v,1) for k,v in sorted(mon.items())})
  # end-of-window truncation: trades whose exit day > hi of window? count kind
  print(" kinds",collections.Counter(r["kind"] for r in rows))
# combined concurrency
rows=allr["long"]+allr["short"]
ev=sorted([(r["e"],1) for r in rows]+[(r["x"],-1) for r in rows],key=lambda t:(t[0],t[1]))
cur=mc=0
for t,k in ev: cur+=k; mc=max(mc,cur)
ro=sorted(rows,key=lambda r:r["x"]); eq=pk=dd=0
for r in ro: eq+=r["sv"]; pk=max(pk,eq); dd=min(dd,eq-pk)
print("both: max concurrent",mc,"notional $",mc*500,"S",round(eq,2),"maxDD $",round(dd,2))
# same-coin overlapping long+short?
# extra: per-trade risk, stop hold, forced/truncation, distance-to-level of stops, stocks share
for side in ("long","short"):
  rows=allr[side]
  stk=[r for r in rows if r["sym"] in STOCKS]
  print(side,"stock-perp trades",len(stk),"S",round(sum(r['sv'] for r in stk),2),"of",round(sum(r['sv'] for r in rows),2))
  # loss at 2x and 3 concurrent stops
  # same-coin both sides overlap
lg={};ov=0
for r in allr["long"]: lg.setdefault(r["sym"],[]).append((r["e"],r["x"]))
for r in allr["short"]:
  for e,x in lg.get(r["sym"],[]):
    if r["e"]<x and e<r["x"]: ov+=1
print("same-coin long/short overlap",ov)
