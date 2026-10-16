# TK-268: почему метрики отбракованы (месяцы / Σ$): ssh calc python3 -I - < этот файл. Читает /data/tk0268/metrics-merged.jsonl
import sys, json, collections
sys.path.insert(0, "/opt/alpha-launch/tools/launch"); sys.path.insert(0, "/opt/alpha-launch/tools/registry")
import launch, cellhash_reg
new = {d["hash"]: d for d in map(json.loads, open("/data/tk0268/metrics-merged.jsonl"))}
L = launch.letopis()
res = {}
pnl = collections.defaultdict(dict)
for l in open(launch.REG + "/results.jsonl", encoding="utf-8"):
    if l.strip():
        d = json.loads(l); pnl[d["cell_id"]][d["month"]] = d.get("pnl_usd")
byh = collections.defaultdict(dict)
for row in cellhash_reg.build():
    for m, v in pnl.get(row[0], {}).items(): byh[row[1]][m] = v
kinds = collections.Counter(); ex = {}
for r in L["rows"]:
    if r["sharpe"] is not None or r["winrate"] is not None or r["status"] != "full": continue
    mt = new.get(r["hash"])
    if not mt: kinds[(r["hyp"], "нет")] += 1; continue
    got = byh[r["hash"]]
    cm = set(mt["by_month"]); gm = {m for m, v in got.items() if v is not None}
    if cm != gm: k = "месяцы"
    else:
        diffs = [(m, round(mt["by_month"][m]["pnl"], 2), round(got[m], 2)) for m in sorted(gm) if abs(mt["by_month"][m]["pnl"] - got[m]) > 0.02]
        k = "сумма"
    kinds[(r["hyp"], k)] += 1
    ex.setdefault((r["hyp"], k), (r["hash"], sorted(cm ^ gm) if k == "месяцы" else diffs[:3], mt.get("src")))
for k in sorted(kinds): print(k, kinds[k], ex.get(k))
