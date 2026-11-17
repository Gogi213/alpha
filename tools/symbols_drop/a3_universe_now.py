import json,datetime
import os
P=os.environ.get("TK584_DIR","/tmp/tk584")
R=json.load(open(P+"/rest-now.json"))
dirs={"binance":"collect/binance","bingx":"collect/bingx","gate":"collect/gate","okx":"collect/okx","bybit":"turbo-probe/soak-bybit"}
def tradable(ex,s,v):
    if ex=="binance": return v["status"]=="TRADING" and v["ctype"]=="PERPETUAL" and v["quote"]=="USDT"
    if ex=="bybit": return v["status"]=="Trading" and v["ctype"]=="LinearPerpetual" and v["quote"]=="USDT"
    if ex=="okx": return v["status"]=="live" and v["ctype"]=="linear" and v["quote"]=="USDT"
    if ex=="bingx": return v["status"]==1 and v["quote"]=="USDT"
    if ex=="gate": return v["status"]=="trading"
D=json.load(open(P+"/runsym.json"))
for ex,dr in dirs.items():
    st=json.load(open(f"{P}/{dr}/data/pool-state.json"))
    sub=set(st["last_above"])
    tr={s for s,v in R[ex].items() if tradable(ex,s,v) and v["qv"] is not None}
    tr_noticker={s for s,v in R[ex].items() if tradable(ex,s,v) and v["qv"] is None}
    ge=lambda t:{s for s in tr if R[ex][s]["qv"]>=t}
    miss=tr-sub
    print(ex,"tradable(with ticker)",len(tr),"tradable w/o ticker",len(tr_noticker),"subscribed(pool-state)",len(sub),"tradable not subscribed",len(miss),
          "of them qv>=300k:",len(miss&ge(300000)),"qv<300k:",len(miss-ge(300000)),"subscribed not tradable:",len(sub-tr), "tradable>=300k:",len(ge(300000)))
    print("   top missing qv:",sorted(((round(R[ex][s]["qv"]),s) for s in miss),reverse=True)[:5])
