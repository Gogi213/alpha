#!/usr/bin/env python3
"""П-42 §13.16, правка 4 (TK-585 v3): сводка по исключённым сигналам калибровки.
  entrycal_excl.py <файл сигналов> <каталог результата entrycal_excl.sh> <sigs-каталог результата P42_SIGS> [<json>]
Исключён = нет записи bookTicker в [T_ord − 10 с; T_ord] (COVER_MS как в entrycal.py). «Нет в пуле» = у символа 0 записей `b` в файлах 12–13.11."""
import glob, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import entrycal as ec

sigf, outd, sigd = sys.argv[1:4]
rows = ec.sigs(sigd)
best, cnt = {}, {}
for p in glob.glob(os.path.join(outd, "*.txt")):
    for line in open(p, encoding="utf-8"):
        a = line.rstrip("\n").split("\t")
        if a[0] == "S":
            k = (a[1], int(a[2])); tb = int(a[3])
            if k not in best or tb > best[k]:
                best[k] = tb
        elif a[0] == "C":
            cnt[a[1]] = cnt.get(a[1], 0) + int(a[2])
res = {"signals": len(rows), "covered": 0, "excl_not_in_pool": 0, "excl_in_pool_no_record_10s": 0, "by_side": {}, "gap_s_in_pool": [], "excluded": []}
for r in rows:
    k = (r["sym"], r["t_ord"])
    tb = best.get(k)
    bs = res["by_side"].setdefault(r["side"], {"covered": 0, "not_in_pool": 0, "in_pool_no_record": 0})
    if tb is not None and r["t_ord"] - tb <= ec.COVER_MS:
        res["covered"] += 1; bs["covered"] += 1
        continue
    if cnt.get(r["sym"], 0) == 0:
        res["excl_not_in_pool"] += 1; bs["not_in_pool"] += 1; kind = "not_in_pool"; gap = None
    else:
        res["excl_in_pool_no_record_10s"] += 1; bs["in_pool_no_record"] += 1; kind = "in_pool"
        gap = (r["t_ord"] - tb) / 1000 if tb is not None else None
        res["gap_s_in_pool"].append(gap)
    res["excluded"].append({"sym": r["sym"], "side": r["side"], "t_ord": r["t_ord"], "kind": kind, "last_record_gap_s": gap, "sym_records": cnt.get(r["sym"], 0)})
print(json.dumps(res, ensure_ascii=False, indent=1))
if len(sys.argv) > 4:
    json.dump(res, open(sys.argv[4], "w", encoding="utf-8"), ensure_ascii=False, indent=1)
