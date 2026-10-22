# TK-268: для хешей Летописи без метрик — из каких прогонов загружены (run_id, примечание): ssh calc python3 -I - < этот файл
import sys, json, collections
sys.path.insert(0, "/opt/alpha-launch/tools/launch")
import launch, cellhash_reg
L = launch.letopis(); mets = launch.hash_metrics_file()
need = {}
for r in L["rows"]:
    if r["status"] == "full" and not (r["sharpe"] is not None or r["winrate"] is not None):
        need[r["hash"]] = r["hyp"]
REG = launch.REG
runs = {}
for l in open(REG + "/runs.jsonl", encoding="utf-8"):
    if l.strip():
        d = json.loads(l); runs[d.get("run_id")] = d
rc = collections.defaultdict(set)
cellrun = {}
for l in open(REG + "/run_cells.jsonl", encoding="utf-8"):
    if l.strip():
        d = json.loads(l); cellrun.setdefault(d["cell_id"], set()).add(d["run_id"])
h2c = collections.defaultdict(set)
for row in cellhash_reg.build():
    if row[1] in need: h2c[row[1]].add(row[0])
agg = collections.defaultdict(collections.Counter)
for h, hy in need.items():
    rs = set()
    for c in h2c.get(h, ()): rs |= cellrun.get(c, set())
    agg[(hy, tuple(sorted(rs)) if rs else ("overlay",))][0] += 1
for (hy, rs), c in sorted(agg.items()): print(hy, rs, c[0])
print("run keys sample:", list(next(iter(runs.values())).keys()))
for rid in sorted({x for (hy, rs) in agg for x in rs if x in runs}):
    d = runs[rid]; print(rid, {k: (str(v)[:90]) for k, v in d.items() if k in ("note","source","what","kind","title","text")})
