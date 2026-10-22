#!/usr/bin/env python3
"""TK-279 / В-224: готовые closes П-17 (psim v171c, янв–сен) -> cells / run_cells / results по каноническому хешу плана.
Как load_f1.py (без пересчёта, В-213), но сетка П-17 (master.p17_cell): хеши берутся из plan_cells (protocol P-17, fraction 1) по --hyp;
строка клетки — master.LINES (общая: Г-98/136/137) или master.MONTHLY[хеш][мес] (Г-81/101/135 — порог месяца). Числа — сводка `sum_closes_f1.py`
(variant = `<набор>@<форма>`): trades = n cap 0, pnl_usd = Σ$ cap 0, ext.n_cap3 / pnl_usd_cap3 = B2.
  python tools/registry/load_p17.py <closes-sum.csv> --run ID --hyp Г-98,Г-101 [--dry]"""
import argparse, csv, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cellhash as ch, db, master

ap = argparse.ArgumentParser(); ap.add_argument("csv"); ap.add_argument("--run", required=True); ap.add_argument("--dry", action="store_true")
ap.add_argument("--hyp", required=True, help="Г-98,Г-101 — гипотезы П-17, чьи хеши лежат в сводке")
ap.add_argument("--fraction", type=int, default=1)
ns = ap.parse_args()
hyps = [h.strip() for h in ns.hyp.split(",") if h.strip()]
c = master.connect()
rules = master.rules_of(c, "P-17")
grids, _ = master.build_grid(c, "P-17", ns.fraction, hyps, rules)   # заполняет master.LINES / MONTHLY
plan = {r[0] for r in c.execute(f"select cell_hash from plan_cells where protocol='P-17' and fraction=? and hyp in ({','.join('?' * len(hyps))})", (ns.fraction, *hyps))}
c.close()
cfgs = {}
for hyp, cells in grids.items():
    for av, cfg in cells:
        h = ch.cell_hash(cfg)
        if h in plan and h not in cfgs:
            cfgs[h] = (hyp, ch.normalize(cfg))
if set(cfgs) != plan:
    sys.exit(f"хешей плана {len(plan)}, в сетке {len(cfgs)}")
agg = {}
for r in csv.DictReader(open(ns.csv, encoding="utf-8")):
    agg.setdefault(r["variant"], {}).setdefault(r["month"], {})[r["cap"]] = (int(r["n"]), float(r["sum_usd"]))
months = [f"2026-{m:02d}" for m in range(1, 10)]
cells_have = {d["id"] for d in db.jl("cells.jsonl")}
rc_have = {(d["run_id"], d["cell_id"]) for d in db.jl("run_cells.jsonl")}
r_have = {(d["run_id"], d["cell_id"], d["month"]) for d in db.jl("results.jsonl")}
cells_new, rc_new, res_new, nomonth, nozero, nocode = [], [], [], [], [], []
for h, (hyp, cfg) in sorted(cfgs.items(), key=lambda x: (x[1][0], x[0])):
    assert ch.cell_hash(cfg) == h
    if h not in master.MONTHLY and master.LINES[h][0] is None:   # строки --cells нет (нет порога месяца): не считалась, не пишем
        nocode.append(h); continue
    for m in months:
        if h in master.MONTHLY:
            ln = master.MONTHLY[h].get(m)
            if ln is None:
                nomonth.append((h, hyp, m)); continue
        else:
            ln = master.LINES[h][0]
        form, _, st = ln.partition(" ")
        caps = agg.get(f"{st}@{form}", {}).get(m)
        empty = not caps
        if empty:   # psim пишет только варианты со сделками: клетка есть в --cells месяца, а в closes нет = 0 сделок
            caps = {"0": (0, 0.0), "3": (0, 0.0)}
            nozero.append((h, m))
        if (ns.run, h, m) in r_have:
            continue
        n0, s0 = caps["0"]
        ext = {"group": "p17", "src": "closes", "cap": "0 (free)", "cell_line": ln}
        if empty:
            ext["empty"] = "нет сделок в closes"
        if "3" in caps:
            ext["n_cap3"], ext["pnl_usd_cap3"] = caps["3"]
        res_new.append({"run_id": ns.run, "cell_id": h, "month": m, "trades": n0, "pnl_usd": s0, "ext": ext})
    if h not in cells_have:
        cells_new.append({"id": h, "logic_version": "bounce-v1", "form": json.dumps(cfg.get("variant"), sort_keys=True, ensure_ascii=False), "set_name": "p17/" + hyp,
                          "params": {"group": "p17", "source": "closes portfolio-sim v171c, drop TRUMP/TRX/BCH", "cfg_json": json.dumps(cfg, sort_keys=True, ensure_ascii=False)}})
    if (ns.run, h) not in rc_have:
        rc_new.append({"run_id": ns.run, "cell_id": h, "hyp_id": hyp})
print("без строки --cells (не записаны)", len(nocode), nocode, "|", "клеток", len(cells_new), "run_cells", len(rc_new), "результатов", len(res_new), "без месячной строки", len(nomonth), "пустых (0 сделок)", len(nozero))
if not ns.dry:
    for fn, rows in (("cells.jsonl", cells_new), ("run_cells.jsonl", rc_new), ("results.jsonl", res_new)):
        with open(os.path.join(db.REGD, fn), "a", encoding="utf-8", newline="\n") as f:
            for d in rows:
                f.write(json.dumps(d, ensure_ascii=False) + "\n")
