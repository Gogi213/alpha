# TK-268 ш.7: у R2A в каноне нет $, только число сделок — чем оно считается (cap0/cap3 closes p12r2a) (только чтение)
import json
v = "t-bid-btc4h-q1@ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800-pyre5u1"
for cap in ("cap0", "cap3"):
    d = json.load(open(f"/data/p12r2a/closes-va-{cap}-2026-01.json"))
    x = d.get(v)
    print(cap, type(x).__name__, (len(x) if x is not None else None), str(x)[:300])
d = json.load(open("/data/p12r2a/closes-va-cap0-2026-01.json"))
k = next(iter(d)); print("cap0 ключ", k, str(d[k])[:200])
