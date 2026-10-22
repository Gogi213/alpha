# TK-268: строки Летописи без метрик — по гипотезам и причине (только чтение): ssh calc python3 -I - < tools/compute/archive/tk268-nometrics.py
import sys, json, collections
sys.path.insert(0, "/opt/alpha-launch/tools/launch")
import launch
L = launch.letopis()
mets = launch.hash_metrics_file()
rows = L["rows"]
print("rows", len(rows), "metrics_n", L["metrics_n"], "metrics.jsonl", len(mets))
cnt = collections.defaultdict(collections.Counter)
ex = {}
for r in rows:
    has = r["sharpe"] is not None or r["winrate"] is not None
    if has:
        continue
    if r["status"] == "none":
        why = "none:" + str(r["off"])
    else:
        mt = mets.get(r["hash"])
        if not mt or not mt.get("n"):
            why = r["status"] + ":нет в metrics.jsonl"
        else:
            why = r["status"] + ":отбраковано(месяцы/сумма)"
    cnt[r["hyp"]][why] += 1
    ex.setdefault((r["hyp"], why), r["hash"])
tot = collections.Counter()
for h in sorted(cnt):
    for w, n in cnt[h].items():
        print(h, w, n, ex[(h, w)][:12]); tot[w] += n
print("TOTAL", dict(tot), sum(tot.values()))
