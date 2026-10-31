#!/usr/bin/env python3
"""TK-461: знак Санчо-ячеек против контролей C1 и C2 (P-TK461 §7; выход TK-470, сводка tk461-c12-report.sh).
Ячейка БУ/ОТ3-5 -- по части «проверка» (§8), остальные -- по всему периоду, как основная таблица. Колонки d1/d2 = Δ против C1/C2,
p_h1/p_h2 -- Холм (K=163923), y1/y2 -- min Δ по годам с n>=8. Строка «против C1 и C2» = обе Δ>0, Холм<0,05, знак во всех годах.
python tk461_c12_verdict.py <c12-rep-dir> <main.csv> <out.csv>"""
import sys
import pandas as pd

rd, main_csv, out = sys.argv[1:4]
K = ["tf", "ev", "dir", "stop", "take", "par", "be_r", "ot", "otp"]


def norm(x):
    for k in ("par", "be_r", "otp"):
        x[k] = pd.to_numeric(x[k], errors="coerce").round(9)
    for k in ("ot", "stop", "take"):
        x[k] = x[k].astype(str).replace("nan", "")
    return x


def load(c, part):
    cl = norm(pd.read_csv(f"{rd}/{c}-{part}-cells.csv"))
    y = norm(pd.read_csv(f"{rd}/{c}-{part}-years.csv"))
    ym = y[y.n >= 8].groupby(K, dropna=False).delta.min().rename("ymin").reset_index()
    cl = cl.merge(ym, on=K, how="left")
    return cl[K + ["delta", "p_holm", "ymin"]].rename(columns={"delta": f"d{c[1]}", "p_holm": f"p_h{c[1]}", "ymin": f"y{c[1]}"})


m = pd.read_csv(main_csv)
m = m[m.family == "Санчо"].copy()
m = norm(m)
m["bu_ot"] = m.be_r.notna() | m.ot.isin(["OT3", "OT4", "OT5"])
res = []
for bu, part in ((False, "all"), (True, "check")):
    s = m[m.bu_ot == bu]
    for c in ("c1", "c2"):
        s = s.merge(load(c, part), on=K, how="left")
    res.append(s)
r = pd.concat(res, ignore_index=True)
r["cx_holm_yr"] = (r.p_holm < 0.05) & (r.delta > 0) & (r.ymin > 0)
for c in ("1", "2"):
    r[f"ok{c}"] = (r[f"p_h{c}"] < 0.05) & (r[f"d{c}"] > 0) & (r[f"y{c}"] > 0)
r["all3"] = r.cx_holm_yr & r.ok1 & r.ok2
r.to_csv(out, index=False, float_format="%.4f")
sel = r[r.cx_holm_yr]
print("Санчо вариантов", len(r), "| C-X Холм+годы", len(sel), "| +C1", int(sel.ok1.sum()), "| +C2", int(sel.ok2.sum()),
      "| все три", int(sel.all3.sum()), "| без данных C1/C2", int(sel.d1.isna().sum()), int(sel.d2.isna().sum()))
print("net>0 среди всех трёх:", int(((sel.all3) & (sel.base > 0)).sum()) if "base" in sel else "base нет в csv")
