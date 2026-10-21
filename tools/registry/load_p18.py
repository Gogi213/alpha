#!/usr/bin/env python3
"""TK-289 / В-224: готовые closes F1 П-18 (титровка B1, оси 1–5, 6а, 6б, 6в (6v), 7, 8; psim v171c, янв–сен) -> cells / run_cells / results по каноническому хешу.
Как load_p17.py (без пересчёта, В-213), но клетки не из master.build_grid, а из p18_scope (хеш + конфиг оси) и p18_plan (имя/форма строки --cells).
Строка сводки closes — variant `<имя>-<мес>@<форма>` (имя p18a<ось>v<значение>), числа: trades = n cap 0, pnl_usd = Σ$ cap 0, ext.n_cap3 / pnl_usd_cap3 = B2.
Ось -> hyp_id: `<префикс><ось>` (--hyp-prefix, умолч. «П18-»); оси 5 ttl 300–1200 лежат в Летописи (Г-129) и в сводке их нет — берутся только оси со строками в сводке.
  python tools/registry/load_p18.py <closes-sum.csv> --run ID [--hyp-prefix П18-] [--dry]"""
import argparse, csv, json, os, re, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "compute"))
import cellhash as ch, db, p18_plan as pl, p18_scope as sc

ap = argparse.ArgumentParser(); ap.add_argument("csv"); ap.add_argument("--run", required=True); ap.add_argument("--dry", action="store_true")
ap.add_argument("--hyp-prefix", default="П18-")
ns = ap.parse_args()
months = pl.MONTHS
agg = {}
for r in csv.DictReader(open(ns.csv, encoding="utf-8")):
    agg.setdefault(r["variant"], {}).setdefault(r["month"], {})[r["cap"]] = (int(r["n"]), float(r["sum_usd"]))
cells_have = {d["id"] for d in db.jl("cells.jsonl")}
rc_have = {(d["run_id"], d["cell_id"]) for d in db.jl("run_cells.jsonl")}
r_have = {(d["run_id"], d["cell_id"], d["month"]) for d in db.jl("results.jsonl")}
cells_new, rc_new, res_new, absent, nozero, seen = [], [], [], [], [], set()
for ax in ("1", "2", "3", "4", "5", "6a", "6b", "6v", "7", "8", "stack"):
    key, vals = pl.AX[ax]
    for v in vals:
        cfg = ch.normalize(sc.cfg_of(ax, v)); h = ch.cell_hash(cfg); hyp = ns.hyp_prefix + ax
        name = "p18a%sv%s" % (ax, re.sub(r"[^0-9A-Za-z]", "_", v.replace("-", "m")))
        tup = pl.cell(ax, v, months[0], {months[0]: {v: 0}})[0]   # 6б: порог по месяцу в форму не входит — подставка
        if not any(f"{name}-{ym}@{tup}" in agg for ym in months):   # не считалась в F1 (ttl 300–1200 — Г-129)
            absent.append((ax, v)); continue
        if h in seen:
            sys.exit(f"хеш {h} дважды (ось {ax}, {v})")
        seen.add(h)
        for ym in months:
            line = f"{tup} {name}-{ym}"
            caps = agg.get(f"{name}-{ym}@{tup}", {}).get(ym)
            empty = not caps
            if empty:   # psim пишет только варианты со сделками: клетка в --cells месяца, а в closes нет = 0 сделок
                caps = {"0": (0, 0.0), "3": (0, 0.0)}; nozero.append((h, ym))
            if (ns.run, h, ym) in r_have:
                continue
            n0, s0 = caps["0"]
            ext = {"group": "p18", "src": "closes", "cap": "0 (free)", "cell_line": line, "axis": ax, "value": v}
            if empty:
                ext["empty"] = "нет сделок в closes"
            if "3" in caps:
                ext["n_cap3"], ext["pnl_usd_cap3"] = caps["3"]
            res_new.append({"run_id": ns.run, "cell_id": h, "month": ym, "trades": n0, "pnl_usd": s0, "ext": ext})
        if h not in cells_have:
            cells_new.append({"id": h, "logic_version": "bounce-v1", "form": json.dumps(cfg.get("variant"), sort_keys=True, ensure_ascii=False), "set_name": "p18/" + ax,
                              "params": {"group": "p18", "source": "closes portfolio-sim v171c, drop TRUMP/TRX/BCH", "axis": ax, "value": v, "cfg_json": json.dumps(cfg, sort_keys=True, ensure_ascii=False)}})
        if (ns.run, h) not in rc_have:
            rc_new.append({"run_id": ns.run, "cell_id": h, "hyp_id": hyp})
print("не в сводке (Летопись/не считались)", absent, "|", "клеток", len(cells_new), "run_cells", len(rc_new), "результатов", len(res_new), "пустых (0 сделок)", len(nozero))
if not ns.dry:
    for fn, rows in (("cells.jsonl", cells_new), ("run_cells.jsonl", rc_new), ("results.jsonl", res_new)):
        with open(os.path.join(db.REGD, fn), "a", encoding="utf-8", newline="\n") as f:
            for d in rows:
                f.write(json.dumps(d, ensure_ascii=False) + "\n")
