#!/usr/bin/env python3
"""TK-440 шаг 6: вердикты TK-438 (доли, конец) по месяцам янв–июль. «ок» = знак разности как в сент. и p<0,05 (сырое).
Использование: tk440-scen-exam.py <каталог scen с sMM-shares.csv/aMM-agg.csv> <rules.csv> <rules-end.csv> <выход-префикс>"""
import sys, os, numpy as np, pandas as pd
d, sh, en, out = sys.argv[1:5]
MS = ["01", "02", "03", "04", "05", "06", "07"]
def run(ref, key, pat, cols, fn):
    R = ref[ref["вердикт"].str.startswith("подтверждено")]
    A = {M: pd.read_csv(os.path.join(d, pat.format(M))) for M in MS}
    for M in MS: A[M]["месяц"] = A[M]["месяц"].astype(str).str.zfill(2)
    rows = []
    for _, r in R.iterrows():
        row = {c: r[c] for c in cols}; row["разн_сент"] = r["разность"]; k = 0
        for M in MS:
            a = A[M]; m = np.ones(len(a), bool)
            for c in cols: m &= (a[c].astype(str) == str(r[c])).values
            x = a[m]
            if len(x) != 1: row[f"разн_{M}"] = np.nan; row[f"p_{M}"] = np.nan; row[f"ок_{M}"] = 0; continue
            x = x.iloc[0]; ok = x["p"] < 0.05 and np.sign(x["разность"]) == np.sign(r["разность"])
            row[f"n_{M}"] = x["n_e"]; row[f"разн_{M}"] = x["разность"]; row[f"p_{M}"] = x["p"]; row[f"ок_{M}"] = int(ok); k += ok
        row["месяцев_из_7"] = k; rows.append(row)
    pd.DataFrame(rows).to_csv(fn, index=False, float_format="%.4g")
    return pd.DataFrame(rows)
sc = pd.read_csv(sh); sc["D"] = sc["D"].astype(int); sc["знак"] = sc["знак"].astype(str)
sc = sc[sc["сценарий"].str[:3].str.rstrip("_").isin(["S3","S4","S5","S7","S10"]) | sc["сценарий"].str.startswith(("S3_","S4_","S5_","S7","S10"))]
sc = sc[sc["месяц"].astype(str).str.zfill(2) == "09"]
a = run(sc, "d", "s{}-shares.csv", ["D", "знак", "сценарий"], out + "-shares.csv")
ee = pd.read_csv(en); ee = ee[ee["месяц"].astype(str).str.zfill(2) == "09"]
ee = ee[ee["сценарий"].str.startswith(("S1_","S3_","S4_","S7","S8"))]
b = run(ee, "e", "a{}-agg.csv", ["сценарий", "отсчёт", "горизонт"], out + "-end.csv")
for n, t in (("доли", a), ("конец", b)):
    print(n, len(t), t["месяцев_из_7"].value_counts().sort_index().to_dict())
    print(t.drop(columns=[c for c in t.columns if c[:2] in ("n_","ра","p_") or c.startswith("ок_")]).to_string())
