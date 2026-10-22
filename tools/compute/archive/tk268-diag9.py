# TK-268 ш.7: Г-72/Г-146 в metrics-A…H: какие месяцы и как отвечает check (только чтение)
import json, sys, collections
sys.path.insert(0, "/opt/alpha-launch/tools/launch"); sys.path.insert(0, "/opt/alpha-launch/tools/registry")
import launch, cellhash_reg
def rd(p):
    return {d["hash"]: d for d in map(json.loads, filter(str.strip, open(p, encoding="utf-8")))}
S = {n: rd(f"/data/tk0268/metrics-{n}.jsonl") for n in "ABCDEFGH"}
pnl = collections.defaultdict(dict)
for l in open(launch.REG + "/results.jsonl", encoding="utf-8"):
    if l.strip():
        d = json.loads(l); pnl[d["cell_id"]][d["month"]] = (d.get("pnl_usd"), d.get("trades"))
byh = collections.defaultdict(dict)
for row in cellhash_reg.build():
    for m, v in pnl.get(row[0], {}).items(): byh[row[1]][m] = v
for h in [x for x in S["D"] if x.startswith(("cea20ae6", "363d1a92", "38bf2aad"))] + [x for x in rd("/data/launch/registry/metrics.jsonl") if x.startswith(("cea20ae6", "363d1a92", "38bf2aad"))]:
    print(h[:8], {n: (sorted(m[:0] or [k[-2:] for k in S[n][h]["by_month"]]) if h in S[n] else None) for n, m in S.items()}, "canon", sorted(k[-2:] for k, v in byh[h].items() if v[0] is not None))
