#!/usr/bin/env python3
"""TK-621: intervals for the universe runs. Day = unit (136 days), circular blocks of 4 days, 20 000 resamples, seed as §13.24.1 п. 2
(retitr_full.own_stat / delta_stat). Per universe and side: mean per trade [5 %; 95 %] and one-sided p (H0 mean <= 0); paired delta
against a reference tag (sum and mean per trade, 5 % lower bound). Descriptive only ("found on this data"), no verdict rule.
  python3 tk621_stat.py <root> <ref_tag> <tag> [<tag> ...]      (needs numpy and retitr_full.py next to this file or on PYTHONPATH)"""
import csv
import datetime as D
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np  # noqa: E402
import retitr_full as rf  # noqa: E402

WIN = (("wa", "2026-06-17", "2026-07-01", "2026-08-25"), ("wb", "2026-07-25", "2026-08-26", "2026-10-02"), ("wc", "2026-09-01", "2026-10-03", "2026-11-13"))
SIDE_OF = {"L_base": "long", "S_base": "short"}
D0 = D.date(2026, 7, 1)


def day_arrays(root, tag, side):
    S, N = np.zeros(rf.NDAYS), np.zeros(rf.NDAYS)
    label = "L_base" if side == "long" else "S_base"
    for w, t0, lo, hi in WIN:
        t0d = D.date.fromisoformat(t0)
        for r in csv.DictReader(open(f"{root}/out/{tag}/{w}/{label}/{side}-trades.csv")):
            day = t0d + D.timedelta(days=int(r["entry_ms"]) // 86_400_000)
            if D.date.fromisoformat(lo) <= day <= D.date.fromisoformat(hi):
                i = (day - D0).days
                S[i] += float(r["gross"]) - float(r["fees"])
                N[i] += 1
    return S, N


def main():
    root, ref, tags = sys.argv[1], sys.argv[2], sys.argv[3:]
    for side in ("long", "short"):
        Sr, Nr = day_arrays(root, ref, side)
        o = rf.own_stat(Sr, Nr)
        print(f"{side:5} ref {ref:12} n={int(Nr.sum()):4d} S={Sr.sum():8.2f} mean={o.mean:6.3f} [{o.lower:6.3f}; {o.upper:6.3f}] p(mean<=0)={o.p:.4f}")
        for tag in tags:
            S, N = day_arrays(root, tag, side)
            o = rf.own_stat(S, N)
            d = rf.delta_stat(S, N, Sr, Nr)
            print(f"{side:5} {tag:16} n={int(N.sum()):4d} S={S.sum():8.2f} mean={o.mean:6.3f} [{o.lower:6.3f}; {o.upper:6.3f}] p(mean<=0)={o.p:.4f} | "
                  f"vs {ref}: dS={d.dsum:+8.2f} (SE {d.se_sum:.1f}), d_mean={d.d:+.3f} (SE {d.se:.3f}, 5% lower {d.lower:+.3f})")


main()
