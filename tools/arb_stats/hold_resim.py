"""Пересчёт исполнимости ТОЛЬКО на оставшихся после фильтров эпизодах (hold_filter: keep): позиции убранных «фальшивых» эпизодов не должны занимать пару.

Тот же ряд пары и тот же hold_scan.simulate (одна позиция на пару и настройку, потолок удержания 1 ч), но по строкам выбранного подмножества.
Запуск: python3 hold_resim.py <flagged.pkl> <книги (с parsed/)> <выход.pkl> [PAR] [marks.pkl,marks-old.pkl]; подмножества: keep — остаток по правилу «молчание по сырым записям», keep_p — остаток при буквальном правиле «цена ноги не менялась > 5 с».
Если даны marks — к каждой настройке исполнения добавляются <выход>_<d>_fund (фандинг за удержание, bps) и <выход>_<d>_npay (число выплат).
Выход: {"keep": DataFrame, "keep_p": DataFrame} — строки подмножества с пересчитанными столбцами <выход>_<d>_<поле> (индекс как в таблице).
"""
import pickle
import sys
import time
import zlib
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import core  # noqa: E402
import hold_scan as hs  # noqa: E402
import maker_run  # noqa: E402
from hold_common import CAP_MS, CENTRE_MIN, DAYS, unit_of  # noqa: E402

G = {}
SUBSETS = ("keep", "keep_p")


def series(A, B, t1):
    T, S, Bu, ia, ib = core.pair_series(A, B, t1)
    valid = ((T - A["e"][ia]) <= CAP_MS) & ((T - B["e"][ib]) <= CAP_MS)
    mins, _, sb, bb = core.minute_bars(T[valid], S[valid], Bu[valid])
    m0, cen = core.rolling_centre(mins, sb, bb, CENTRE_MIN, CENTRE_MIN // 2)
    return T, S, Bu, (S + Bu) / 2, cen[np.clip(T // 60000 - m0, 0, len(cen) - 1)]


def work_pair(key):
    coin, a, b = key
    g = G
    books = {}
    for ex in (a, b):
        sym, mult, meta = g["coins"][coin][ex]
        books[ex] = maker_run.load_book(g["bwork"], ex, sym, mult, unit_of(ex, meta))
    T, S, Bu, M, cT = series(books[a], books[b], g["t1"])
    cache = {}
    res = {}
    sub = g["df"].loc[g["idx"][key]]
    for name in SUBSETS:
        s = sub[sub[name]]
        for (th, view), grp in s.groupby(["th", "view"]):
            eps = grp.reindex(columns=list(hs.COLS)).to_numpy()
            o = np.argsort(eps[:, hs.CI["t"]], kind="stable")
            eps, ids = eps[o], grp.index.to_numpy()[o]
            for d in hs.DELAYS:
                for kind in hs.KINDS:
                    seed = zlib.crc32(f"{coin}|{a}|{b}|{th}|{view}|{d}|{kind}".encode())
                    arr = hs.simulate(books[a], books[b], T, S, Bu, M, cT, g["t1"], th, view, eps, d, kind, cache, t_lo=max(g["t0"], int(T[0])), seed=seed)
                    res[(name, th, view, d, kind)] = (ids, arr)
    return res


def main(flagp, bwork, out, par=5, marks="-"):
    from hold_common import build_coins
    tab = pickle.load(open(flagp, "rb"))
    df = tab["df"]
    coins, _ = build_coins(str(Path(bwork, "parsed")), DAYS)
    need = df[df["keep"] | df["keep_p"]].groupby(["coin", "exA", "exB"]).indices
    G.update({"bwork": str(Path(bwork, "parsed")), "t0": tab["t0"], "t1": tab["t1"], "coins": coins, "df": df, "idx": df.groupby(["coin", "exA", "exB"]).indices})
    keys = [k for k in need]
    t = time.time()
    cols = {}
    with Pool(par, maxtasksperchild=4) as p:
        for k, res in enumerate(p.imap_unordered(work_pair, keys, chunksize=1)):
            for (name, th, view, d, kind), (ids, arr) in res.items():
                for ci, c in enumerate(hs.SIM_COLS):
                    cols.setdefault((name, f"{kind}_{d // 1000}_{c}"), []).append(pd.Series(arr[:, ci], index=ids))
            if k % 50 == 0:
                print(k, len(keys), round(time.time() - t), flush=True)
    outd = {}
    for name in SUBSETS:
        part = df[df[name]].copy()
        for (nm, col), lst in cols.items():
            if nm == name:
                part[col] = pd.concat(lst)
        outd[name] = part
    if marks != "-":
        from hold_filter import funding_over_hold, load_marks
        mk = load_marks(marks)
        for name, part in outd.items():
            for kind in hs.KINDS:
                for d in hs.DELAYS:
                    pre = f"{kind}_{d // 1000}_"
                    part[pre + "fund"], part[pre + "npay"] = funding_over_hold(part, mk, part[pre + "tx"].to_numpy(), part[pre + "te"].to_numpy())
    pickle.dump(outd, open(out, "wb"), protocol=4)
    print({n: len(v) for n, v in outd.items()}, round(time.time() - t))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]) if len(sys.argv) > 4 else 5, sys.argv[5] if len(sys.argv) > 5 else "-")
