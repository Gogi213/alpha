# TK-268 ш.7: диагностика — какие хеши из G/H (p12r2a/p12r2) не прошли check против канона (только чтение)
import json, sys, collections
sys.path.insert(0, "/opt/alpha-launch/tools/launch"); sys.path.insert(0, "/opt/alpha-launch/tools/registry")
import launch, cellhash_reg
def rd(p):
    return {d["hash"]: d for d in map(json.loads, filter(str.strip, open(p, encoding="utf-8")))}
G = rd("/data/tk0268/metrics-G.jsonl"); H = rd("/data/tk0268/metrics-H.jsonl")
old = launch.hash_metrics_file()
L = launch.letopis()
miss = collections.defaultdict(set)
for r in L["rows"]:
    if r["sharpe"] is None and r["winrate"] is None and r["status"] == "full":
        miss[r["hyp"]].add(r["hash"])
allm = set().union(*miss.values())
print("G∩пропущенных:", {h: sorted(x[:8] for x in s if x in G or x in H) for h, s in miss.items() if any(x in G or x in H for x in s)})
print("G уже в prod:", sum(1 for h in G if h in old), "из", len(G))
k = next(iter(G.values())); print({a: str(b)[:120] for a, b in k.items()})
pnl = collections.defaultdict(dict)
for l in open(launch.REG + "/results.jsonl", encoding="utf-8"):
    if l.strip():
        d = json.loads(l); pnl[d["cell_id"]][d["month"]] = (d.get("pnl_usd"), d.get("trades"))
byh = collections.defaultdict(dict)
for row in cellhash_reg.build():
    for m, v in pnl.get(row[0], {}).items(): byh[row[1]][m] = v
for h in sorted(allm):
    s = G.get(h) or H.get(h)
    if s:
        print(h[:8], "canon", dict(sorted(byh.get(h, {}).items())), "| src", str({a: b for a, b in s.items() if a != "hash"})[:240])
