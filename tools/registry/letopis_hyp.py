#!/usr/bin/env python3
"""Летопись (реестр прогонов, TK-089/В-202): таблица всех гипотез пула одним запросом.
Месяцы — по results (run_cells.hyp_id и run_hypotheses -> клетки прогона), вердикт — по verdicts.hyp_id,
класс — tk024-pool-inventory. Пусто = в Летописи нет; что нет — не «посчитано»."""
import csv, json, os, sqlite3, sys
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db
OUT = os.path.join(ROOT, "docs", "findings", "letopis-hypotheses-2026-10-08")
MONTHS = [f"2026-{m:02d}" for m in range(1, 11)]
if not os.path.exists(db.DB):
    import registry; registry.build()
con = sqlite3.connect(db.DB)
q = lambda s, *a: con.execute(s, a).fetchall()
inv = {r["id"]: r for r in csv.DictReader(open(os.path.join(ROOT, "docs/findings/tk024-pool-inventory-2026-10-02.csv"), encoding="utf-8"), delimiter=";")}
hyps = [json.loads(l) for l in open(os.path.join(ROOT, "docs/registry/hypotheses.jsonl"), encoding="utf-8")]
rows = []
for h in hyps:
    i = h["id"]
    # клетки гипотезы: прямая привязка клетка<->гипотеза, либо через прогоны hyp
    cells = {c for (c,) in q("select cell_id from run_cells where hyp_id=?", i)}
    runs = {r for (r,) in q("select run_id from run_hypotheses where hyp_id=?", i)}
    for r in runs:
        cells |= {c for (c,) in q("select cell_id from run_cells where run_id=?", r)}
    mon = sorted({m for c in cells for (m,) in q("select distinct month from results where cell_id=?", c)}) if cells else []
    grid = ""
    if cells:
        k = {tuple(x) for c in cells for x in q("select form,set_name,entry_bps,stop_bps,take_bps,deadline_s from cells where id=?", c)}
        grid = f"{len(cells)} клеток" + (f"; стопы {sorted({x[3] for x in k if x[3] is not None})}" if len({x[3] for x in k}) > 1 else "")
    vs = q("select judge,verdict,review_path,ts from verdicts where hyp_id=?", i)
    ver = "; ".join(f"{v[1]} ({v[0]}, {v[3]})" for v in vs if v[1] != "см. текст") or ""
    miss = [m for m in MONTHS if m not in mon]
    cls = inv.get(i, {}).get("класс_TK024", "")
    rows.append({"id": i, "семья": h["family"], "класс_TK024": cls, "формулировка": h["title"],
                 "месяцы_в_Летописи": ",".join(m[5:] for m in mon), "сетка": grid,
                 "вердикт_Летописи": ver, "статус_пула_25.09": h["ext"].get("status_pool_0925", "")[:160],
                 "не_хватает_до_01.01-02.10": ("нет данных в Летописи" if not mon else ",".join(m[5:] for m in miss) if miss else "—"),
                 "прогонов": len(runs)})
with open(OUT + ".csv", "w", encoding="utf-8", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0]), delimiter=";"); w.writeheader(); w.writerows(rows)
n = len(rows); have = sum(1 for r in rows if r["месяцы_в_Летописи"])
with open(OUT + ".md", "w", encoding="utf-8") as f:
    f.write(f"# Летопись: гипотезы пула (08.10, TK-089)\n\n{n} гипотез; в Летописи месяцы счёта есть у {have}, у {n-have} — нет (нет данных).\n"
            "Месяцы — из results по клеткам гипотезы (run_cells.hyp_id + run_hypotheses); вердикты — verdicts.hyp_id, «см. текст» не считается вердиктом. "
            "Генератор — `python tools/registry/letopis_hyp.py`; .csv — `;`.\n\n"
            "| id | семья | класс | месяцы | сетка | вердикт | не хватает |\n|---|---|---|---|---|---|---|\n")
    for r in rows:
        f.write(f"| {r['id']} | {r['семья']} | {r['класс_TK024']} | {r['месяцы_в_Летописи']} | {r['сетка']} | {r['вердикт_Летописи']} | {r['не_хватает_до_01.01-02.10']} |\n")
print(n, have)
