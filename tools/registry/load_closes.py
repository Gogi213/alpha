#!/usr/bin/env python3
"""TK-182: готовые closes portfolio-sim (R1/ext П-12, пул v171c) -> cells/run_cells/results. Без пересчёта (В-213).
closes-sum.csv: src,cell,month,cap,n,sum_usd (сводка closes-cap{0,3}-<мес>.json). results: trades = n (cap 0),
pnl_usd = Σ$ cap 0 (free), ext.pnl_usd_cap3 / n_cap3 = B2. Использование: load_closes.py <csv> --run ID."""
import argparse, collections, csv, hashlib, json, os, re, sys
sys.path.insert(0, os.path.dirname(__file__))
import db

ap = argparse.ArgumentParser(); ap.add_argument("csv"); ap.add_argument("--run", required=True)
ns = ap.parse_args()
agg = collections.defaultdict(dict)
for r in csv.DictReader(open(ns.csv, encoding="utf-8")):
    agg[(r["src"], r["cell"])].setdefault(r["month"], {})[r["cap"]] = (int(r["n"]), float(r["sum_usd"]))
cells = {d["id"] for d in db.jl("cells.jsonl")}
have_rc = {(d["run_id"], d["cell_id"]) for d in db.jl("run_cells.jsonl")}
have_r = {(d["run_id"], d["cell_id"], d["month"]) for d in db.jl("results.jsonl")}
op = lambda n: open(os.path.join(db.REGD, n), "a", encoding="utf-8", newline="\n")
nc = nr = 0
with op("cells.jsonl") as fc, op("run_cells.jsonl") as fr, op("results.jsonl") as fo:
    for (src, name), ms in sorted(agg.items()):
        if src == "ext" and name == "B1":
            continue
        group = "p12-" + src
        cell = {"logic_version": "bounce-v1", "form": name, "set_name": group + "/" + name,
                "params": {"group": group, "source": "closes portfolio-sim v171c, drop TRUMP/TRX/BCH"}}
        cid = hashlib.sha256(json.dumps(cell, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
        m = re.match(r"g(\d+)-", name)
        hyp = "Г-%02d" % int(m.group(1)) if m else ("Г-85б" if name == "B1" else None)
        if cid not in cells:
            cells.add(cid); fc.write(json.dumps({"id": cid, **cell}, ensure_ascii=False) + "\n")
        if (ns.run, cid) not in have_rc:
            have_rc.add((ns.run, cid))
            fr.write(json.dumps({"run_id": ns.run, "cell_id": cid, "hyp_id": hyp}, ensure_ascii=False) + "\n")
        for mo, caps in sorted(ms.items()):
            if (ns.run, cid, mo) in have_r or "0" not in caps:
                continue
            have_r.add((ns.run, cid, mo))
            n0, s0 = caps["0"]
            ext = {"group": group, "src": "closes", "cap": "0 (free)"}
            if "3" in caps:
                ext["n_cap3"], ext["pnl_usd_cap3"] = caps["3"]
            fo.write(json.dumps({"run_id": ns.run, "cell_id": cid, "month": mo, "trades": n0, "pnl_usd": s0,
                                 "ext": ext}, ensure_ascii=False) + "\n")
            nr += 1
        nc += 1
print("клеток", nc, "результатов", nr)
