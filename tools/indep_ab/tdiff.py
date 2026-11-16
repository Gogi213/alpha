#!/usr/bin/env python3
"""BingX: does the exchange time field T of the SAME trade id differ between independent connections? Per same-set pair of connections:
share of ids with T_i != T_j, dT = T_i - T_j, dK = kernel arrival difference, dL = dK - dT (the part added after the exchange stamped T),
corr(dT, dK). Usage (niced on srv): tdiff.py --from <epoch> --to <epoch>"""
import argparse, os, sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import analyze as A


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="t_from", type=float, required=True)
    ap.add_argument("--to", dest="t_to", type=float, required=True)
    a = ap.parse_args()
    ind = A.load_ind_bingx(a.t_from, a.t_to)
    ind = ind[(ind["k"] >= (a.t_from + 120) * 10**9) & (ind["k"] <= (a.t_to - 120) * 10**9)]
    print("set | pair | n ids | share T_i!=T_j | dT p5/p50/p95 | dK (arrival) p5/p50/p95 | dL=dK-dT p5/p50/p95 | corr(dT,dK) | share |dK|>20 ms | of those share |dT|>20 ms")
    for s in ("s03", "f00"):
        g = ind[ind["set"] == s]
        wT = g.pivot_table(index=["sym", "id"], columns="conn", values="T", aggfunc="min")
        wK = g.pivot_table(index=["sym", "id"], columns="conn", values="k", aggfunc="min")
        names = sorted(wT.columns)
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                m = wT[[names[i], names[j]]].dropna().index
                dT = (wT.loc[m, names[i]] - wT.loc[m, names[j]]).to_numpy(dtype=float)
                dK = ((wK.loc[m, names[i]] - wK.loc[m, names[j]]) / 1e6).to_numpy(dtype=float)
                dL = dK - dT
                big = np.abs(dK) > 20
                f = lambda v: "/".join(f"{x:.1f}" for x in np.percentile(v, [5, 50, 95]))
                print(f"{s} | {names[i]}+{names[j]} | {len(m)} | {float((dT != 0).mean()):.3f} | {f(dT)} | {f(dK)} | {f(dL)} | {float(np.corrcoef(dT, dK)[0, 1]):.3f} | {float(big.mean()):.3f} | {float((np.abs(dT[big]) > 20).mean()) if big.any() else float('nan'):.3f}")


if __name__ == "__main__":
    main()
