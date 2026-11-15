"""Проверка отчёта arb-cross-exit-2026-11-16 (проверяющий): круги тейкер-тейкер L = 0 (и раздельные задержки входа/выхода) по всем парам без BingX,
с пометками загрязнения дырами сбора и оценкой незакрытых по цене конца окна.

Пометки круга [ts, te] (незакрытый — до t1):
  audit — пересекает участок тишины книги ≥ 5 мин любой ноги из аудита collect-gap (stretches-book.csv, REST-сверка; ± 1 мин на минутную точность);
  out   — нога Binance и круг пересекает обрыв 15.11 18:12:18–18:14:20 UTC (+ 2 мин запаса, как DEAD_MARGIN);
  age_in / age_out — возраст самой старой ноги (мс с последней СЫРОЙ записи книги) в момент входа / выхода.
Запуск: python3 cross_review.py <каталог книг (с parsed/)> <t0_мс> <t1_мс> <выход.pkl> [PAR]; DEAD — как у cross_run.py, AUDIT — каталог out/ аудита.
"""
import csv
import glob
import json
import os
import pickle
import sys
import time
from datetime import datetime, timezone
from multiprocessing import Pool
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import cross  # noqa: E402
import maker_run  # noqa: E402
import outlier  # noqa: E402
import outlier_run as orun  # noqa: E402
from arb_live import symmap_for  # noqa: E402
from common import EXCHANGES, load_pool, norm  # noqa: E402

G = {}
THETAS = (10.0, 15.0, 20.0, 30.0, 50.0, 75.0, 100.0)
SPLIT = ((50, 0), (0, 50))            # (задержка входа, задержка выхода), мс — только x1/x2
OUT_BN = (1794766338000, 1794766460000 + 120_000)
# поля круга
F = ("ts", "te", "closed", "net", "size", "age_in", "age_out", "audit", "out", "mtm")


def utc_ms(s):
    return int(datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp() * 1000)


def load_audit(root):
    res = {}
    for ex in EXCHANGES:
        f = Path(root, ex, "stretches-book.csv")
        if not f.exists():
            continue
        for r in csv.DictReader(open(f)):
            res.setdefault(ex, {}).setdefault(r["symbol"], []).append((utc_ms(r["start_utc"]) - 60_000, utc_ms(r["end_utc"]) + 120_000))
    return res


def raw_e(work, ex, sym):
    fs = sorted(glob.glob(str(Path(work, ex, sym + ".*.npz"))))
    return np.sort(np.concatenate([np.load(f)["e"] for f in fs])) if fs else None


def age(e, t):
    i = np.searchsorted(e, t, "right") - 1
    return np.where(i >= 0, t - e[np.maximum(i, 0)], np.inf)


def overlap(iv, ts, te):
    if not iv:
        return np.zeros(len(ts), bool)
    hit = np.zeros(len(ts), bool)
    for a, b in iv:
        hit |= (ts < b) & (te > a)
    return hit


def sim_split(P, ent, lat_in, lat_out, th_exit):
    """cross.simulate_cross с раздельными задержками входа и выхода (та же логика, rearm)."""
    T, t1 = P["T"], P["t1"]
    Z, X = cross.exit_sig(P), {1: P["Dm"], -1: -P["Dm"]}
    out, unclosed, t_free = [], 0, P["t0"]
    while True:
        nxt = cross._next_entry(P, ent, t_free)
        if nxt is None:
            break
        side, i = nxt
        e = cross._enter(P, i, side, lat_in)
        if e is None:
            break
        ts, tx, edge, slip, size, div = e
        k0 = max(int(np.searchsorted(T, tx, "right")) - 1, 0)
        ke = cross.first_ge(Z[side], k0, th_exit)
        te = max(int(T[ke]), tx) + lat_out if ke >= 0 else t1
        if te >= t1:
            out.append((ts, side, edge, slip, size, np.nan, np.nan, np.nan, div, tx, np.nan))
            unclosed = 1
            break
        gross = cross._gross_exit(P, side, edge, te)
        out.append((ts, side, edge, slip, size, gross - cross.FEE_TT4, te - ts, gross, div, tx, te))
        kr = cross.first_le(X[-side], ke, 0.0)
        if kr < 0:
            break
        t_free = max(te, int(T[kr]))
    return np.array(out, float).reshape(-1, cross.NC), unclosed


def rounds_of(P, arr, lat, ea, eb, iv, bn):
    """(n, 11) cross.COLS -> (m, len(F)) с пометками. Незакрытый: te = t1, mtm — чистый итог при закрытии тейкером по ценам t1 − 1."""
    t1 = P["t1"]
    # пропуски — только для закрытых: в dead.json у каждой биржи хвост [t1 − 10 мс, t1], drop_dead снял бы все незакрытые
    unc = arr[np.isnan(arr[:, 5])]
    arr = np.r_[orun.drop_dead(arr[~np.isnan(arr[:, 5])], orun.dead_of(*G["cur"]), lat, t1), unc]
    if not len(arr):
        return np.zeros((0, len(F)))
    closed = ~np.isnan(arr[:, 5])
    ts = arr[:, 0]
    te = np.where(closed, arr[:, 0] + np.nan_to_num(arr[:, 6]), t1)
    tin = arr[:, 9]
    a_in = np.maximum(age(ea, tin), age(eb, tin))
    a_out = np.where(closed, np.maximum(age(ea, te), age(eb, te)), np.nan)
    aud = overlap(iv, ts, te)
    out = (ts < OUT_BN[1]) & (te > OUT_BN[0]) if bn else np.zeros(len(ts), bool)
    mtm = np.full(len(ts), np.nan)
    for k in np.flatnonzero(~closed):
        mtm[k] = cross._gross_exit(P, int(arr[k, 1]), arr[k, 2], t1 - 1) - cross.FEE_TT4
    return np.c_[ts, te, closed, arr[:, 5], np.minimum(arr[:, 4], orun.SIZE_CAP), a_in, a_out, aud, out, mtm]


def work_coin(coin):
    g = G
    books, raws = {}, {}
    for ex, (sym, mult, _) in g["coins"][coin].items():
        if ex == "bingx":
            continue
        unit = float((g["meta"][ex].get(sym) or {}).get("ctVal") or 1.0) if ex == "okx" else 1.0
        a = maker_run.load_book(g["bwork"], ex, sym, mult, unit)
        if a is None or len(a["e"]) < 20:
            continue
        books[ex] = a
        raws[ex] = raw_e(g["bwork"], ex, sym)
    exs = [e for e in EXCHANGES if e in books]
    res = {}
    for i, a_ in enumerate(exs):
        for b_ in exs[i + 1:]:
            ia, ib = (float(np.median((books[x]["bid"] + books[x]["ask"]) / 2)) for x in (a_, b_))
            if not 0.9 < ia / ib < 1.1:
                continue
            P = outlier.prep_tt(books[a_], books[b_], g["t0"], g["t1"])
            if P is None:
                continue
            G["cur"] = (a_, b_)
            sa, sb = g["coins"][coin][a_][0], g["coins"][coin][b_][0]
            iv = g["audit"].get(a_, {}).get(sa, []) + g["audit"].get(b_, {}).get(sb, [])
            bn = "binance" in (a_, b_)
            for th in g["thetas"]:
                ent = outlier.entries_for(P, th)
                arrs = {"c-t": cross.ext9(outlier.simulate_tt(P, ent, 0, "full")[0], 0),
                        "x2-t": cross.simulate_cross(P, ent, 0, th / 2)[0],
                        "x1-t": cross.simulate_cross(P, ent, 0, th)[0],
                        "flip": cross.simulate_flip(P, ent, 0, th)[0]}
                for (li, lo) in SPLIT:
                    if th <= 30:
                        for nm, x in (("x2", th / 2), ("x1", th)):
                            arrs[f"{nm}-t@{li}/{lo}"] = sim_split(P, ent, li, lo, x)[0]
                for nm, arr in arrs.items():
                    lat = 50 if "@" in nm else 0
                    res[(coin, a_, b_, th, nm)] = rounds_of(P, arr, lat, raws[a_], raws[b_], iv, bn).astype(np.float64)
    return res


def main(bwork, t0, t1, out, par=4):
    day = time.strftime("%Y-%m-%d", time.gmtime(t1 / 1000))
    symmap, day = symmap_for(day)
    meta = {ex: load_pool(ex, day)[1] for ex in EXCHANGES}
    coins = {}
    for ex, m in symmap.items():
        for sym, (coin, mult) in m.items():
            cur = coins.setdefault(coin, {}).get(ex)
            exact = norm(ex, sym)[0] == coin
            if cur is None or (exact and not cur[2]):
                coins[coin][ex] = (sym, mult, exact)
    dead = json.load(open(os.environ["DEAD"])) if os.environ.get("DEAD") else None
    G.update({"bwork": str(Path(bwork, "parsed")), "t0": t0, "t1": t1, "coins": coins, "meta": meta, "thetas": THETAS,
              "audit": load_audit(os.environ["AUDIT"])})
    orun.G.update({"dead": dead})
    only = os.environ.get("COINS")
    names = sorted(coins) if not only else only.split(",")
    res, t = {}, time.time()
    with Pool(par, maxtasksperchild=4) as p:
        for k, r in enumerate(p.imap_unordered(work_coin, names, chunksize=1)):
            res.update(r)
            if k % 50 == 0:
                print(k, len(names), round(time.time() - t), flush=True)
    with open(out, "wb") as f:
        pickle.dump({"t0": t0, "t1": t1, "days": orun.day_edges(t0, t1), "F": F, "rows": res,
                     "audit_n": {ex: sum(len(v) for v in d.values()) for ex, d in G["audit"].items()}}, f, protocol=4)
    print({"coins": len(names), "keys": len(res), "s": round(time.time() - t, 1)})


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4], int(sys.argv[5]) if len(sys.argv) > 5 else 4)
