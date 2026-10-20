#!/usr/bin/env python3
"""TK-324: вариант (в) тариф В-47 для 17 клеток спота TK-313 -> run / run_cells / results Летописи. Клетки (хеши) уже в cells.jsonl (tk313/<имя>).
Без пересчёта: data/tk324-idx3.json (в) и idx2.json (б). tk324-load-registry.py --run R-20261020-002 [--dry]"""
import argparse, json, os
ap = argparse.ArgumentParser(); ap.add_argument("--run", required=True); ap.add_argument("--dry", action="store_true"); ns = ap.parse_args()
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."); R = os.path.join(ROOT, "docs", "registry")
ids = {}
for l in open(os.path.join(R, "cells.jsonl"), encoding="utf-8"):
    d = json.loads(l)
    if d["set_name"].startswith("tk313/"): ids[d["set_name"][6:]] = d["id"]
V = json.load(open(os.path.join(ROOT, "data", "tk324-idx3.json"), encoding="utf-8"))["cells"]
B = json.load(open(os.path.join(ROOT, "data", "tk324-idx2.json"), encoding="utf-8"))["cells"]
run = {"id": ns.run, "ts": "2026-10-20T13:20:00+04:00", "ticket": "TK-324", "protocol": "P-21", "kind": "research",
       "what": "TK-324: спот B1 на тарифе владельца В-47 (maker 5,625 / taker 6,75 bps на ногу) — пересчёт 17 клеток TK-313 из готовых rounds, без нового прохода",
       "hyp": "Г-03", "data_pool": "v171-spot", "period": "2026-01-01..2026-09-30", "machine": "calc", "result_path": "/data/tk0313",
       "outcome": "ни одна из 16 клеток не проходит П-21 §8 (≥6/9 мес.>0): S0 Σ −1516 $, 3/9 мес., лучшая g-s100-u3 −4,6 $; не подтверждено ≠ опровергнуто",
       "judge": "judge", "status": "боевой",
       "note": "результаты: docs/findings/tk313-spot-tariff-v47-2026-10-20.md, data/tk324-idx{2,3,4}.json; pnl_usd = (в); ext.sum_usd_b = (б) 10/10; tools/compute/tk324-load-registry.py"}
rcs, res = [], []
for n in sorted(V):
    h = ids[n]; rcs.append({"run_id": ns.run, "cell_id": h, "hyp_id": "Г-03"})
    for m, r in sorted(V[n]["months"].items()):
        res.append({"run_id": ns.run, "cell_id": h, "month": m, "trades": r["n"], "pnl_usd": r["sum_b"],
                    "ext": {"group": "p21-spot-v47", "cell": n, "sum_usd_b_public": B[n]["months"][m]["sum_b"], "sum_usd_a_futfees": r["sum_a"],
                            "sr_day_v47": r["sr_day"], "days_with_trade": r["days_with_trade"], "note": "pnl_usd = (в) тариф В-47"}})
print("клеток", len(rcs), "results", len(res))
if not ns.dry:
    for fn, rows in (("runs.jsonl", [run]), ("run_cells.jsonl", rcs), ("results.jsonl", res)):
        with open(os.path.join(R, fn), "a", encoding="utf-8", newline="\n") as f:
            for d in rows: f.write(json.dumps(d, ensure_ascii=False) + "\n")
