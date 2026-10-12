#!/usr/bin/env python3
"""Сверка closure_mech.net_bps с net_bps rounds.csv по каждому reason (месяц целиком): check_netbps.py <мес> [s-v]. Печатает reason, число кругов, max|diff| для ноги выхода тейкер / мейкер."""
import csv, glob, os, sys, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import closure_mech as cm

mon = sys.argv[1]; sv = sys.argv[2] if len(sys.argv) > 2 else cm.S_V
st = collections.defaultdict(lambda: {True: [0, 0.0], False: [0, 0.0]})
for f in sorted(glob.glob(f"{sv}/{mon}/*/t-bid-btc4h-q1/rounds.csv")):
    rows = [l for l in open(f, encoding="utf-8") if not l.startswith("#")]
    for r in csv.DictReader(rows):
        if r["reason"] == "":
            continue
        for tk in (True, False):
            d = abs(cm.net_bps(int(r["dir"]), float(r["entry_vwap"]), float(r["exit_px"]), tk) - float(r["net_bps"]))
            s = st[r["reason"]][tk]; s[0] += 1; s[1] = max(s[1], d)
print("reason\tкругов\tmax|diff| тейкер\tmax|diff| мейкер")
for k, v in sorted(st.items()):
    print(f"{k}\t{v[True][0]}\t{v[True][1]:.6f}\t{v[False][1]:.6f}")
