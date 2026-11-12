#!/usr/bin/env python3
"""TK-355 (G-015 E/L/Z): собрать busy-replay-rounds (после busy-replay.py без --keep) всех конфигов tk355-e.sh в один CSV (cfg,...). На calc: python3 g015-elz-collect.py /data/tk0355/e > elz-rounds.csv"""
import csv, glob, os, sys
R = sys.argv[1]
w = csv.writer(sys.stdout, lineterminator="\n")
w.writerow("cfg symbol day_utc dir entry_px entry_vwap exit_px qty net_bps reason fill_frac legs_filled legs_rejected signal_index".split())
for line in open(os.path.join(R, "configs.txt")):
    c = line.split()[0]
    for f in sorted(glob.glob(f"{R}/{c}/busy/*/t-bid-btc4h-q1/rounds.csv")):
        rows = [l for l in open(f, encoding="utf-8") if not l.startswith("#")]
        for r in csv.DictReader(rows):
            w.writerow([c, r["symbol"], r["day_utc"], r["dir"], r["entry_px"], r["entry_vwap"], r["exit_px"], r["qty"],
                        r["net_bps"], r["reason"], r["fill_frac"], r["legs_filled"], r["legs_rejected"], r["signal_index"]])
