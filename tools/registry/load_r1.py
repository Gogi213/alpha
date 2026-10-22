#!/usr/bin/env python3
"""TK-251 / В-224: готовые closes R1 (psim p12-r1-psim.py, пул v171c, янв–сен) -> cells / run_cells / results по каноническому хешу плана.
Без пересчёта (В-213). Хеши — те, что есть в сводке closes И в master.R1X (--which r1x) / master.R1COL (--which r1col); конфиг клетки берётся из
сетки P-14 (как load_f1.py). Числа — сводка `sum_closes_r1.py`: trades = n cap 0, pnl_usd = Σ$ cap 0, ext.n_cap3 / pnl_usd_cap3 = B2.
Клетка без closes в месяце — 0 сделок (psim пишет только варианты со сделками), ext.empty. Сверка: --check печатает Σ по (месяц, cap) из записанного.
  python tools/registry/load_r1.py <closes-sum.csv> --run ID --which r1x|r1col|g134 [--protocol P-15] [--dry]"""
import argparse, collections, csv, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cellhash as ch, db, master

ap = argparse.ArgumentParser(); ap.add_argument("csv"); ap.add_argument("--run", required=True); ap.add_argument("--dry", action="store_true")
ap.add_argument("--which", required=True, choices=("r1x", "r1col", "g134"))
ap.add_argument("--protocol", default="P-14", help="протокол (TK-274: P-15, Г-134)")
ap.add_argument("--fraction", type=int, default=1, help="доля плана (TK-303: 2 = F2 Г-134)")
ap.add_argument("--f1-py", help="TK-303: файл .py с f1() — сито F1 для сетки F2 (tools/compute/tk303-f2-plan.py)")
ns = ap.parse_args()
c = master.connect()
rules = master.rules_of(c, ns.protocol)
f1 = None
if ns.f1_py:
    import importlib.util as iu
    sp = iu.spec_from_file_location("f1src", ns.f1_py); f1m = iu.module_from_spec(sp); sp.loader.exec_module(f1m); f1 = f1m.f1()
grids, _ = master.build_grid(c, ns.protocol, ns.fraction, None, rules, f1)   # заполняет master.R1X / R1COL
c.close()
want = ({ch.cell_hash(cfg) for av, cfg in grids.get("Г-134", [])} if ns.which == "g134"   # TK-274: Г-134 (П-15) ни в R1X, ни в R1COL — отбор r1cell --hist own
        else set(master.R1X if ns.which == "r1x" else master.R1COL))
cfgs = {}
for hyp, cells in sorted(grids.items()):
    for av, cfg in cells:
        h = ch.cell_hash(cfg)
        if h in want and h not in cfgs:
            cfgs[h] = (hyp, ch.normalize(cfg))
agg = collections.defaultdict(dict)
for r in csv.DictReader(open(ns.csv, encoding="utf-8")):
    agg[r["variant"]].setdefault(r["month"], {})[r["cap"]] = (int(r["n"]), float(r["sum_usd"]))
extra = sorted(set(agg) - set(cfgs))
if extra:
    sys.exit(f"хешей в сводке, которых нет в {ns.which} сетки {ns.protocol}: {len(extra)} {extra[:5]}")
months = [f"2026-{m:02d}" for m in range(1, 10)]
# хеш без единой записи за все месяцы в сводке — не считали (для R1COL это 145 r1ext и «данных нет»), не пишем
todo = {h: v for h, v in cfgs.items() if h in agg}
cells_have = {d["id"] for d in db.jl("cells.jsonl")}
rc_have = {(d["run_id"], d["cell_id"]) for d in db.jl("run_cells.jsonl")}
r_have = {(d["run_id"], d["cell_id"], d["month"]) for d in db.jl("results.jsonl")}
cells_new, rc_new, res_new, nozero = [], [], [], []
for h, (hyp, cfg) in sorted(todo.items(), key=lambda x: (x[1][0], x[0])):
    assert ch.cell_hash(cfg) == h
    for m in months:
        caps = agg[h].get(m)
        empty = not caps
        if empty:
            caps = {"0": (0, 0.0), "3": (0, 0.0)}
            nozero.append((h, m))
        if (ns.run, h, m) in r_have:
            continue
        n0, s0 = caps["0"]
        ext = {"group": ns.which, "src": "closes", "cap": "0 (free)"}
        if empty:
            ext["empty"] = "нет сделок в closes"
        if "3" in caps:
            ext["n_cap3"], ext["pnl_usd_cap3"] = caps["3"]
        res_new.append({"run_id": ns.run, "cell_id": h, "month": m, "trades": n0, "pnl_usd": s0, "ext": ext})
    if h not in cells_have:
        cells_new.append({"id": h, "logic_version": "bounce-v1", "form": json.dumps(cfg.get("variant"), sort_keys=True, ensure_ascii=False), "set_name": f"{ns.which}/" + hyp,
                          "params": {"group": ns.which, "source": "closes portfolio-sim v171c, drop TRUMP/TRX/BCH", "cfg_json": json.dumps(cfg, sort_keys=True, ensure_ascii=False)}})
    if (ns.run, h) not in rc_have:
        rc_new.append({"run_id": ns.run, "cell_id": h, "hyp_id": hyp})
tot = collections.defaultdict(lambda: [0, 0.0])
for r in res_new:
    for cap, (n, s) in (("0", (r["trades"], r["pnl_usd"])), ("3", (r["ext"]["n_cap3"], r["ext"]["pnl_usd_cap3"]))):
        tot[(r["month"], cap)][0] += n; tot[(r["month"], cap)][1] += s
print(f"{ns.which}: хешей в сетке {len(cfgs)}, в сводке {len(agg)}, клеток новых {len(cells_new)}, run_cells {len(rc_new)}, результатов {len(res_new)}, пустых (0 сделок) {len(nozero)}")
for (m, cap), (n, s) in sorted(tot.items()):
    print(f"LOAD {m} cap{cap} n={n} sum_usd={s:.4f}")
if not ns.dry:
    for fn, rows in (("cells.jsonl", cells_new), ("run_cells.jsonl", rc_new), ("results.jsonl", res_new)):
        with open(os.path.join(db.REGD, fn), "a", encoding="utf-8", newline="\n") as f:
            for d in rows:
                f.write(json.dumps(d, ensure_ascii=False) + "\n")
