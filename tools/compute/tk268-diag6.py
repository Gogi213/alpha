# TK-268 ш.7: почему Г-72/Г-146/Г-65/Г-110 отбракованы check-ом против канона (только чтение)
import json, sys, collections
sys.path.insert(0, "/opt/alpha-launch/tools/launch"); sys.path.insert(0, "/opt/alpha-launch/tools/registry")
import launch, cellhash_reg
mets = launch.hash_metrics_file()
L = launch.letopis()
pnl = collections.defaultdict(dict)
for l in open(launch.REG + "/results.jsonl", encoding="utf-8"):
    if l.strip():
        d = json.loads(l); pnl[d["cell_id"]][d["month"]] = (d.get("pnl_usd"), d.get("trades"))
byh = collections.defaultdict(dict)
for row in cellhash_reg.build():
    for m, v in pnl.get(row[0], {}).items(): byh[row[1]][m] = v
seen = set()
for r in L["rows"]:
    if r["status"] != "full" or r["sharpe"] is not None or r["winrate"] is not None or r["hash"] in seen: continue
    mt = mets.get(r["hash"])
    if not mt or not mt.get("n"): continue
    seen.add(r["hash"])
    got = byh.get(r["hash"], {})
    cm = {m: v[0] for m, v in got.items() if v[0] is not None}
    bm = {m: x["pnl"] for m, x in mt["by_month"].items()}
    diff = {m: (cm.get(m), bm.get(m)) for m in sorted(set(cm) | set(bm)) if cm.get(m) is None or bm.get(m) is None or abs(cm[m] - bm[m]) > 0.02}
    print(r["hyp"], r["hash"][:8], "расхождения (канон, closes):", diff)
