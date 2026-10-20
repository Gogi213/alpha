#!/usr/bin/env python3
"""TK-303 / В-224: готовые closes Г-102 B (T 600/720/840/960 × k 1…8 = 32 клетки, вне сетки master) -> cells / run_cells / results.
Без пересчёта (В-213). Хеш и конфиг — строка плана <plan.jsonl> (tk303-g102b-plan.py); строка --cells — по (k, T) из <plan.cells>;
числа — сводка sum_closes_f1.py (префикс p14f1): trades = n cap 0, pnl_usd = Σ$ cap 0, ext.n_cap3 / pnl_usd_cap3 = B2.
  python tools/registry/load_g102b.py <closes-sum.csv> <plan.jsonl> <plan.cells> --run ID [--dry]"""
import argparse, collections, csv, json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cellhash as ch, db

ap = argparse.ArgumentParser(); ap.add_argument("csv"); ap.add_argument("plan"); ap.add_argument("cells"); ap.add_argument("--run", required=True); ap.add_argument("--dry", action="store_true")
ns = ap.parse_args()
lines = {}
for ln in open(ns.cells, encoding="utf-8").read().splitlines():
    m = re.match(r"retreat(\d+)t(\d+)-", ln)
    if m:
        lines[(int(m[1]), int(m[2]))] = ln
plan = []
for l in open(ns.plan, encoding="utf-8"):
    if l.strip():
        d = json.loads(l); h = d.pop("hash"); assert ch.cell_hash(d) == h, h
        plan.append((h, d))
agg = collections.defaultdict(dict)
for r in csv.DictReader(open(ns.csv, encoding="utf-8")):
    agg[r["variant"]].setdefault(r["month"], {})[r["cap"]] = (int(r["n"]), float(r["sum_usd"]))
months = [f"2026-{m:02d}" for m in range(1, 10)]
cells_have = {d["id"] for d in db.jl("cells.jsonl")}
rc_have = {(d["run_id"], d["cell_id"]) for d in db.jl("run_cells.jsonl")}
r_have = {(d["run_id"], d["cell_id"], d["month"]) for d in db.jl("results.jsonl")}
cells_new, rc_new, res_new, nozero = [], [], [], []
for h, cfg in sorted(plan, key=lambda x: (x[1]["entry"]["t"], x[1]["entry"]["k"])):
    e = cfg["entry"]; ln = lines[(e["k"], e["t"])]
    form, _, st = ln.partition(" ")
    for m in months:
        caps = agg.get(f"{st}@{form}", {}).get(m)
        empty = not caps
        if empty:
            caps = {"0": (0, 0.0), "3": (0, 0.0)}; nozero.append((h, m))
        if (ns.run, h, m) in r_have:
            continue
        ext = {"group": "f2", "sub": "B", "src": "closes", "cap": "0 (free)", "cell_line": ln}
        if empty:
            ext["empty"] = "нет сделок в closes"
        ext["n_cap3"], ext["pnl_usd_cap3"] = caps["3"]
        res_new.append({"run_id": ns.run, "cell_id": h, "month": m, "trades": caps["0"][0], "pnl_usd": caps["0"][1], "ext": ext})
    if h not in cells_have:
        cells_new.append({"id": h, "logic_version": "bounce-v1", "form": json.dumps(cfg.get("variant"), sort_keys=True, ensure_ascii=False), "set_name": "f2/Г-102",
                          "params": {"group": "f2", "source": "closes portfolio-sim v171c, drop TRUMP/TRX/BCH", "cfg_json": json.dumps(ch.normalize(cfg), sort_keys=True, ensure_ascii=False)}})
    if (ns.run, h) not in rc_have:
        rc_new.append({"run_id": ns.run, "cell_id": h, "hyp_id": cfg["hyp"]})
tot = collections.defaultdict(lambda: [0, 0.0])
for r in res_new:
    tot[r["month"]][0] += r["trades"]; tot[r["month"]][1] += r["pnl_usd"]
print(f"Г-102 B: хешей в плане {len(plan)}, в сводке {len(agg)}, клеток новых {len(cells_new)}, run_cells {len(rc_new)}, результатов {len(res_new)}, пустых {len(nozero)}")
for m, (n, s) in sorted(tot.items()):
    print(f"LOAD {m} cap0 n={n} sum_usd={s:.4f}")
if not ns.dry:
    for fn, rows in (("cells.jsonl", cells_new), ("run_cells.jsonl", rc_new), ("results.jsonl", res_new)):
        with open(os.path.join(db.REGD, fn), "a", encoding="utf-8", newline="\n") as f:
            for d in rows:
                f.write(json.dumps(d, ensure_ascii=False) + "\n")
