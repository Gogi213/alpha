#!/usr/bin/env python3
"""T-32: сделки главного варианта, взятые счётом (без TRX, одна позиция на монету, без потолка) → CSV."""
import csv, importlib.util, os
A = os.path.expanduser("~/alpha")
spec = importlib.util.spec_from_file_location("ps", os.path.join(A, "tmp-kpi/portfolio-sim.py"))
ps = importlib.util.module_from_spec(spec); spec.loader.exec_module(ps)
F = "ladder3x2..20w2-pct2-tr1x1-14400-ttl1800"
out = open(os.path.join(A, "tmp-t32/main-trades.csv"), "w", newline="")
w = csv.writer(out); w.writerow(["month", "sym", "t0_ms", "t1_ms", "net_bps", "usd", "pnl_usd", "entry", "dir", "fee_bps", "reason", "fill"])
for month, homes in (("aug", [("epochs/e-aug", "b5/titrc-u500r")]),
                     ("sep", [("epochs/e-archive", "b5/titrc-u500r"), ("tmp-t20/p2/rec", "b5/titrc-u500r")])):
    rows = []
    for h, r in homes:
        rows += [x for x in ps.load_rounds(os.path.join(A, h), r, "t-bid-btc4h-q1", F) if x["sym"] != "TRXUSDT"]
    rows.sort(key=lambda x: x["t0"])
    busy, n = {}, 0
    for x in rows:
        if busy.get(x["sym"], 0) > x["t0"]:
            continue
        busy[x["sym"]] = x["t1"]
        n += 1
        w.writerow([month, x["sym"], x["t0"] // 10**6, x["t1"] // 10**6, round(x["net"], 4), round(x["usd"], 4),
                    round(x["net"] / 1e4 * x["usd"], 4), x["entry"], x["dir"], round(x["fee"], 4), x["reason"], x["fill"]])
    print(month, n, "сделок")
