"""TK-451 слой 1: вердикт по заявлениям автора из l1agg.csv по правилу P-TK451 §4/§10 (связная область по соседним значениям оси).
Запуск: python sancho_l1verdict.py l1agg.csv out.csv  (1H, all84 и BTC+ETH; режимы k=2 и k=1)."""
import sys
import pandas as pd

AX = ["N", "K_I", "g", "K_B", "sc"]  # порядковые оси; k и dir/oc/подмножество — фиксируются в группе
AUTH = {"A1": 61.0, "A2": 49.2, "A3": 53.9, "A4": 66.5, "A5": 62.2, "A6": 84.1, "A7": 89.0, "A8": 91.6}
SH = {"A6": (28.2, .05), "A7": (28.8, .05), "A8": (15.0, .5)}
ANCH = dict(N=10, K_I=3, g=0.0, K_B=1, sc=1.0)

d = pd.read_csv(sys.argv[1])
d = d[(d.tf == 60) & d.claim.isin(AUTH)]
rows = []
for (sub, cl, k, dr), g in d.groupby(["subset", "claim", "k", "dir"]):
    if k not in (1, 2) or dr != "all":
        continue
    for oc, gg in g.groupby("oc"):
        ax = [a for a in AX if gg[a].nunique() > 1]
        key = lambda r: tuple(r[a] for a in AX)
        cells = {key(r): r for r in gg.to_dict("records")}
        hits = {c for c, r in cells.items() if r["hit"]}
        # связность: пара ячеек, различающихся по одной оси на соседнее значение сетки
        vals = {a: sorted(gg[a].unique()) for a in AX}
        conn = set()
        for c in hits:
            for i, a in enumerate(AX):
                v = vals[a]
                j = v.index(c[i])
                for jj in (j - 1, j + 1):
                    if 0 <= jj < len(v):
                        n = c[:i] + (v[jj],) + c[i + 1:]
                        if n in hits:
                            conn.update((c, n))
        a_cell = cells.get(tuple(ANCH[a] if ANCH[a] in vals[a] else vals[a][0] for a in AX))
        shv = gg.share.where(gg.claim.isin(SH), None)
        row = dict(subset=sub, claim=cl, k=k, oc=oc, author=AUTH[cl], cells=len(gg), hit=len(hits), conn=len(conn),
                   hit_axes=",".join(a for a in ax if len({c[AX.index(a)] for c in hits}) > 1),
                   pmin=gg.p.min(), pmax=gg.p.max())
        if a_cell:
            row.update(anchor_p=a_cell["p"], anchor_lo=a_cell["lo"], anchor_hi=a_cell["hi"], anchor_hit=a_cell["hit"],
                       anchor_n=a_cell["n"], anchor_share=a_cell["share"])
        if hits:
            b = min(hits, key=lambda c: abs(cells[c]["p"] - AUTH[cl]))
            row.update(best=dict(zip(AX, b)), best_p=cells[b]["p"], best_n=cells[b]["n"], best_share=cells[b]["share"])
        if cl in SH:
            v, t = SH[cl]
            s_ok = [c for c in hits if abs(cells[c]["share"] - v) <= t]
            row["hit_and_share"] = len(s_ok)
        row["verdict"] = ("воспроизводится" if conn else "не различимо (остров)" if hits else "не воспроизводится")
        rows.append(row)
o = pd.DataFrame(rows)
o.to_csv(sys.argv[2], index=False)
pd.set_option("display.width", 250, "display.max_columns", 30, "display.max_colwidth", 60)
print(o[["subset", "claim", "k", "oc", "author", "cells", "hit", "conn", "hit_axes", "pmin", "pmax", "anchor_p", "anchor_hit", "verdict"]].round(2).to_string())
