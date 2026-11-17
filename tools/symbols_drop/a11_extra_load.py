import json,statistics
import os
P=os.environ.get("TK584_DIR","/tmp/tk584")
R=json.load(open(P+"/rest-now.json"))
dirs={"binance":"collect/binance","bingx":"collect/bingx","gate":"collect/gate","okx":"collect/okx","bybit":"turbo-probe/soak-bybit"}
def tradable(ex,v):
    if ex=="binance": return v["status"]=="TRADING" and v["ctype"]=="PERPETUAL" and v["quote"]=="USDT"
    if ex=="bybit": return v["status"]=="Trading" and v["ctype"]=="LinearPerpetual" and v["quote"]=="USDT"
    if ex=="okx": return v["status"]=="live" and v["ctype"]=="linear" and v["quote"]=="USDT"
    if ex=="bingx": return v["status"]==1 and v["quote"]=="USDT"
    if ex=="gate": return v["status"]=="trading"
for ex,dr in dirs.items():
    st=json.load(open(f"{P}/{dr}/data/pool-state.json")); rates=st["rates"]
    sub=set(st["last_above"]); tr={s for s,v in R[ex].items() if tradable(ex,v)}
    low={s for s in sub if (R[ex].get(s,{}).get("qv") or 0)<300000}
    unsub=tr-sub
    def lower(s): return s.lower()
    tot=lambda syms: sum(sum(rates[k].get(lower(s),0) for k in rates) for s in syms)
    allsub=tot(sub)
    per_low=tot(low)/len(low) if low else None
    per_all=allsub/len(sub)
    q_low=statistics.median([R[ex][s]["qv"] for s in low if R[ex].get(s,{}).get("qv") is not None]) if low else None
    q_un=statistics.median([R[ex][s]["qv"] for s in unsub if R[ex][s]["qv"] is not None]) if unsub else None
    print(f"{ex}: subscribed {len(sub)} total frames/s {allsub:.0f} (mean {per_all:.1f}/symbol); held-below-300k {len(low)} sym, mean {per_low if per_low is None else round(per_low,1)} fr/s each (median qv {q_low if q_low is None else round(q_low)}); to add {len(unsub)} sym, median qv {q_un if q_un is None else round(q_un)} -> model +{(per_low or 0)*len(unsub):.0f} fr/s upper (+{(per_low or 0)*len(unsub)/allsub*100:.0f}%)")
