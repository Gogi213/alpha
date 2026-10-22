import csv, math, random, collections, sys
random.seed(7)
for mode in ("fut","spot"):
    rows=list(csv.DictReader(open(f"docs/findings/tk327-features-{mode}-2026-10-20.csv")))
    x=[float(r["net_bps"]) for r in rows]; n=len(x); m=sum(x)/n
    s=(sum((v-m)**2 for v in x)/(n-1))**.5
    notional=[float(r["entry_px"])*float(r["qty"]) for r in rows]
    d=collections.defaultdict(lambda:[0.0,0.0,0])
    for r,nt,v in zip(rows,notional,x):
        k=r["day"]; d[k][0]+=v*nt/1e4; d[k][1]+=nt; d[k][2]+=1
    days=sorted(d); D=len(days)
    pnl=[d[k][0] for k in days]; nts=[d[k][1] for k in days]; cnt=[d[k][2] for k in days]
    tot=sum(pnl); totn=sum(nts)
    # ratio estimator: bps on notional, block bootstrap by day
    def ratio(ix): return 1e4*sum(pnl[i] for i in ix)/sum(nts[i] for i in ix)
    pt=ratio(range(D))
    bs=[ratio([random.randrange(D) for _ in range(D)]) for _ in range(2000)]
    bm=sum(bs)/len(bs); se=(sum((b-bm)**2 for b in bs)/(len(bs)-1))**.5
    # naive SE per trade
    se_naive=s/math.sqrt(n)
    # design effect
    deff=(se/se_naive)**2
    # by month ratio and day-boot SE
    mons=collections.defaultdict(list)
    for i,k in enumerate(days): mons[k[:7]].append(i)
    print(f"== {mode}: trades {n} days_with_trades {D} mean_bps {m:.2f} sd_trade {s:.1f} SE_naive {se_naive:.2f}")
    print(f"   notional-weighted {pt:.2f} bps; day-block bootstrap SE {se:.2f} bps; deff {deff:.2f}; eff N trades {n/deff:.0f}")
    print(f"   daily $: mean {tot/D:.2f} sd {(sum((p-tot/D)**2 for p in pnl)/(D-1))**.5:.2f} t_day {tot/D/(((sum((p-tot/D)**2 for p in pnl)/(D-1))**.5)/math.sqrt(D)):.2f}")
    for mo,ix in sorted(mons.items()):
        bsm=[ratio([random.choice(ix) for _ in ix]) for _ in range(1000)]
        a=sum(bsm)/len(bsm); sm=(sum((b-a)**2 for b in bsm)/(len(bsm)-1))**.5
        print(f"   {mo}: days {len(ix)} trades {sum(cnt[i] for i in ix)} bps {ratio(ix):.1f} SE_day-boot {sm:.1f}")
    # MDE for exam of K months: SE scales 1/sqrt(days)
    for dd in (60,90,180):
        sed=se*math.sqrt(D/dd)
        print(f"   exam {dd} days: SE {sed:.1f} bps; MDE(80% pow, 1-sided a=.05)=2.49*SE={2.49*sed:.1f} bps")
