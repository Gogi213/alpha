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

# --- сводки в репо (R2 пул, TK-065; R1 — колонки пула TK-064; число клеток П-12) ---
import re, collections
F = os.path.join(ROOT, "docs", "findings")
BASE_T = "ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800"
BASE_1 = "ladder3x0..0.0409sw2-pct2-1to1-14400-ttl1800"
SUF = [(r"-pyre", "Г-92"), (r"-pynw", "Г-93"), (r"-pyeat", "Г-94"), (r"-conv", "Г-119"), (r"-half", "Г-114"),
       (r"-tsl", "Г-117"), (r"-pyfresh", "Г-87")]
def r2_hyp(sset, form):
    if form.startswith("ladder3x0..0.0409sw3"): return "Г-95", BASE_T
    for pat, h in SUF:
        if pat in form: return h, (BASE_1 if h == "Г-117" else BASE_T)
    return None, None
r2 = collections.defaultdict(lambda: collections.defaultdict(dict))  # hyp -> form -> month -> sum
base = {}
for r in csv.DictReader(open(os.path.join(F, "r2-pool-summary-2026-10-07.csv"), encoding="utf-8")):
    if r["form"] in (BASE_T, BASE_1): base[(r["set"], r["form"], r["month"])] = float(r["sum_net_bps"])
for r in csv.DictReader(open(os.path.join(F, "r2-pool-summary-2026-10-07.csv"), encoding="utf-8")):
    h, bf = r2_hyp(r["set"], r["form"])
    if h: r2[h][(r["set"], r["form"], bf)][r["month"]] = float(r["sum_net_bps"])
r2info = {}
for h, forms in r2.items():
    tot = []; ab = []
    ms = set()
    for (st, fm, bf), mm in forms.items():
        d = sum(v - base[(st, bf, m)] for m, v in mm.items() if (st, bf, m) in base)
        tot.append(d); ms |= set(mm); ab.append(sum(mm.values()))
    bb = [base[(st, bf, m)] for (st, fm, bf) in list(forms)[:1] for m in sorted(ms) if (st, bf, m) in base]
    r2info[h] = (sorted(ms), len(forms), min(tot), max(tot), sum(1 for t in tot if t > 0), min(ab), max(ab), sum(bb), sum(1 for a in ab if a > 0))
p12 = collections.Counter(("Г-" + re.sub(r"\D.*", "", x["family"][1:]) if x["family"][0] in "ge" and x["family"][1:2].isdigit() else x["family"]) + "|" + x["class"]
                          for x in csv.DictReader(open(os.path.join(F, "p12-cells-2026-10-06.csv"), encoding="utf-8")))
p12n = {k.split("|")[0]: (v, k.split("|")[1]) for k, v in p12.items()}
WHY = {"Д": "класс Д: посчитана на авг/сен (TK-009, П-02…П-08), янв–июл и окт в Летописи нет",
       "П": "класс П: Python на сигналах/свечах, результата в Летописи нет",
       "Н": "класс Н: не операционализируема (нет данных или определения)", "О": "класс О: отложена владельцем",
       "М": "класс М: метод/контроль, не торговое правило", "Е": "класс Е: считать только вне авг/сен (TK-022)",
       "R1": "класс R1: исходы клеток ждут TK-064 lvl-all2", "R2": "класс R2: в сводке TK-065 клеток нет"}
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
    note = ""
    if i in r2info:
        ms, nf, lo, hi, pos, alo, ahi, bs, apos = r2info[i]
        mon = sorted(set(mon) | set(ms)); grid = f"{nf} клеток R2 (TK-065)"
        note = f"R2 пул 10 мес: Δ к базе, сырые bps, сумма: {lo:+.0f}…{hi:+.0f}; клеток с Δ>0: {pos} из {nf}; абсолют клетки за 10 мес {alo:+.0f}…{ahi:+.0f}, база {bs:+.0f}; клеток с абсолютом >0: {apos} из {nf}; без Холма/WY"
    elif i in p12n:
        n, cl = p12n[i]; grid = grid or f"{n} клеток {cl} в П-12 (план)"
        note = ("R1: колонки признаков на весь пул янв–окт посчитаны (TK-064, /data/tk064/pool); исходы клеток не посчитаны — ждут lvl-all2.done"
                if cl == "R1" else "R2: клетки в плане П-12, в r2-pool-summary (TK-065) их нет — не посчитаны либо вне сводки")
    vs = q("select judge,verdict,review_path,ts from verdicts where hyp_id=?", i)
    ver = "; ".join(f"{v[1]} ({v[0]}, {v[3]})" for v in vs if v[1] != "см. текст") or ""
    miss = [m for m in MONTHS if m not in mon]
    cls = inv.get(i, {}).get("класс_TK024", "")
    where = inv.get(i, {}).get("где_или_что_нужно", "")
    pm = [m for w, m in (("авг", "08"), ("сен", "09")) if w in where] if cls == "Д" else []
    rows.append({"id": i, "месяцы_по_протоколу": ",".join(pm) + (" (П-02…П-08, TK-024)" if pm else ""), "семья": h["family"], "класс_TK024": cls, "формулировка": h["title"],
                 "месяцы_в_Летописи": ",".join(m[5:] for m in mon), "сетка": grid,
                 "вердикт_Летописи": ver, "статус_пула_25.09": h["ext"].get("status_pool_0925", "")[:160],
                 "не_хватает_до_01.01-02.10": (WHY.get(cls, "нет данных в Летописи") if not mon else ",".join(m[5:] for m in miss) if miss else "—"),
                 "итог_по_данным": note, "прогонов": len(runs)})
with open(OUT + ".csv", "w", encoding="utf-8", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0]), delimiter=";"); w.writeheader(); w.writerows(rows)
n = len(rows); have = sum(1 for r in rows if r["месяцы_в_Летописи"])
with open(OUT + ".md", "w", encoding="utf-8") as f:
    f.write(f"# Летопись: гипотезы пула (08.10, TK-089)\n\n{n} гипотез; в Летописи месяцы счёта есть у {have}, у {n-have} — нет (нет данных).\n"
            "Месяцы — из results по клеткам гипотезы (run_cells.hyp_id + run_hypotheses); вердикты — verdicts.hyp_id, «см. текст» не считается вердиктом. "
            "Δ к базе — разность суммарных сырых bps клетки и базы (B1 / B3 для Г-117) по r2-pool-summary, без равной экспозиции, KPI-часов, Холма — это не вердикт. Генератор — `python tools/registry/letopis_hyp.py`; .csv — `;`.\n\n"
            "| id | семья | класс | месяцы | сетка | итог по данным | вердикт | не хватает |\n|---|---|---|---|---|---|---|---|\n")
    for r in rows:
        f.write(f"| {r['id']} | {r['семья']} | {r['класс_TK024']} | {r['месяцы_в_Летописи']} | {r['сетка']} | {r['итог_по_данным']} | {r['вердикт_Летописи']} | {r['не_хватает_до_01.01-02.10']} |\n")
print(n, have)
