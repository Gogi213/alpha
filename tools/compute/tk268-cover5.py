# TK-268 ш.8 (cover4 + сверка по n для канона без $; launch из argv[1]): итоговый файл метрик = прежние ∪ выбранные из A/B (первый вариант, прошедший launch.checked_metrics против канона строки) + охват Летописи
import sys, json, collections
sys.path.insert(0, sys.argv[1]); sys.path.insert(0, "/opt/alpha-launch/tools/registry")
import launch, cellhash_reg
rd = lambda p: {d["hash"]: d for d in map(json.loads, filter(str.strip, open(p, encoding="utf-8")))}
old, A, B, C, D, E, F, G, H = launch.hash_metrics_file(), rd("/data/tk0268/metrics-A.jsonl"), rd("/data/tk0268/metrics-B.jsonl"), rd("/data/tk0268/metrics-C.jsonl"), rd("/data/tk0268/metrics-D.jsonl"), rd("/data/tk0268/metrics-E.jsonl"), rd("/data/tk0268/metrics-F.jsonl"), rd("/data/tk0268/metrics-G.jsonl"), rd("/data/tk0268/metrics-H.jsonl")
cfg = launch.load_config()
pnl = collections.defaultdict(dict)
for l in open(launch.REG + "/results.jsonl", encoding="utf-8"):
    if l.strip():
        d = json.loads(l); pnl[d["cell_id"]][d["month"]] = (d.get("pnl_usd"), d.get("trades"), d.get("max_dd"), d.get("sum_net_bps"))
byh = collections.defaultdict(dict)
for row in cellhash_reg.build():
    for m, v in pnl.get(row[0], {}).items(): byh[row[1]][m] = v
for r in launch.overlay_rows(cfg): byh[r["cell_id"]][r["month"]] = (r.get("pnl_usd"), r.get("trades"), r.get("max_dd"), r.get("sum_net_bps"))
final = dict(old); how = collections.Counter()
for h in sorted({r["hash"] for r in launch.letopis()["rows"]}):   # все хеши Летописи, не только план текущего протокола (P-15 раньше выпадал)
    got = byh.get(h, {})
    if not got or launch.checked_metrics(old.get(h), got): continue
    for nm, src in (("A", A), ("B", B), ("C", C), ("D", D), ("E", E), ("F", F), ("G", G), ("H", H)):
        if launch.checked_metrics(src.get(h), got):
            final[h] = src[h]; how[nm] += 1; break
    else:
        how["нет"] += 1
with open("/data/tk0268/metrics-final5.jsonl", "w", encoding="utf-8", newline="\n") as f:
    for h in sorted(final): f.write(json.dumps(final[h], ensure_ascii=False) + "\n")
print("прежних", len(old), "A", len(A), "B", len(B), "итог", len(final), "выбрано", dict(how))
launch.hash_metrics_file = lambda: final
L = launch.letopis()
miss = collections.Counter(); why = collections.Counter()
for r in L["rows"]:
    if r["sharpe"] is None and r["winrate"] is None:
        miss[r["hyp"]] += 1; why[r["status"] + (":нет в файле" if r["hash"] not in final else ":отбраковано")] += 1
print("строк", len(L["rows"]), "с метриками", L["metrics_n"], "без", sum(miss.values()), dict(why))
for h in sorted(miss): print(h, miss[h])
# причины прочерка: кандидат из источников (месяцы = месяцы канона — первым) против канона строки -> metrics_why-final5.jsonl
whys = {}
for r in L["rows"]:
    if not r["mwhy"]: continue
    got = byh.get(r["hash"], {})
    cands = [s[r["hash"]] for s in (G, H, F, E, D, C, B, A) if r["hash"] in s and s[r["hash"]].get("n")]
    cand = next((c for c in cands if set(c["by_month"]) == set(got)), cands[0] if cands else None)
    whys[r["hash"]] = launch.metrics_why(cand, got, r["off"])
with open("/data/tk0268/metrics_why-final5.jsonl", "w", encoding="utf-8", newline="\n") as f:
    for h in sorted(whys): f.write(json.dumps({"hash": h, "why": whys[h]}, ensure_ascii=False) + "\n")
launch.hash_why_file = lambda: whys
L = launch.letopis()
print("метрики", L["metrics_n"], "прочерк с причиной", L["metrics_dash"], "итого", len(L["rows"]))
rs = collections.Counter(); ex = {}
for r in L["rows"]:
    if r["mwhy"]:
        k = r["mwhy"].split(" (×")[0]; rs[k] += 1; ex.setdefault(k, collections.Counter())[r["hyp"]] += 1
for k, v in rs.most_common(): print(v, k, dict(ex[k]))
