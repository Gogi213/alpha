#!/usr/bin/env python3
"""TK-194 / В-224: 14 клеток первой волны R2 П-12 (e119 conv ×3, e114 halfstop/halflevel ×2, e117 tsl ×9) из готовых closes
/data/p12r2/closes-{vn,vtn}-cap{0,3}-<мес>.json (равная экспозиция, пул v171b, без --drop, как стоит принятый Шарп R2-A) -> cells / run_cells / results.
Без пересчёта (В-213). Клетка — как у TK-183: form = полная форма, set_name «r2/t-bid-btc4h-q1»; канонический хеш — cellhash_reg map.
trades = n cap 0, pnl_usd = Σ$ cap 0, ext.n_cap3 / pnl_usd_cap3 = B2.
  python tools/registry/load_r2a14.py <каталог с closes-*> --run ID [--dry]"""
import argparse, hashlib, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db

ap = argparse.ArgumentParser(); ap.add_argument("dir"); ap.add_argument("--run", required=True); ap.add_argument("--dry", action="store_true")
ns = ap.parse_args()
B1 = "ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800"
B3 = "ladder3x0..0.0409sw2-pct2-1to1-14400-ttl1800"
WANT = {"vn": [B1 + s for s in ("-conv0a20", "-conv1a20", "-conv2a20", "-halfstop", "-halflevel")],
        "vtn": [f"{B3}-tsl{a}t{b}" for a in (5, 10, 20) for b in (1, 2, 4)]}
agg = {}
for kind, forms in WANT.items():
    for m in range(1, 10):
        mo = f"2026-{m:02d}"
        for cap in (0, 3):
            j = json.load(open(os.path.join(ns.dir, f"closes-{kind}-cap{cap}-{mo}.json"), encoding="utf-8"))
            byf = {k.split("@", 1)[-1]: v for k, v in j.items() if k.startswith("t-bid-btc4h-q1@")}
            for f in forms:
                tr = [x for per in byf[f].values() for x in per.values()]
                tr = [t for l in tr for t in l]
                agg.setdefault(f, {}).setdefault(mo, {})[cap] = (len(tr), sum(t[1] for t in tr))
cells = {d["id"] for d in db.jl("cells.jsonl")}
rc = {(d["run_id"], d["cell_id"]) for d in db.jl("run_cells.jsonl")}
rr = {(d["run_id"], d["cell_id"], d["month"]) for d in db.jl("results.jsonl")}
nc, nrc, nres = [], [], []
for f, ms in agg.items():
    cell = {"logic_version": "bounce-v1", "form": f, "set_name": "r2/t-bid-btc4h-q1"}
    cid = hashlib.sha256(json.dumps(cell, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
    hyp = "Г-119" if "conv" in f else "Г-117" if "tsl" in f else "Г-114"
    if cid not in cells:
        nc.append({"id": cid, **cell})
    if (ns.run, cid) not in rc:
        nrc.append({"run_id": ns.run, "cell_id": cid, "hyp_id": hyp})
    for mo, caps in sorted(ms.items()):
        if (ns.run, cid, mo) in rr:
            continue
        ext = {"group": "r2", "src": "closes", "cap": "0 (free)", "n_cap3": caps[3][0], "pnl_usd_cap3": caps[3][1]}
        nres.append({"run_id": ns.run, "cell_id": cid, "month": mo, "trades": caps[0][0], "pnl_usd": caps[0][1], "ext": ext})
print("клеток", len(nc), "run_cells", len(nrc), "результатов", len(nres))
if not ns.dry:
    for fn, rows in (("cells.jsonl", nc), ("run_cells.jsonl", nrc), ("results.jsonl", nres)):
        with open(os.path.join(db.REGD, fn), "a", encoding="utf-8", newline="\n") as fh:
            for d in rows:
                fh.write(json.dumps(d, ensure_ascii=False) + "\n")
