#!/usr/bin/env python3
"""TK-289 (П-18, В-35): строки Летописи для 9 осей титровки B1 — hypotheses (П18-<ось>, класс «метод»: титровка базы, не гипотеза, В-228)
и plan_cells (P-18, F1) по хешам p18_scope. Как master.plan-grid, но сетка фиксирована протоколом, а не hyp_axes. Идемпотентно (заменяет свои строки).
  python tools/registry/plan_p18.py [--prefix П18-] [--dry]"""
import argparse, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "compute"))
import cellhash as ch, db, p18_scope as sc

ap = argparse.ArgumentParser(); ap.add_argument("--prefix", default="П18-"); ap.add_argument("--dry", action="store_true")
ns = ap.parse_args()
TITLE = {"1": "вход: глубина лестницы (to), σ", "2": "стоп, % от входа", "3": "тейк, % от входа", "4": "дедлайн сделки, с", "5": "ttl входного ордера, с",
         "6a": "пол номинала стены usd_min, $", "6b": "порог силы стены strength_min, квантиль месяца", "6v": "сила стены В-67 (размер / оборот за час, %) × пол номинала usd_min, $", "7": "фильтр BTC за 4 ч, btc4h_max, %", "8": "окно σ, мин"}
fam = sc.cells()
hyps, plan = [], []
for ax in ("1", "2", "3", "4", "5", "6a", "6b", "6v", "7", "8"):
    hid = ns.prefix + ax
    hyps.append({"id": hid, "family": "титровка B1", "protocol": "П-18",
                 "title": f"Титровка базы B1 (OFAT): ось {ax} — {TITLE[ax]}",
                 "ext": {"class": "method", "spec": "docs/research/P-18-b1-titration.md", "scope": "docs/research/P-18-scope.json",
                         "mechanism": "метод: одна ось за раз от центра B1, остальные оси зафиксированы; отбраковки нет (В-23)", "source": "В-35"}})
    for c in fam[ax]:
        v = next(iter(c["axes"].values()))
        cfg = sc.cfg_of(ax, v)
        plan.append({"protocol": "P-18", "fraction": 1, "hyp": hid, "cell_hash": c["hash"],
                     "axes": json.dumps({"ax": ax, **c["axes"]}, ensure_ascii=False), "cfg": json.dumps(ch.normalize(cfg), sort_keys=True, ensure_ascii=False)})
print("гипотез", len(hyps), "plan_cells", len(plan), "хешей", len({p["cell_hash"] for p in plan}))
if not ns.dry:
    def put(fn, new, keyf):
        keys = {keyf(d) for d in new}
        old = [d for d in db.jl(fn) if keyf(d) not in keys]
        with open(os.path.join(db.REGD, fn), "w", encoding="utf-8", newline="\n") as f:
            for d in old + new:
                f.write(json.dumps(d, ensure_ascii=False) + "\n")
    put("hypotheses.jsonl", hyps, lambda d: d["id"])
    put("plan_cells.jsonl", plan, lambda d: (d["protocol"], d["fraction"], d["hyp"], d["cell_hash"]))
