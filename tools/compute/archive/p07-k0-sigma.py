# k0 = 4,5 bps / медиана σ240 (√Σr² 1-мин лог-доходностей закрытий, 240 закрытых минут до arm) на сигналах базы Г-85а в августе, без исходов (Судья v131, «Окончательно»)
import csv,glob,math,os,statistics as st,bisect
A=os.path.expanduser("~/alpha/epochs/e-aug")
K={}
def kl(sym):
    if sym not in K:
        f=f"{A}/study/klines/ref-{sym}-1m.csv"
        if not os.path.exists(f): K[sym]=None; return None
        t=[];c=[]
        for r in csv.DictReader(open(f)):
            t.append(int(r["minute_ms"])); c.append(float(r["close"]))
        K[sym]=(t,c)
    return K[sym]
sig=[];nos=0;n=0;seen=set()
for f in sorted(glob.glob(f"{A}/b5/p07a-base/2026-08-*/t-bid-btc4h-q1/signals.csv")):
    rows=[l for l in open(f) if not l.startswith("#")]
    rd=csv.DictReader(rows)
    for r in rd:
        sym=r.get("symbol") or r.get("sym")
        if sym=="TRXUSDT": continue
        key=(sym,r["day_utc"],r["signal_index"])
        if key in seen: continue
        seen.add(key)
        t0=int(r.get("t0_ns") or r.get("arm_ns"))//1_000_000
        n+=1
        k=kl(sym)
        if not k: nos+=1; continue
        t,c=k
        j=bisect.bisect_left(t,(t0//60000)*60000)  # минуты, закрытые до arm: open_time+60s <= arm
        # минута с open_time m закрыта в m+60000; берём все m с m+60000 <= t0
        j=bisect.bisect_right(t,t0-60000)
        lo=max(0,j-241); seg=c[lo:j]
        seg_t=t[lo:j]
        rr=[math.log(seg[i]/seg[i-1]) for i in range(1,len(seg)) if seg_t[i]-seg_t[i-1]==60000 and seg[i]>0 and seg[i-1]>0]
        if len(rr)<200: nos+=1; continue
        sig.append(1e4*math.sqrt(sum(x*x for x in rr[-240:])))
m=st.median(sig)
print(f"сигналов {n}, с σ {len(sig)}, без σ {nos}; σ240 bps: q25 {st.quantiles(sig,n=4)[0]:.1f} медиана {m:.1f} q75 {st.quantiles(sig,n=4)[2]:.1f}; k0 = 4.5/{m:.2f} = {4.5/m:.4f}")
