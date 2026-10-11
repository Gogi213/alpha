#!/usr/bin/env python3
"""TK-063 (С2 по монете, пик позиций g93): строки rounds нормированных деревьев vn-b для B1, g92/g93/e119 -> один csv.
Запуск на сервере счёта юнитом alsched: p12-coin-rows.py /data/p12r2 /data/p12r2/coin-rows.csv"""
import csv, glob, os, sys
O, out = sys.argv[1], sys.argv[2]
B1 = "ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800"
keep = lambda f: f == B1 or f.startswith(B1 + "-pyre") or f.startswith(B1 + "-pynw") or f.startswith(B1 + "-conv")
n = 0
with open(out + ".tmp", "w", encoding="utf-8", newline="") as fh:
    w = csv.writer(fh, lineterminator="\n")
    w.writerow(["month", "form", "symbol", "t0_ms", "exit_ms", "qty", "entry_vwap", "net_bps"])
    for f in sorted(glob.glob(f"{O}/vn-b/20*/20*/*/rounds.csv")):
        mo = f.split("/vn-b/")[1][:7]
        with open(f, encoding="utf-8") as ih:
            for r in csv.DictReader(l for l in ih if not l.startswith("#")):
                if keep(r["form"]):
                    w.writerow([mo, r["form"][len(B1):] or "B1", r["symbol"], int(r["t0_ns"]) // 1000000, int(r["exit_ns"]) // 1000000,
                                r["qty"], r["entry_vwap"], r["net_bps"]])
                    n += 1
os.replace(out + ".tmp", out)
open(out + ".done", "w").write(str(n))
