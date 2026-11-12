#!/usr/bin/env python3
"""TK-393: фильтр свопов Ekubo по пулам STRK (ключ token0-token1-fee-tick_spacing-ext) -> out.csv + счёт по пулу.
usage: POOLS=<пул,пул,...> tk393-starknet-filter.py <swaps.csv> <out.csv>"""
import csv, sys, os
src, dst = sys.argv[1], sys.argv[2]
want = {}
for p in os.environ["POOLS"].strip().split(","):
    a, b, f, ts, e = p.split("-")
    want[(int(a, 16), int(b, 16), int(f), int(ts), int(e, 16))] = p
cnt = {}
with open(src) as fi, open(dst, "w", newline="") as fo:
    r = csv.reader(fi); h = next(r); w = csv.writer(fo); w.writerow(h + ["pool"])
    for x in r:
        k = (int(x[2], 16), int(x[3], 16), int(x[4], 16), int(x[5]), int(x[6], 16))
        p = want.get(k)
        if p:
            w.writerow(x + [p]); cnt[p] = cnt.get(p, 0) + 1
for p, n in cnt.items(): print(n, p[-60:])
print("total", sum(cnt.values()), "pools_hit", len(cnt), "of", len(want))
