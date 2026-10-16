#!/bin/bash
# TK-268 ш.1: метрики для строк Летописи без метрик — из готовых closes cap 0, без бэктеста. Выход /data/tk0268/{metrics.jsonl,metrics-merged.jsonl,verify.txt,cover.txt}
set -e
mkdir -p /data/tk0268 && cd /data/tk0268
[ -d launch ] || cp -a /data/tk0258/launch ./launch
cd launch
# build_metrics.py / verify_metrics.py с --alias-cells кладутся в /data/tk0268/launch перед подачей (cat по ssh)
S1=/data/tk0231/psim/closes-p14f1-cap0-{m}.json
S2=/data/tk0196/psim/closes-f1-cap0-{m}.json
S3=/data/p12r3a/closes-r2bd-cap0-{m}.json
D1=/data/tk0251/ps/closes-cap0-{mon}.json
D2=/data/tk0251/p3/ps/closes-cap0-{mon}.json
D3=/data/tk0113/drop2/r1/closes-cap0-{mon}.json
D4=/data/tk0113/drop2/ext/closes-cap0-{m}.json
AL="--alias-cells /data/launch/registry/cells.jsonl"
python3 build_metrics.py --out /data/tk0268/metrics.jsonl --direct $D1 --direct $D2 --direct $D3 --direct $D4 $AL $S1 $S2 $S3
python3 verify_metrics.py --metrics /data/tk0268/metrics.jsonl --n 14 --direct $D1 --direct $D2 --direct $D3 --direct $D4 $AL $S1 $S2 $S3 | tee /data/tk0268/verify.txt
python3 -I - <<'PY' | tee /data/tk0268/cover.txt
import sys, json, collections
sys.path.insert(0, "/opt/alpha-launch/tools/launch")
import launch
old = launch.hash_metrics_file()
new = {d["hash"]: d for d in map(json.loads, open("/data/tk0268/metrics.jsonl"))}
same = sum(1 for h, d in old.items() if new.get(h) == d)
print(f"прежних {len(old)}, совпало по записи {same}, новых {len(set(new) - set(old))}, всего {len(new)}")
new = {**old, **new}   # Г-117 (TK-266) — метрики из closes вне источников этого прогона: берём прежние
with open("/data/tk0268/metrics-merged.jsonl", "w", encoding="utf-8", newline="\n") as f:
    for h in sorted(new):
        f.write(json.dumps(new[h], ensure_ascii=False) + "\n")
launch.hash_metrics_file = lambda: new
L = launch.letopis()
miss = collections.Counter(); why = collections.Counter()
for r in L["rows"]:
    if r["sharpe"] is None and r["winrate"] is None:
        miss[r["hyp"]] += 1
        why[r["status"] + (":нет в файле" if r["hash"] not in new else ":отбраковано")] += 1
print("строк", len(L["rows"]), "с метриками", L["metrics_n"], "без", sum(miss.values()), dict(why))
for h in sorted(miss): print(h, miss[h])
PY
