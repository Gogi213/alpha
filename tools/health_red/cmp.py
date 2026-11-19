import json,statistics as st
D=json.load(open("/tmp/minfeat.json"))
rows=list(D.values())
def avg(l): return sum(l)/len(l) if l else float("nan")
def feats(r):
    nw=r["nw"]
    g=nw.get("gateway",[0,0]);ne=nw.get("neutral",[0,0])
    return {"steal_avg":r.get("steal",[None,None])[0],"steal_max":r.get("steal",[None,None])[1],
     "cpu":(r["user"][0]+r["nice"][0]+r["sys"][0]) if "user" in r else None,
     "runq":r.get("runq",[None])[0],"await_max":r.get("await",[None,None])[1],"util_max":r.get("util",[None,None])[1],
     "wMB":r.get("wkB",[0])[0]/1024 if "wkB" in r else None,"retr_max":r.get("retr",[None,None])[1],
     "gw_loss":g[1],"neu_loss":ne[1],"gw_n":g[0],"iow_max":r.get("iow",[None,None])[1]}
def stats(name,sel):
    fs=[feats(r) for r in rows if sel(r)]
    print(f"{name:34s} n={len(fs):4d} "+" ".join(f"{k}={avg([f[k] for f in fs if f[k] is not None]):.2f}" for k in ["steal_avg","steal_max","cpu","runq","iow_max","util_max","wMB","retr_max","gw_loss","neu_loss"]))
isbad=lambda ex: (lambda r: r[ex] is not None and (r[ex][0]>0 or r[ex][1]>0))
for ex in ["binance","gate","bybit","okx","bingx"]:
    stats(f"{ex} bad min",isbad(ex))
anyc=lambda r: any(r[e] is not None and (r[e][0]>0 or r[e][1]>0) for e in ["binance","gate","bybit","okx","bingx"])
stats("any exch bad",anyc)
stats("all exch good",lambda r: not anyc(r))
stats("bn+gate both bad",lambda r: isbad("binance")(r) and isbad("gate")(r))
stats("bn bad, gate good",lambda r: isbad("binance")(r) and not isbad("gate")(r))
stats("gate bad, bn good",lambda r: isbad("gate")(r) and not isbad("binance")(r))
stats("bybit lost>0",lambda r: r["bybit"] is not None and r["bybit"][0]>0)
stats("bybit lost==0",lambda r: r["bybit"] is not None and r["bybit"][0]==0)
# counts
for ex in ["binance","gate","bybit","okx","bingx"]:
    lm=[r for r in rows if r[ex] and r[ex][0]>0]; lt=[r for r in rows if r[ex] and r[ex][1]>0]
    print(ex,"minutes lost>0:",len(lm),"sum_lost_s:",round(sum(r[ex][0] for r in lm)/1000,1),"| minutes late>0:",len(lt),"sum_late:",sum(r[ex][1] for r in lt), "| to:",sum(r[ex][2] for r in rows if r[ex]))
