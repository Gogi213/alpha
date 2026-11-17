import json,pickle,datetime,csv,glob
import os
P=os.environ.get("TK584_DIR","/tmp/tk584")
D=json.load(open(P+"/runsym.json")); res=pickle.load(open(P+"/a7.pkl","rb")); R=json.load(open(P+"/rest-now.json"))
PD=json.load(open(P+"/pooldiff.json"))
dirs={"binance":"collect/binance","bingx":"collect/bingx","gate":"collect/gate","okx":"collect/okx","bybit":"turbo-probe/soak-bybit"}
def ts(ms): return datetime.datetime.fromtimestamp(ms/1000,datetime.UTC).strftime("%Y-%m-%d %H:%M")
def tradable(ex,v):
    if ex=="bybit": return v["status"]=="Trading" and v["ctype"]=="LinearPerpetual" and v["quote"]=="USDT"
    if ex=="okx": return v["status"]=="live" and v["ctype"]=="linear" and v["quote"]=="USDT"
    if ex=="bingx": return v["status"]==1 and v["quote"]=="USDT"
    if ex=="gate": return v["status"]=="trading"
rows=[]
for (ex,S),det in sorted(res.items(),key=lambda x:(x[0][0],x[0][1])):
    runs=D[ex]; ids=sorted(runs,key=int)
    st=json.load(open(f"{P}/{dirs[ex]}/data/pool-state.json"))["last_above"]
    for (s,e,backt,last_any,tr,qvnow,inpool) in det:
        ret=None
        for rid in ids:
            if int(rid)>=S-1 and s in runs[rid]:
                f=runs[rid][s][1]
                if f>S*1000+60000: ret=f if ret is None else min(ret,f)
        prev=ids[ids.index(str(S))-1]
        lastbefore=runs[prev][s][2]
        v=R[ex].get(s)
        rows.append([ex,ts(S*1000),s,ts(lastbefore),"" if e is None else round(e),ts(ret) if ret else "",
                     "нет в списке REST" if v is None else (v["status"] if ex!="bingx" else f'status={v["status"]}'),
                     "" if not v or v["qv"] is None else round(v["qv"]),
                     "да" if s in st else "нет",
                     ("оборот < 300 000 на момент перезапуска" if e is not None and e<300000 else "оборот ≈ порогу (оценка по часовым свечам ±шум)")])
# BingX 17.11 04:12
x=[y for y in PD["bingx"] if y["run"]==1794888774][0]
runs=D["bingx"]; st=json.load(open(f"{P}/collect/bingx/data/pool-state.json"))["last_above"]
nc={l.split("\t")[0]:int(l.split("\t")[3]) for l in open(P+"/nc-last.tsv",encoding="utf-8")} if False else {}
for s in x["removed"]:
    v=R["bingx"].get(s)
    rows.append(["bingx","2026-11-17 04:12",s,"2026-11-17 04:12","", "" if s not in st else "см. changes","status=%s"%v["status"] if v else "нет в списке REST",
                 "" if not v or v["qv"] is None else round(v["qv"]),"да" if s in st else "нет",
                 "status=25: контракт в списке REST, не торгуемый (в пул заново не входит,  его не удержал)" if v and v["status"]==25 else "торгуемый; вернулся пересмотром"])
hdr=["exchange","restart_utc","symbol","last_book_record_before_utc","est_turnover24h_at_restart_usd","first_record_after_utc","rest_status_now","rest_turnover24h_now_usd","subscribed_now","reason"]
out="docs/findings/symbols-drop-after-restart-2026-11-17-symbols.csv"
with open(out,"w",encoding="utf-8",newline="") as f:
    w=csv.writer(f); w.writerow(hdr); w.writerows(rows)
print(len(rows))
