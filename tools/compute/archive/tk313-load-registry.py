#!/usr/bin/env python3
"""TK-313: 17 клеток спота П-21 (S0, решётка сила×usd_min, ctl-nn) × янв–сен -> cells / run_cells / results Летописи.
Без пересчёта (В-213): bycell.csv (n, Σ$ б = спот-издержки, Σ$ а = фьюч-издержки) + spot-monthly.json (SR сут. для 5 клеток).
Хеш = cellhash.cell_hash(cfg), вселенная `v171-spot`; конфиг B1 = F1-клетка Г-95 с w=2 и usd_min/min_flow_pct по клетке.
  tk313-load-registry.py --run R-20261020-001 [--dry]"""
import argparse, csv, json, os, sys
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, os.path.join(ROOT, "tools", "registry"))
import cellhash as ch
ap = argparse.ArgumentParser(); ap.add_argument("--run", required=True); ap.add_argument("--dry", action="store_true"); ns = ap.parse_args()
F = os.path.join(ROOT, "docs", "findings"); R = os.path.join(ROOT, "docs", "registry")
U = {"1": 30943, "2": 107397, "3": 376218}   # П-21 §10 п.2
base = {"logic": "bounce-v1", "deadline_s": 14400, "ttl": 1800, "early_s": None, "exit": None,
        "entry": {"kind": "ladder", "n": 3, "lo": 0, "hi": 0.0409, "unit": "sigma", "w": 2},
        "stop": {"kind": "pct_bps", "v": 200}, "take": {"kind": "trail", "a": 1, "b": 1}, "sigma": "study/sigma240",
        "exec": {"approach_bps": 20, "busy_skip": "off", "cancel_ns": 3980000, "exit_group": "on", "h3": "Notional", "h3_usd": 10000,
                 "hold_step": "skip", "p95_cancel_ns": 4550000, "p95_place_ns": 4790000, "p95_taker_ns": 6420000, "place_ns": 4200000,
                 "queue": "prob:3", "taker_ns": 5650000, "touches_from": "approaches/D20", "verdict_csv": "final3/verdict.csv"},
        "universe": "v171-spot", "variant": None}
def cfg_of(name):
    st = {"side": "bid", "min_age_s": 2700, "ctx": ["btc4h_max", -44.55], "signal": "approach", "driver": "setups", "lot": "usd:500x1",
          "entry_ttl": "60+300+1800", "post_only": True, "band_exit_bps": 20, "frontrun_only": False, "usd_min": None, "min_flow_pct": None}
    if name == "s0": st["usd_min"] = 10000
    elif name != "ctl-nn":
        _, s, u = name.split("-"); s, u = s[1:], u[1:]
        if s != "n": st["min_flow_pct"] = int(s)
        if u != "n": st["usd_min"] = U[u]
    return dict(base, set=st)
by = {}
for r in csv.DictReader(open(os.path.join(F, "tk313-spot-b1-bycell-2026-10-20.csv"), encoding="utf-8")):
    by.setdefault(r["cell"], {})[r["month"]] = r
mon = json.load(open(os.path.join(F, "tk313-spot-monthly-2026-10-20.json"), encoding="utf-8"))["cells"]
cells, rcs, res = [], [], []
for name in sorted(by):
    cfg = ch.normalize(cfg_of(name)); h = ch.cell_hash(cfg)
    cells.append({"id": h, "logic_version": "bounce-v1", "form": "null", "set_name": "tk313/" + name,
                  "params": {"group": "p21-spot", "source": "TK-313 bounce-grid спот v171-spot, янв–сен", "cfg_json": json.dumps(cfg, sort_keys=True, ensure_ascii=False)}})
    rcs.append({"run_id": ns.run, "cell_id": h, "hyp_id": "Г-03"})
    for m in sorted(by[name]):
        r = by[name][m]
        ext = {"group": "p21-spot", "cell": name, "sum_usd_a_futfees": float(r["sum_usd_futfees"]), "days": int(r["days"]),
               "note": "pnl_usd = (б) спот-издержки 10/10 bps; ext.sum_usd_a = фьюч-издержки"}
        mm = mon.get(name, {}).get("months", {}).get(m)
        if mm:
            ext["sr_day_spot"] = mm["spot"]["sr_day"]; ext["common_days_n_spot"] = mm["spot"]["n"]; ext["common_days_sum_a_spot"] = mm["spot"]["sum_a"]
            ext["fut_analog"] = mon[name]["fut_key"]; ext["common_days_fut"] = mm["fut"]
        res.append({"run_id": ns.run, "cell_id": h, "month": m, "trades": int(r["n"]), "pnl_usd": float(r["sum_usd_spotfees"]), "ext": ext})
print("клеток", len(cells), "уникальных хешей", len({c["id"] for c in cells}), "results", len(res))
if not ns.dry:
    for fn, rows in (("cells.jsonl", cells), ("run_cells.jsonl", rcs), ("results.jsonl", res)):
        with open(os.path.join(R, fn), "a", encoding="utf-8", newline="\n") as f:
            for d in rows: f.write(json.dumps(d, ensure_ascii=False) + "\n")
