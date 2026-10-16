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
