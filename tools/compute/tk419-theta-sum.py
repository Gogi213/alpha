#!/usr/bin/env python3
"""TK-419 вар. 6, сводка: разрыв Δ = доля принятых среди T − среди P, п.п., по ячейкам (θ, правило, знак); кластерный бутстрап по инструменто-суткам.
Правило вердикта протокола: «подтверждён» — интервал (Бонф. 12) целиком > +10 п.п.; «T≈P» — интервал содержит 0 и целиком в ±10; иначе «не различимо».
Использование: tk419-theta-sum.py <theta-X.csv> <выход.csv> [B=10000] > сводка.md"""
import csv, sys
from collections import defaultdict
import numpy as np

src, outcsv = sys.argv[1:3]
NB = int(sys.argv[3]) if len(sys.argv) > 3 else 10000
rng = np.random.default_rng(4196)
rows = list(csv.DictReader(open(src, newline="", encoding="utf-8")))
fl = lambda x: float(x) if x != "" else None
for r in rows:
    sb, ib, dz = fl(r["surgeB"]), fl(r["imbB"]), fl(r["ob_dz"])
    B = sb is not None and ib is not None and sb >= 3 and ib >= .1
    O1 = dz is not None and dz <= 1 / 3
    r["acc"] = {"B": B, "O1": O1, "O4": B and O1}
keys = sorted({(r["инструмент"], r["t_событие"][:10]) for r in rows})
kid = {k: i for i, k in enumerate(keys)}
cnt = rng.multinomial(len(keys), [1 / len(keys)] * len(keys), size=NB).astype(np.int32)
a = 5.0 / 12
print("# TK-419 вар. 6: разрыв T−P при эталоне θ 6/7 %% (%s)\n" % src)
print("| θ | правило | знак | T/P событий | инстр. (≥5 T и ≥5 P) | доля принятых T / P, % (медиана по инстр.) | Δ п.п. | 95% | 95% Бонф.(12) | ячейка |")
print("|---|---|---|---|---|---|---|---|---|---|")
out = []
for th in ("6", "7"):
    for rule in ("B", "O1", "O4"):
        for sg in (1, -1):
            d = defaultdict(lambda: {"T": [], "P": []})
            for r in rows:
                c = r["cls" + th]
                if int(r["знак"]) == sg and c in "TP":
                    d[r["инструмент"]][c].append((int(r["acc"][rule]), kid[(r["инструмент"], r["t_событие"][:10])]))
            nT = sum(len(v["T"]) for v in d.values()); nP = sum(len(v["P"]) for v in d.values())
            ins = [k for k, v in d.items() if len(v["T"]) >= 5 and len(v["P"]) >= 5]
            if len(ins) < 3:
                print("| %s | %s | %+d | %d/%d | %d | | | | | мало данных |" % (th, rule, sg, nT, nP, len(ins)))
                out.append(dict(theta=th, правило=rule, знак=sg, nT=nT, nP=nP, инстр=len(ins), ячейка="мало данных"))
                continue
            sh = lambda L: np.mean([x[0] for x in L])
            pt = np.median([sh(d[k]["T"]) - sh(d[k]["P"]) for k in ins]) * 100
            mT = np.median([sh(d[k]["T"]) for k in ins]) * 100; mP = np.median([sh(d[k]["P"]) for k in ins]) * 100
            ds = []
            for k in ins:
                s = []
                for c in ("T", "P"):
                    L = d[k][c]
                    idx = np.array([x[1] for x in L]); ac = np.array([x[0] for x in L], dtype=bool)
                    den = cnt[:, idx].sum(1).astype(float)
                    num = cnt[:, idx[ac]].sum(1).astype(float) if ac.any() else np.zeros(NB)
                    s.append(np.where(den > 0, num / np.maximum(den, 1), np.nan))
                ds.append(s[0] - s[1])
            bt = np.nanmedian(np.stack(ds, 1), 1) * 100
            bt = bt[~np.isnan(bt)]
            lo, hi = np.percentile(bt, [2.5, 97.5]); lb, hb = np.percentile(bt, [a / 2, 100 - a / 2])
            if lb > 10: cell = "подтверждён"
            elif lb <= 0 <= hb and lb >= -10 and hb <= 10: cell = "T≈P"
            else: cell = "не различимо"
            print("| %s | %s | %+d | %d/%d | %d | %.1f / %.1f | %.1f | %.1f…%.1f | %.1f…%.1f | %s |" % (th, rule, sg, nT, nP, len(ins), mT, mP, pt, lo, hi, lb, hb, cell))
            out.append(dict(theta=th, правило=rule, знак=sg, nT=nT, nP=nP, инстр=len(ins), долиT=mT, долиP=mP, delta=pt, lo=lo, hi=hi, lo_b=lb, hi_b=hb, ячейка=cell))
with open(outcsv, "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, sorted({k for o in out for k in o}, key=lambda x: list(out[0]).index(x) if x in out[0] else 99)); w.writeheader(); w.writerows(out)
n = [o["ячейка"] for o in out]
print("\nИтог: подтверждён %d, T≈P %d, не различимо %d, мало данных %d из %d" % (n.count("подтверждён"), n.count("T≈P"), n.count("не различимо"), n.count("мало данных"), len(n)))
