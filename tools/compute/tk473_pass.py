#!/usr/bin/env python3
"""TK-473 (P-TK473): проходы по d2b-out. Столбцы файла: sym,tf,ev,dir,day,side,stop,take,par,be_r,ot,otp,c,d,b,k,d2 (b net события, k net C-X, d = b - k).
python tk473_pass.py p1 <src> <in.csv.gz> <out.csv.gz>                      -> (key,sym,part O|P) c,b   (сутки <= 2025-12-31; 2026 не читается в выход)
python tk473_pass.py p2 <src> <in.csv.gz> <cand.csv> <outdir_prefix>       -> prefix.pday.csv.gz (кандидаты, P: key,day c,b),
    prefix.hkey.csv.gz (ВСЕ конфиги, H: key c,b,d,k), prefix.hsym.csv.gz (кандидаты, H: key,sym,month c,b,d,k), prefix.hday.csv.gz (кандидаты, H: key,day c,b,d,k)
H-файлы (hkey/hsym/hday) не открываются до фиксации winners.csv (P-TK473 §4)."""
import hashlib
import sys

import pandas as pd

KEY = ["tf", "ev", "dir", "stop", "take", "par", "be_r", "ot", "otp"]
D2024, D2026 = 19723, 20454


def load(f, cols):
    x = pd.read_csv(f, usecols=["sym"] + KEY + ["day"] + cols, low_memory=False,
                    dtype={"stop": "category", "take": "category", "ot": "category", "otp": "category", "ev": "category", "dir": "category"})
    x = x[~x.ev.astype(str).str.startswith("C-")]
    for k in ("par", "be_r", "ot", "otp", "stop", "take", "ev", "dir"):
        x[k] = x[k].astype(str).replace("nan", "")
    x["tf"] = x.tf.astype(str)
    return x


def even(sym):
    return int(hashlib.md5(sym.encode()).hexdigest(), 16) % 2 == 0


def main(mode, src, f, *a):
    if mode == "p1":
        x = load(f, ["c", "b"])
        x = x[x.day < D2026]
        if not len(x):
            return
        x["part"] = ["O" if even(s) and d < D2024 else "P" for s, d in zip(x.sym, x.day)]
        x["src"] = src
        x.groupby(["src"] + KEY + ["sym", "part"], sort=False)[["c", "b"]].sum().reset_index().to_csv(a[0], index=False)
    else:
        cand, pre = a
        x = load(f, ["c", "b", "d", "k"])
        if not len(x):
            return
        x["src"] = src
        cd = pd.read_csv(cand, dtype=str, keep_default_na=False, na_values=[])
        cd = cd[cd.src == src].drop(columns="src").assign(_c=1)
        h = x[x.day >= D2026]
        if len(h):
            h.groupby(KEY, sort=False)[["c", "b", "d", "k"]].sum().reset_index().assign(src=src).to_csv(pre + ".hkey.csv.gz", index=False)
        xc = x.merge(cd, on=KEY, how="left")
        xc = xc[xc._c.notna()]
        p = xc[xc.day < D2026]
        if len(p):
            p.groupby(KEY + ["day"], sort=False)[["c", "b"]].sum().reset_index().assign(src=src).to_csv(pre + ".pday.csv.gz", index=False)
        h = xc[xc.day >= D2026].copy()
        if len(h):
            h["month"] = pd.to_datetime(h.day.astype("int64") * 86400000, unit="ms").dt.month
            h.groupby(KEY + ["sym", "month"], sort=False)[["c", "b", "d", "k"]].sum().reset_index().assign(src=src).to_csv(pre + ".hsym.csv.gz", index=False)
            h.groupby(KEY + ["day"], sort=False)[["c", "b", "d", "k"]].sum().reset_index().assign(src=src).to_csv(pre + ".hday.csv.gz", index=False)


if __name__ == "__main__":
    main(*sys.argv[1:])
