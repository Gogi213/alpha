#!/usr/bin/env python3
"""TK-268 / В-224: Г-147 (signal=touch, одна клетка P-14 F1) — closes psim-launch.sh (v171c, drop TRUMP/TRX/BCH, cap 0 и 3) -> cells / run_cells / results.
Клетка считалась отдельным заходом `--signal touch` (кэш касаний D20, в --cells не кодируется), поэтому load_f1.py её пропускает (nocode). Без пересчёта (В-213).
Числа: trades = n cap 0, pnl_usd = Σ$ cap 0, ext.n_cap3 / pnl_usd_cap3 = B2; месяц без closes — 0 сделок (psim пишет только варианты со сделками), ext.empty.
  python tools/registry/load_g147.py <каталог psim> --run ID [--dry]   (psim: closes-p14f1-cap{0,3}-<ГГГГ-ММ>.json)"""
import argparse, glob, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cellhash as ch, db, master

ap = argparse.ArgumentParser(); ap.add_argument("psim"); ap.add_argument("--run", required=True); ap.add_argument("--dry", action="store_true")
ns = ap.parse_args()
c = master.connect()
grids, _ = master.build_grid(c, "P-14", 1, ["Г-147"], master.rules_of(c, "P-14"))
c.close()
cells = [cfg for cl in grids.values() for _, cfg in cl]
assert len(cells) == 1, len(cells)
cfg = ch.normalize(cells[0]); h = ch.cell_hash(cells[0]); assert ch.cell_hash(cfg) == h
months = [f"2026-{m:02d}" for m in range(1, 10)]
agg = {}
for m in months:
    for cap in (0, 3):
        f = os.path.join(ns.psim, f"closes-p14f1-cap{cap}-{m}.json")
        if not os.path.exists(f):
            sys.exit(f"нет {os.path.basename(f)}: psim не дошёл")
        for v, per in json.load(open(f, encoding="utf-8")).items():
            for mo, caps in per.items():
                for cp, tr in caps.items():
                    agg.setdefault(mo, {}).setdefault(str(cp), {}).setdefault(v, []).extend(tr)
variants = {v for mo in agg.values() for cp in mo.values() for v in cp}
assert len(variants) <= 1, variants   # в заходе одна клетка
cells_have = {d["id"] for d in db.jl("cells.jsonl")}
rc_have = {(d["run_id"], d["cell_id"]) for d in db.jl("run_cells.jsonl")}
r_have = {(d["run_id"], d["cell_id"], d["month"]) for d in db.jl("results.jsonl")}
res_new = []
for m in months:
    caps = {}
    for cp in ("0", "3"):
        tr = [x for v in agg.get(m, {}).get(cp, {}).values() for x in v]
        caps[cp] = (len(tr), round(sum(x[1] for x in tr), 4))
    empty = caps["0"][0] == 0 and caps["3"][0] == 0
    if (ns.run, h, m) in r_have:
        continue
    ext = {"group": "g147", "src": "closes", "cap": "0 (free)", "signal": "touch", "n_cap3": caps["3"][0], "pnl_usd_cap3": caps["3"][1]}
    if empty:
        ext["empty"] = "нет сделок в closes"
    res_new.append({"run_id": ns.run, "cell_id": h, "month": m, "trades": caps["0"][0], "pnl_usd": caps["0"][1], "ext": ext})
cells_new = [] if h in cells_have else [{"id": h, "logic_version": "bounce-v1", "form": json.dumps(cfg.get("variant"), sort_keys=True, ensure_ascii=False), "set_name": "f1/Г-147",
    "params": {"group": "f1", "source": "closes portfolio-sim v171c, drop TRUMP/TRX/BCH; --signal touch", "cfg_json": json.dumps(cfg, sort_keys=True, ensure_ascii=False)}}]
rc_new = [] if (ns.run, h) in rc_have else [{"run_id": ns.run, "cell_id": h, "hyp_id": "Г-147"}]
print(f"hash {h}: клеток новых {len(cells_new)}, run_cells {len(rc_new)}, результатов {len(res_new)}, пустых {sum('empty' in r['ext'] for r in res_new)}")
for r in res_new:
    print(f"LOAD {r['month']} n={r['trades']} sum_usd={r['pnl_usd']} | cap3 n={r['ext']['n_cap3']} sum_usd={r['ext']['pnl_usd_cap3']}")
if not ns.dry:
    for fn, rows in (("cells.jsonl", cells_new), ("run_cells.jsonl", rc_new), ("results.jsonl", res_new)):
        with open(os.path.join(db.REGD, fn), "a", encoding="utf-8", newline="\n") as f:
            for d in rows:
                f.write(json.dumps(d, ensure_ascii=False) + "\n")
