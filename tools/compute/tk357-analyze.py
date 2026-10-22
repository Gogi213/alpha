#!/usr/bin/env python3
"""TK-357: анализ days.csv — по суткам/монетам, топ-3, бутстрап по суткам и монетам."""
import csv, sys, random, collections
random.seed(357)
rows = list(csv.DictReader(open(sys.argv[1])))
for st in ("ge1000", "500-1000"):
    R = [r for r in rows if r["strata"] == st]
    for k, nk, lab in (("s1h", "n1h", "1h"), ("s10m", "n10m", "10m")):
        n = sum(int(r[nk]) for r in R); s = sum(float(r[k]) for r in R)
        print(f"== {st} {lab}: n={n} mean={s/n:.2f}")
        for g in ("day", "sym"):
            D = collections.defaultdict(lambda: [0, 0.0])
            for r in R:
                D[r[g]][0] += int(r[nk]); D[r[g]][1] += float(r[k])
            L = sorted(D.items(), key=lambda kv: -kv[1][1])
            units = len(L)
            pos = sum(1 for _, v in L if v[1] > 0)
            top3 = sum(v[1] for _, v in L[:3])
            # mean without top3 and without top10
            m3 = (s - top3) / (n - sum(v[0] for _, v in L[:3]))
            top10 = sum(v[1] for _, v in L[:10]); m10 = (s - top10) / (n - sum(v[0] for _, v in L[:10]))
            print(f" by {g}: units={units} pos={pos} top3={[(a, v[0], round(v[1]/v[0],1)) for a, v in L[:3]]} share_top3={top3/s:.2f} mean_ex_top3={m3:.2f} ex_top10={m10:.2f}")
            # bootstrap over units: ratio estimator
            vals = [(v[0], v[1]) for _, v in L]; B = []
            for _ in range(2000):
                smp = [random.choice(vals) for _ in vals]
                nn = sum(a for a, _ in smp); B.append(sum(b for _, b in smp) / nn)
            B.sort(); print(f"   boot {g}: [{B[50]:.1f}, {B[1949]:.1f}]")
        # day-level mean of day means
        D = collections.defaultdict(lambda: [0, 0.0])
        for r in R:
            D[r["day"]][0] += int(r[nk]); D[r["day"]][1] += float(r[k])
        dm = [v[1] / v[0] for v in D.values() if v[0]]
        print(f"   days with mean>0: {sum(1 for x in dm if x>0)}/{len(dm)}; median day mean {sorted(dm)[len(dm)//2]:.1f}")
