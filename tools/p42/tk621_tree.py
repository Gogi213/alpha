#!/usr/bin/env python3
"""TK-621: window tree for the honest universe (symlinks to published files), same geometry as tools/p42/tk599_prep.sh.
  python3 tk621_tree.py <wa|wb|wc> <symbols.txt> <out_root>
Per symbol and day: the file of the TK-599 tree of the same window if present (identical bytes to the old runs), else the file
downloaded by tk621_dl.py (/data/p42-tk621/dl). Prints the differences against the old tree for the old symbols."""
import datetime
import os
import sys

OLD, DL = "/data/p42-tk599", "/data/p42-tk621/dl"
WIN = {"wa": ("2026-06-17", "2026-08-29"), "wb": ("2026-07-25", "2026-10-06"), "wc": ("2026-09-01", "2026-11-13")}
name, symf, root = sys.argv[1:4]
d0, d1 = (datetime.date.fromisoformat(x) for x in WIN[name])
days = [(d0 + datetime.timedelta(i)).isoformat() for i in range((d1 - d0).days + 1)]
syms = [x for x in open(symf, encoding="utf-8").read().replace("\n", ",").split(",") if x]
W = f"{root}/{name}"
nk = nt = nfund = 0
extra_old = []
for s in syms:
    os.makedirs(f"{W}/klines/{s}", exist_ok=True)
    os.makedirs(f"{W}/aggtrades/{s}", exist_ok=True)
    for d in days:
        k_old, k_new = f"{OLD}/{name}/klines/{s}/{d}.csv", f"{DL}/klines-csv/{s}/{d}.csv"
        t_name = f"{s}-aggTrades-{d}.zip"
        t_old, t_new = f"{OLD}/{name}/aggtrades/{s}/{t_name}", f"{DL}/aggtrades/{s}/{t_name}"
        for old, new, dst in ((k_old, k_new, f"{W}/klines/{s}/{d}.csv"), (t_old, t_new, f"{W}/aggtrades/{s}/{t_name}")):
            src = old if os.path.exists(old) and os.path.getsize(old) > 0 else (new if os.path.exists(new) and os.path.getsize(new) > 0 else None)
            if src is None:
                continue
            if src is new and os.path.dirname(old) and os.path.isdir(os.path.dirname(old)):
                extra_old.append(dst.split(W)[1])
            os.symlink(os.path.realpath(src), dst)
            if dst.endswith(".csv"):
                nk += 1
            else:
                nt += 1
    f = f"{OLD}/{name}/klines/{s}/funding.csv"
    if os.path.exists(f):
        os.symlink(os.path.realpath(f), f"{W}/klines/{s}/funding.csv")
        nfund += 1
print(f"{name}: symbols {len(syms)} days {len(days)} klines {nk} trades {nt} funding {nfund}")
print(f"{name}: files of OLD symbols taken from the new download instead of the old tree: {len(extra_old)}", extra_old[:4])
