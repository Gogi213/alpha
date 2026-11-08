#!/usr/bin/env python3
"""TK-440: экзамен янв–июль. Вердикты, принятые на сент. (v11 vol: pump-v11-sept-r; v9b-признаки: pump-v11-v9b-features),
по месяцам отдельно: тот же знак медианы r и p<0,05 (сырое, без Холма; бутстреп по монетам, B=3000, сид 433).
  vol:  tk440-exam.py vol <vol-каталог> <sept-r.csv> <выход.csv>   (файлы vol-ММ-D{D}{p|m}.csv)
  feat: tk440-exam.py feat <каталог features-2026-ММ.csv> <v9b.csv> <выход.csv>"""
import csv, glob, importlib.util, os, sys
import numpy as np, pandas as pd

H = os.path.dirname(os.path.abspath(__file__))
def mod(name, f):
    s = importlib.util.spec_from_file_location(name, os.path.join(H, f)); m = importlib.util.module_from_spec(s); s.loader.exec_module(m); return m
MONTHS = ["01", "02", "03", "04", "05", "06", "07"]

def vol(vdir, ref, out):
    an = mod("an", "tk433-v11-analyze.py")
    R = pd.read_csv(ref); R = R[R["вердикт"].str.startswith("подтверждено")]
    res = {}
    for M in MONTHS:
        rng = np.random.default_rng(an.SEED)
        for D in (90, 180, 360, 720, 1440):
            for sg, n in (("+", "p"), ("-", "m")):
                f = os.path.join(vdir, f"vol-{M}-D{D}{n}.csv")
                if not os.path.exists(f): continue
                coins, Rr, vs, dur = an.load(f)
                for (v, p), (N, m, se, pv) in an.stats(coins, Rr, rng).items():
                    res[(M, D, sg, v, p)] = (N, m, pv)
    rows = []
    for _, r in R.iterrows():
        row = dict(D=r["D"], знак=r["знак"], мера=r["мера"], положение=r["положение"], мед_сент=r["мед_r"])
        k = 0
        for M in MONTHS:
            N, m, pv = res.get((M, r["D"], r["знак"], r["мера"], r["положение"]), (0, np.nan, np.nan))
            ok = (not np.isnan(pv)) and pv < 0.05 and np.sign(m) == np.sign(r["мед_r"])
            row[f"N_{M}"] = N; row[f"мед_{M}"] = m; row[f"p_{M}"] = pv; row[f"ок_{M}"] = int(ok); k += ok
        row["месяцев_из_7"] = k; rows.append(row)
    pd.DataFrame(rows).to_csv(out, index=False, float_format="%.4g")

def feat(fdir, ref, out):
    v9 = mod("v9", "tk433-v11-v9b.py"); an = v9.an
    R = pd.read_csv(ref); R = R[R["вердикт"] == "подтверждено"]
    from scipy.stats import norm
    res = {}
    for M in MONTHS:
        d = pd.read_csv(os.path.join(fdir, f"features-2026-{M}.csv"))
        T = v9.rows_for(d, np.random.default_rng(an.SEED), True)
        for _, t in T.iterrows():
            se = t.get("se"); m = t.get("мед")
            pv = 2 * norm.sf(abs(m) / se) if se and se > 0 and not np.isnan(m) else np.nan
            res[(M, t["D"], t["знак"], t["признак"])] = (t["N"], m, pv)
    rows = []
    for _, r in R.iterrows():
        row = dict(D=r["D"], знак=r["знак"], признак=r["признак"], мед_сент=r["мед"]); k = 0
        for M in MONTHS:
            N, m, pv = res.get((M, r["D"], r["знак"], r["признак"]), (0, np.nan, np.nan))
            ok = (not np.isnan(pv)) and pv < 0.05 and np.sign(m) == np.sign(r["мед"])
            row[f"N_{M}"] = N; row[f"мед_{M}"] = m; row[f"p_{M}"] = pv; row[f"ок_{M}"] = int(ok); k += ok
        row["месяцев_из_7"] = k; rows.append(row)
    pd.DataFrame(rows).to_csv(out, index=False, float_format="%.4g")

if __name__ == "__main__":
    {"vol": vol, "feat": feat}[sys.argv[1]](*sys.argv[2:5])
