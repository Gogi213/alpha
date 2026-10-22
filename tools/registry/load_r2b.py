#!/usr/bin/env python3
"""TK-115 / В-224: готовые closes R2-B (31 клетка П-12, psim v171c, янв–сен) -> cells / run_cells / results по каноническому хешу плана.
Без пересчёта (В-213). Клетка = хеш из плана P-14 F1, у которого r2b_covers; конфиг клетки кладётся в params.cfg_json (cellhash_reg.config_of
берёт его как есть), строка месяца — из master.MONTHLY / LINES (те же числа, что archive/tk115-r2b-day.sh). Числа — сводка closes
(`sum_closes_r2b.py`): trades = n cap 0, pnl_usd = Σ$ cap 0, ext.n_cap3 / pnl_usd_cap3 = B2.
  python tools/registry/load_r2b.py <closes-r2b-sum.csv> --run ID [--dry]"""
import argparse, csv, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cellhash as ch, db, master

ap = argparse.ArgumentParser(); ap.add_argument("csv"); ap.add_argument("--run", required=True); ap.add_argument("--dry", action="store_true"); ap.add_argument("--thr", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data", "thr7"))
ns = ap.parse_args()
c = master.connect()
rules = master.rules_of(c, "P-14")
grids, _ = master.build_grid(c, "P-14", 1, None, rules)
c.close()
r2b = master.r2b_lines()
cov = {}
for hyp, cells in grids.items():
    for av, cfg in cells:
        h = ch.cell_hash(cfg)
        if h not in cov and master.r2b_covers(h, r2b):
            cov[h] = (hyp, ch.normalize(cfg))
# Г-116 (cxl<W>q<Q_m>) в сетке P-14 нет: конфиг — по образцу Г-112 (те же оси W, q, форма B1), семья e116; хеш может не совпасть с будущим планом
# Г-116 — тогда лишний пересчёт, не ложный пропуск (cellhash-canonical п.4). Пороги месяцев — thr7 e-thresholds-<ГГГГ-ММ>.csv (--thr).
import copy
if len(cov) != 19:
    sys.exit(f"ожидали 19 хешей R2-B в плане P-14, нашли {len(cov)}")
fam116 = master.family_of("Г-116")
if fam116 != "e116":
    sys.exit(f"семья Г-116 = {fam116}, ожидали e116")
e116 = {}
for h, (hyp, cfg) in list(cov.items()):
    if hyp == "Г-112":
        c2 = copy.deepcopy(cfg); c2["variant"]["family"] = "e116"
        h2 = ch.cell_hash(c2)
        assert h2 not in cov
        e116[h2] = ("Г-116", ch.normalize(c2))
cov.update(e116)
THR = {}
for m in master.R2B_MONTHS:
    THR[m] = {r[0]: r[1] for r in csv.reader(open(os.path.join(ns.thr, f"e-thresholds-{m}.csv"), encoding="utf-8")) if len(r) == 2}
agg = {}
for r in csv.DictReader(open(ns.csv, encoding="utf-8")):
    agg.setdefault(r["variant"], {}).setdefault(r["month"], {})[r["cap"]] = (int(r["n"]), float(r["sum_usd"]))
cells_have = {d["id"] for d in db.jl("cells.jsonl")}
rc_have = {(d["run_id"], d["cell_id"]) for d in db.jl("run_cells.jsonl")}
r_have = {(d["run_id"], d["cell_id"], d["month"]) for d in db.jl("results.jsonl")}
cells_new, rc_new, res_new = [], [], []
for h, (hyp, cfg) in sorted(cov.items(), key=lambda x: (x[1][0], x[0])):
    assert ch.cell_hash(cfg) == h
    for m in master.R2B_MONTHS:
        if hyp == "Г-116":
            ax = cfg["variant"]["axes"]; w, q = ax["W"], ax["q"]
            v = THR[m].get(f"e116_W{w}_q{q}") or (THR[m].get(f"e116_q{q}") if w == 30 else None)
            if v is None:
                sys.exit(f"нет порога e116_W{w}_q{q} {m}")
            ln = f"ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800-cxl{w}q{float(v)!r} t-bid-btc4h-q1"
        else:
            ln = master.MONTHLY[h][m] if h in master.MONTHLY else master.LINES[h][0]
        form, _, st = ln.partition(" ")
        caps = agg.get(f"{st}@{form}", {}).get(m)
        if not caps or "0" not in caps:
            sys.exit(f"нет closes: {h} {hyp} {m} {st}@{form}")
        if (ns.run, h, m) in r_have:
            continue
        n0, s0 = caps["0"]
        ext = {"group": "r2b", "src": "closes", "cap": "0 (free)", "cell_line": ln}
        if "3" in caps:
            ext["n_cap3"], ext["pnl_usd_cap3"] = caps["3"]
        res_new.append({"run_id": ns.run, "cell_id": h, "month": m, "trades": n0, "pnl_usd": s0, "ext": ext})
    if h not in cells_have:
        cells_new.append({"id": h, "logic_version": "bounce-v1", "form": "ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800 " + json.dumps(cfg.get("variant"), sort_keys=True, ensure_ascii=False), "set_name": "r2b/" + hyp,
                          "params": {"group": "r2b", "source": "closes portfolio-sim v171c, drop TRUMP/TRX/BCH", "cfg_json": json.dumps(cfg, sort_keys=True, ensure_ascii=False)}})
    if (ns.run, h) not in rc_have:
        rc_new.append({"run_id": ns.run, "cell_id": h, "hyp_id": hyp})
print("клеток", len(cells_new), "run_cells", len(rc_new), "результатов", len(res_new))
if not ns.dry:
    for fn, rows in (("cells.jsonl", cells_new), ("run_cells.jsonl", rc_new), ("results.jsonl", res_new)):
        with open(os.path.join(db.REGD, fn), "a", encoding="utf-8", newline="\n") as f:
            for d in rows:
                f.write(json.dumps(d, ensure_ascii=False) + "\n")
