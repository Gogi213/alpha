#!/usr/bin/env python3
"""TK-461 D2b: ячейки и годы из d2b-out/*.csv.gz (выход ядра: суммы c,d,b,k,d2 по (sym, ключ ячейки, день)).
Ключ ячейки = tf, ev, dir, stop, take, par, be_r, ot, otp (side однозначно по ev×dir). Интервал по дням UTC (кластер день, как tk461_d2a),
по годам, Холм одной семьёй размера KFAM (P-TK461 §12.1 п.7: 377 + K_TK461 <= 163923; двусторонний p ~ 2K односторонних), ячейки с c=0 пропущены.
python tk461_d2b_report.py <d2b_dir> <out_cells.csv> <out_years.csv> [процессы] [маска] [KFAM]"""
import glob
import hashlib
import os
import sys
from math import erfc, sqrt
from multiprocessing import Pool

import numpy as np
import pandas as pd

KEY = ["tf", "ev", "dir", "stop", "take", "par", "be_r", "ot", "otp"]
SUMS = ["c", "d", "b", "k", "d2"]


def strcol(s):
    """astype(str).replace("nan","") на уникальных значениях, не на всех строках (TK-469: 8 с на файл BTC-60 -> 0,2 с); результат тот же."""
    codes, uniq = pd.factorize(s, use_na_sentinel=False)
    u = pd.Series(uniq).astype(str).replace("nan", "")
    return pd.Series(u.to_numpy()[codes], index=s.index)


FAST = os.environ.get("TK469_REPORT_FAST") == "1"
PART = os.environ.get("TK461_PART", "")  # P-TK461 §8: check | xa | xb | open; пусто = все строки (прежнее поведение)
_D2024 = 19723  # 2024-01-01 UTC, сутки с эпохи


def part_mask(x):
    """§8: открытие = чётные монеты x <=2023; проверка = нечётные x >=2024; xa = чётные x >=2024; xb = нечётные x <=2023 (md5(символ) чётный как tk461_open)."""
    ev = x.sym.map({s: int(hashlib.md5(s.encode()).hexdigest(), 16) % 2 == 0 for s in x.sym.unique()})
    late = x.day >= _D2024
    return {"open": ev & ~late, "check": ~ev & late, "xa": ev & late, "xb": ~ev & ~late}[PART]




def codes_of(x, cols):
    """Составной int64-ключ строк по sorted-кодам колонок (порядок = лексикографический, как у groupby по самим колонкам) -> (ключ, [(коды, число значений)])."""
    key = np.zeros(len(x), dtype=np.int64)
    for c in cols:
        cd, un = pd.factorize(x[c], sort=True, use_na_sentinel=False)
        key = key * len(un) + cd
    return key


def one_fast(f):
    """TK-469: тот же результат, что one(), без groupby/drop_duplicates по 10 строковым колонкам: группы и пары (sym, ключ) -- по одному int64 (в 4,2 млн строк BTC-60 groupby KEY+day 9,5 с, strcol 3 с, read_csv 15 с)."""
    x = pd.read_csv(f, low_memory=False)
    if PART:
        x = x[part_mask(x)]
    if not len(x):
        return None
    for k in ("be_r", "ot", "otp", "par"):
        x[k] = strcol(x[k])
    cols = KEY + ["day"]
    card = 1
    for c in cols:
        card *= max(x[c].nunique(dropna=False), 1)
    if card >= 2 ** 62:
        return one_slow(x)
    comp = codes_of(x, cols)
    uq, first = np.unique(comp, return_index=True)
    s = x[SUMS].groupby(comp, sort=True).sum()
    g = x[cols].iloc[first].reset_index(drop=True)
    for c in SUMS:
        g[c] = s[c].to_numpy()
    sk = codes_of(x, KEY + ["sym", "side"])
    _, sf = np.unique(sk, return_index=True)
    sy = x[KEY + ["sym", "side"]].iloc[np.sort(sf)]
    return g, sy


def one_slow(x):
    g = x.groupby(KEY + ["day"], observed=True)[SUMS].sum().reset_index()
    sy = x[KEY + ["sym", "side"]].drop_duplicates()
    return g, sy


def one(f):
    if FAST:
        return one_fast(f)
    x = pd.read_csv(f, low_memory=False)
    if PART:
        x = x[part_mask(x)]
    if not len(x):
        return None
    for k in ("be_r", "ot", "otp", "par"):
        x[k] = strcol(x[k])
    return one_slow(x)


def main(sd, oc, oy, nproc="8", pat="*.csv.gz", kfam="163923"):
    jobs = sorted(glob.glob(os.path.join(sd, pat)))
    D = S = None
    buf, sbuf = [], []
    with Pool(int(nproc)) as p:
        for i, r in enumerate(p.imap_unordered(one, jobs, chunksize=1)):
            if r is not None:
                buf.append(r[0])
                sbuf.append(r[1])
            if len(buf) >= 12 or i == len(jobs) - 1:
                if buf:
                    X = pd.concat(([D] if D is not None else []) + buf, ignore_index=True)
                    D = X.groupby(KEY + ["day"], observed=True)[SUMS].sum().reset_index()
                    S = pd.concat(([S] if S is not None else []) + sbuf, ignore_index=True).drop_duplicates()
                    buf, sbuf = [], []
                    print("files", i + 1, "of", len(jobs), flush=True)
    D = D[D.c > 0]
    nsym = S.groupby(KEY, observed=True).sym.nunique().rename("syms")
    side = S.groupby(KEY, observed=True).side.first().rename("side")
    D["year"] = pd.to_datetime(D.day.astype(np.int64) * 86400000, unit="ms").dt.year
    D["sc"] = D.d * D.c
    D["c2"] = D.c.astype(np.float64) ** 2
    g = D.groupby(KEY, observed=True)
    A = g[["c", "d", "b", "k", "d2", "sc", "c2"]].sum()
    A["days"] = g.size()
    A = A.join(nsym).join(side)
    n = A.c
    m = A.d / n
    ss_ = A.d2 - 2 * m * A.sc + m ** 2 * A.c2
    se = np.sqrt(np.maximum(ss_, 0)) / n
    z = (m / se.replace(0, np.nan)).abs()
    p = z.map(lambda v: erfc(v / sqrt(2)) if v == v else 1.0)
    cells = pd.DataFrame({"side": A.side, "n": n.astype(int), "syms": nsym.reindex(A.index), "days": A.days,
                          "base": A.b / n, "ctrl": A.k / n, "delta": m, "lo": m - 1.96 * se, "hi": m + 1.96 * se, "p": p,
                          "small": ((n < 8) | (nsym.reindex(A.index) < 5)).astype(int)}).reset_index()
    K = len(cells)
    Kf = max(int(kfam), K)
    srt = cells.sort_values("p", kind="stable")
    adj = np.minimum(1.0, np.maximum.accumulate(srt.p.values * (Kf - np.arange(K))))
    cells.loc[srt.index, "p_holm"] = adj
    Yr = D.groupby(KEY + ["year"], observed=True)[["c", "d", "b"]].sum().reset_index()
    years = Yr[KEY + ["year"]].assign(n=Yr.c, delta=Yr.d / Yr.c, base=Yr.b / Yr.c)
    cells.to_csv(oc, index=False)
    years.to_csv(oy, index=False)
    print("cells", K, "Kfam", Kf, "events-pairs", int(n.sum()))


if __name__ == "__main__":
    main(*sys.argv[1:])
