#!/usr/bin/env python3
"""TK-315 / В-224: closes П-17 F2 (383 хеша, psim v171c, янв–сен) -> plan_cells (P-17, fraction 2) / cells / run_cells / results.
Как load_p17.py (без пересчёта, В-213), но состав F2 берётся из p17_f2.py --out (jsonl: hyp, axes, hash, set/form/axes_full) — оси F2 шире F1-сетки hyp_axes;
клетка и строка --cells — master.p17_cell с порогами P-17-thresholds{,-f2}.csv, как в плане счёта. Числа — сводка `sum_closes_f1.py` (variant = `<набор>@<форма>`).
  python tools/registry/load_p17_f2.py <closes-sum.csv> --f2 <f2-cells.jsonl> --run ID [--dry]"""
import argparse, csv, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cellhash as ch, db, master, p17_scope as sc

ap = argparse.ArgumentParser(); ap.add_argument("csv"); ap.add_argument("--f2", required=True); ap.add_argument("--run", required=True)
ap.add_argument("--dry", action="store_true"); ap.add_argument("--fraction", type=int, default=2)
ns = ap.parse_args()
cfgs, axes_of = {}, {}
for l in open(ns.f2, encoding="utf-8"):
    x = json.loads(l)
    cfg = sc.cfg_of(); av = dict(x.get("axes_full") or x["axes"])
    master.p17_cell(x["hyp"], av, cfg, x.get("form") or sc.BASE)
    h = ch.cell_hash(cfg)
    assert h == x["hash"], (x, h)
    cfgs[h] = (x["hyp"], ch.normalize(cfg)); axes_of[h] = {k: str(v) for k, v in av.items()}
agg = {}
for r in csv.DictReader(open(ns.csv, encoding="utf-8")):
    agg.setdefault(r["variant"], {}).setdefault(r["month"], {})[r["cap"]] = (int(r["n"]), float(r["sum_usd"]))
months = [f"2026-{m:02d}" for m in range(1, 10)]
cells_have = {d["id"] for d in db.jl("cells.jsonl")}
rc_have = {(d["run_id"], d["cell_id"]) for d in db.jl("run_cells.jsonl")}
r_have = {(d["run_id"], d["cell_id"], d["month"]) for d in db.jl("results.jsonl")}
pc_have = {(d["protocol"], d["fraction"], d["cell_hash"]) for d in db.jl("plan_cells.jsonl")}
cells_new, rc_new, res_new, pc_new, nomonth, nozero, nocode = [], [], [], [], [], [], []
for h, (hyp, cfg) in sorted(cfgs.items(), key=lambda x: (x[1][0], x[0])):
    if h not in master.MONTHLY and master.LINES[h][0] is None:
        nocode.append(h); continue
    if ("P-17", ns.fraction, h) not in pc_have:
        pc_new.append({"protocol": "P-17", "fraction": ns.fraction, "hyp": hyp, "cell_hash": h,
                       "axes": json.dumps(axes_of[h], ensure_ascii=False), "cfg": json.dumps(cfg, sort_keys=True, ensure_ascii=False)})
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
        if empty:
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
print("без строки --cells", len(nocode), nocode, "| plan_cells", len(pc_new), "клеток", len(cells_new), "run_cells", len(rc_new), "результатов", len(res_new),
      "без месячной строки", len(nomonth), "пустых (0 сделок)", len(nozero))
if not ns.dry:
    for fn, rows in (("plan_cells.jsonl", pc_new), ("cells.jsonl", cells_new), ("run_cells.jsonl", rc_new), ("results.jsonl", res_new)):
        with open(os.path.join(db.REGD, fn), "a", encoding="utf-8", newline="\n") as f:
            for d in rows:
                f.write(json.dumps(d, ensure_ascii=False) + "\n")
