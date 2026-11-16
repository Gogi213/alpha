#!/usr/bin/env python3
"""Per-second lag (kernel stamps) around given hours for the independent clients and the collector rows of the same symbols.
BingX: 8 independent connections pooled vs prod rows of the two symbol sets; Binance: bookTicker / aggTrade of the 8 symbols.
Usage (niced on srv): persecond.py --from <epoch> --to <epoch> --to-binance <epoch> --hours <epoch> [<epoch>...] [--before 5 --after 10]"""
import argparse, glob, os, sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import analyze as A


def table(name, df, tcol, lagcol, H, before, after):
    print(f"  {name}")
    for s in range(-before, after):
        x = df[(df[tcol] >= H + s) & (df[tcol] < H + s + 1)]
        if len(x):
            v = x[lagcol].to_numpy()
            print(f"    {s:+3d} s n={len(x):6d} p50 {np.percentile(v, 50):7.1f} p99 {np.percentile(v, 99):7.1f} max {v.max():7.1f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="t_from", type=float, required=True)
    ap.add_argument("--to", dest="t_to", type=float, required=True)
    ap.add_argument("--to-binance", dest="t_to_bn", type=float, required=True)
    ap.add_argument("--hours", type=int, nargs="+", required=True)
    ap.add_argument("--before", type=int, default=5)
    ap.add_argument("--after", type=int, default=10)
    a = ap.parse_args()

    ind = A.load_ind_bingx(a.t_from, a.t_to)
    ind["t"] = ind["k"] / 1e9
    symsets = {n: [l.strip() for l in open(p) if l.strip()] for n, p in A.SETS.items()}
    allsyms = set(sum(symsets.values(), []))
    files = A.prod_files(A.BINGX_PROD, ["trade0"], a.t_from, a.t_to, A.days_between(a.t_from, a.t_to), allsyms)
    rows = []
    for f in files:
        rows += A.decode_prod(f, allsyms, "t", int(a.t_from) * 10**9, int(a.t_to) * 10**9)
    prod = pd.DataFrame(rows, columns=["kind", "rx", "sym", "id", "lag_e", "et"]).sort_values("rx").drop_duplicates(["sym", "id"], keep="first")
    prod["lag"] = prod["rx"] / 1e6 - ((prod["rx"] // 1_000_000) - prod["lag_e"] - prod["et"])  # rx_ms - T of the prod connection's own message
    prod["t"] = prod["rx"] / 1e9
    print("# BingX: independent (8 connections pooled, first arrival per connection) vs prod (first arrival of both lanes), symbols of both sets")
    for H in a.hours:
        print(f"== hour epoch {H}")
        table("independent", ind, "t", "lag", H, a.before, a.after)
        table("prod", prod, "t", "lag", H, a.before, a.after)
    del ind, prod

    frames = [pd.read_csv(f, sep="\t", header=None, names=["kind", "k", "u", "sym", "id", "T", "E"],
                          dtype={"k": "float64", "u": "int64", "sym": "string", "id": "int64", "T": "int64", "E": "int64"}) for f in sorted(glob.glob(f"{A.IND_BINANCE}/*.tsv"))]
    bi = pd.concat(frames, ignore_index=True)
    bi = bi[(bi["u"] >= a.t_from * 10**9) & (bi["u"] <= a.t_to_bn * 10**9)]
    bi["k"] = bi["k"].fillna(bi["u"]).astype("int64")
    bi = bi.drop_duplicates(["kind", "sym", "id"], keep="first")
    bi["lag"] = bi["k"] / 1e6 - bi["E"]
    bi["t"] = bi["k"] / 1e9
    syms = set(bi["sym"].unique())
    files = A.prod_files(A.BINANCE_PROD, ["book", "trade"], a.t_from, a.t_to_bn, A.days_between(a.t_from, a.t_to_bn), syms)
    rows = []
    for f in files:
        kind = "b" if os.path.basename(f).startswith("book") else "t"
        rows += A.decode_prod(f, syms, kind, int(a.t_from) * 10**9, int(a.t_to_bn) * 10**9)
    bp = pd.DataFrame(rows, columns=["kind", "rx", "sym", "id", "lag_e", "et"]).sort_values("rx").drop_duplicates(["kind", "sym", "id"], keep="first")
    bp["lag"] = bp["lag_e"].astype(float)
    bp["t"] = bp["rx"] / 1e9
    for kind, name in (("b", "bookTicker"), ("t", "aggTrade")):
        print(f"# Binance {name}: independent vs prod, same 8 symbols")
        for H in a.hours:
            if H + a.after > a.t_to_bn:
                continue
            print(f"== hour epoch {H}")
            table("independent", bi[bi["kind"] == kind], "t", "lag", H, a.before, a.after)
            table("prod", bp[bp["kind"] == kind], "t", "lag", H, a.before, a.after)


if __name__ == "__main__":
    main()
