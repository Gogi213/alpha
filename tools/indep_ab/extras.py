#!/usr/bin/env python3
"""Extras for the final report: (1) silent gaps > 1 s per independent BingX connection, (2) distribution of min(pair of independent connections) - prod
per trade id for every same-set pair (p1..p99, share within +-5 ms / earlier than -10 ms), same window as analyze.py.
Usage (niced on srv): extras.py --from <epoch> --to <epoch>"""
import argparse, datetime, glob, os, sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import analyze as A


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="t_from", type=float, required=True)
    ap.add_argument("--to", dest="t_to", type=float, required=True)
    a = ap.parse_args()
    ind = A.load_ind_bingx(a.t_from, a.t_to)
    print("## silent gaps > 1 s per connection (kernel arrival stamps of the connection's own rows)")
    for c, g in ind.groupby("conn"):
        k = np.sort(g["k"].to_numpy())
        d = np.diff(k) / 1e9
        for i in np.where(d > 1.0)[0]:
            print(f"{c} gap {d[i]:.1f} s from {datetime.datetime.fromtimestamp(k[i] / 1e9, datetime.timezone.utc).strftime('%H:%M:%S')}")
    symsets = {n: [l.strip() for l in open(p) if l.strip()] for n, p in A.SETS.items()}
    allsyms = set(sum(symsets.values(), []))
    files = A.prod_files(A.BINGX_PROD, ["trade0"], a.t_from, a.t_to, A.days_between(a.t_from, a.t_to), allsyms)
    rows = []
    for f in files:
        rows += A.decode_prod(f, allsyms, "t", int(a.t_from) * 10**9, int(a.t_to) * 10**9)
    prod = pd.DataFrame(rows, columns=["kind", "rx", "sym", "id", "lag_e", "et"]).sort_values("rx").drop_duplicates(["sym", "id"], keep="first")
    prod["set"] = np.where(prod["sym"].isin(symsets["s03"]), "s03", "f00")
    print("\n## min(pair of independent connections) - prod, per trade id, ms")
    print("set | pair | n | p1 | p5 | p25 | p50 | p75 | p95 | p99 | share d<-10 | share |d|<=5 | share d>10")
    for s in ("s03", "f00"):
        gi = ind[(ind["set"] == s) & (ind["k"] >= (a.t_from + 120) * 10**9) & (ind["k"] <= (a.t_to - 120) * 10**9)]
        pr = prod[prod["set"] == s].set_index(["sym", "id"])["rx"]
        wide = gi.pivot_table(index=["sym", "id"], columns="conn", values="k", aggfunc="min")
        names = sorted(wide.columns)
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                x = wide[[names[i], names[j]]].dropna().join(pr, how="inner")
                d = (x[[names[i], names[j]]].min(axis=1) - x["rx"]) / 1e6
                q = np.percentile(d, [1, 5, 25, 50, 75, 95, 99])
                print(f"{s} | {names[i]}+{names[j]} | {len(d)} | " + " | ".join(f"{v:.2f}" for v in q) + f" | {float((d < -10).mean()):.4f} | {float((d.abs() <= 5).mean()):.4f} | {float((d > 10).mean()):.4f}")


if __name__ == "__main__":
    main()
