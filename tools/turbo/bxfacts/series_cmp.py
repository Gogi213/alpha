"""bxfacts: series-minutes.jsonl of the four collectors side by side per minute ($BXFIX/ser/<exchange>.jsonl): late (book+trade+mark), lost_ms, gaps, timeouts.
"""
import json,os,datetime,collections
B=os.environ.get("BXFIX","/tmp/bxfix")+"/ser"
data={}
for ex in ("bingx","binance","bybit","okx"):
    rows={}
    for l in open(f"{B}/{ex}.jsonl"):
        r=json.loads(l)
        if "t" in r: rows[r["t"]]=r
    data[ex]=rows
ts=sorted(set().union(*[set(d) for d in data.values()]))
print("minute(UTC) | per exchange: late(book,trade,mark,raw) lost_ms gaps to")
for t in ts:
    if t<1794577020: continue
    out=[]
    for ex in ("bingx","binance","bybit","okx"):
        r=data[ex].get(t)
        out.append(ex+":"+("-" if not r else f"L{sum(r['late'][:3])} lost{r['lost_ms']} g{r['gaps']} to{sum(r['to'])}"))
    print(datetime.datetime.fromtimestamp(t,datetime.UTC).strftime("%H:%M"), " | ".join(out))
