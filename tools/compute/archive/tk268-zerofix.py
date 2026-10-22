# TK-268 ш.7: метрики Г-72/Г-146 (9 хешей) отбракованы только потому, что в by_month нет месяцев, где канон = 0,0 $ и 0 сделок
# (psim пишет в closes лишь варианты со сделками — как --zero-fill). Добавляем такой месяц нулём ({n:0,pnl:0,…}, months+1), если канон
# подтверждает (pnl_usd 0 и trades 0) и месяц ещё не в by_month; затем тот же check. Выход /data/tk0268/metrics-final4.jsonl (prod ∪ правка).
import json, sys, collections
sys.path.insert(0, "/opt/alpha-launch/tools/launch"); sys.path.insert(0, "/opt/alpha-launch/tools/registry")
import launch, cellhash_reg
pnl = collections.defaultdict(dict)
for l in open(launch.REG + "/results.jsonl", encoding="utf-8"):
    if l.strip():
        d = json.loads(l); pnl[d["cell_id"]][d["month"]] = (d.get("pnl_usd"), d.get("trades"), d.get("max_dd"), d.get("sum_net_bps"))
byh = collections.defaultdict(dict)
for row in cellhash_reg.build():
    for m, v in pnl.get(row[0], {}).items(): byh[row[1]][m] = v
for r in launch.overlay_rows(launch.load_config()): byh[r["cell_id"]][r["month"]] = (r.get("pnl_usd"), r.get("trades"), r.get("max_dd"), r.get("sum_net_bps"))
final = dict(launch.hash_metrics_file()); fixed = []
for h in sorted({r["hash"] for r in launch.letopis()["rows"]}):
    mt, got = final.get(h), byh.get(h, {})
    if not mt or not mt.get("n") or not got or launch.checked_metrics(mt, got): continue
    add = {m: {"n": 0, "pnl": 0.0, "overhigh_d": None, "tail_d": None} for m, v in got.items()
           if v[0] == 0 and v[1] == 0 and m not in mt["by_month"]}
    if not add: continue
    new = dict(mt); new["by_month"] = {**mt["by_month"], **add}; new["months"] = mt["months"] + len(add)
    if launch.checked_metrics(new, got):
        final[h] = new; fixed.append((h[:8], sorted(add)))
with open("/data/tk0268/metrics-final4.jsonl", "w", encoding="utf-8", newline="\n") as f:
    for h in sorted(final): f.write(json.dumps(final[h], ensure_ascii=False) + "\n")
print("исправлено", len(fixed), fixed)
launch.hash_metrics_file = lambda: final
L = launch.letopis(); print("строк", len(L["rows"]), "с метриками", L["metrics_n"], "без", len(L["rows"]) - L["metrics_n"])
