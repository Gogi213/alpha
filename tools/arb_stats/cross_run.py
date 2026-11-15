"""Счёт выходов «через центр» и «переворот» (cross.py) по всем парам: тейкер-тейкер вход (L = 0/50/300) и мейкер + хедж вход (L = 50/300) × правила выхода,
вместе с прежним выходом «к центру» (outlier.simulate_tt «полное» и maker.simulate «тт»/«мт» — прежний код как есть) на тех же данных и том же проходе.

Запуск: python3 cross_run.py <каталог книг (parsed/)> <каталог сделок (parsed/)> <t0_мс> <t1_мс> <выход.pkl> [PAR]
Переменные: DEAD — json пропусков сбора (круги, пересекающие пропуск ± запас, исключаются), FUNDING — json funding_fetch.py, COINS — список монет (проба),
THETAS — список θ в bps через запятую (по умолчанию 10,15,20,30,50,75,100), MODES — «tt», «mk» или «tt,mk».
Пишет pickle: {"rows": {(режим, монета, X, Y, (θ, L, выход)): сводка}, "hist": {(режим, (θ, L, выход), группа): гистограммы}, …}; сводка — outlier_run.pair_stats
+ фандинг (fund_*, usd_fund), занятый номинал (occ), число сделок на ноге M. Группа «ok» — в рейтинге; «bingx» — вне рейтинга (как outlier_run).
Режим «tt»: пара (A, B) в порядке бирж; «mk»: (M, H) упорядочена.
"""
import json
import os
import pickle
import sys
import time
from multiprocessing import Pool
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import cross  # noqa: E402
import maker  # noqa: E402
import maker_run  # noqa: E402
import outlier  # noqa: E402
import outlier_run as orun  # noqa: E402
from arb_live import symmap_for  # noqa: E402
from common import EXCHANGES, load_pool, norm  # noqa: E402

G = {}
THETAS = (10.0, 15.0, 20.0, 30.0, 50.0, 75.0, 100.0)    # bps = 0,1 / 0,15 / 0,2 / 0,3 / 0,5 / 0,75 / 1,0 %
LATS_TT = outlier.LATS_TT
LATS_MK = maker.LATS
EXITS_TT = ("c-t", "x2-t", "x1-t", "flip", "c-m", "x2-m", "x1-m")
EXITS_MK = ("c-t", "c-m", "x2-t", "x2-m", "x1-t", "x1-m")
HOLD_BINS_PER_DEC, HOLD_N = 40, 360        # гистограмма удержания: log10(мс) × 40, 0 … 9 декад


def hold_hist(hold_ms):
    h = hold_ms[~np.isnan(hold_ms)]
    return np.bincount(np.clip(np.floor(np.log10(np.maximum(h, 1.0)) * HOLD_BINS_PER_DEC).astype(np.int64), 0, HOLD_N - 1), minlength=HOLD_N).astype(np.int64)


def stats_x(arr, un, t0, t1):
    """pair_stats + фандинг, занятый номинал. arr — (n, 12): 11 полей cross.COLS + funding_bps (NaN — истории нет). -> (сводка, гистограммы) или None."""
    r = orun.pair_stats(arr, un, t0, t1)
    if r is None:
        return None
    st, (hn, hg, hd) = r
    win_d = (t1 - t0) / 86_400_000
    k = ~np.isnan(arr[:, 5])
    cl, fb = arr[k], arr[k, 11]
    w = np.minimum(cl[:, 4], orun.SIZE_CAP)
    kn = ~np.isnan(fb)
    st["fund_n"] = int(kn.sum())
    st["fund_med"] = orun.q(fb[kn], 50)
    st["usd_fund"] = float((w[kn] * fb[kn] / 1e4).sum() / win_d)
    hold_all = np.where(np.isnan(arr[:, 6]), t1 - arr[:, 0], arr[:, 6])
    st["occ"] = float((np.minimum(arr[:, 4], orun.SIZE_CAP) * hold_all).sum() / (t1 - t0))
    return st, (hn, hg, hd, orun.hist(fb[kn]), hold_hist(cl[:, 6]))


def work_coin(coin):
    g = G
    books, trades, info = {}, {}, {}
    for ex, (sym, mult, _) in g["coins"][coin].items():
        unit = float((g["meta"][ex].get(sym) or {}).get("ctVal") or 1.0) if ex == "okx" else 1.0
        a = maker_run.load_book(g["bwork"], ex, sym, mult, unit)
        if a is None or len(a["e"]) < 20:
            continue
        te, tp = maker_run.load_trades(g["twork"], ex, sym, mult)
        books[ex] = a
        trades[ex] = (te if te is not None else np.zeros(0, np.int64), tp if tp is not None else np.zeros(0))
        info[ex] = float(np.median((a["bid"] + a["ask"]) / 2))
    rows, hs = {}, {}
    exs = [e for e in EXCHANGES if e in books]
    t0, t1 = g["t0"], g["t1"]
    fund = {}
    for ex in exs:
        sym = g["coins"][coin][ex][0]
        f = g["funding"].get(ex, {}).get(sym)
        fund[ex] = cross.funding_cum(f) if f else None

    def put(mode, key, cfg, arr, un, grp, lat):
        arr = np.asarray(arr, float).reshape(-1, cross.NC)
        arr = orun.drop_dead(arr, orun.dead_of(key[1], key[2]), lat, t1)
        if not len(arr):
            return
        arr = np.c_[arr, cross.funding_bps(arr, fund.get(key[1]), fund.get(key[2]))]
        r = stats_x(arr, un, t0, t1)
        if r is None:
            return
        st, hh = r
        rows[(mode, *key, cfg)] = st
        h = hs.setdefault((mode, cfg, grp), [np.zeros(len(x), np.int64) for x in hh])
        for a_, b_ in zip(h, hh):
            a_ += b_

    if "tt" in g["modes"]:
        for i, a_ in enumerate(exs):
            for b_ in exs[i + 1:]:
                if not 0.9 < info[a_] / info[b_] < 1.1:
                    continue
                P = outlier.prep_tt(books[a_], books[b_], t0, t1)
                if P is None:
                    continue
                grp = "bingx" if "bingx" in (a_, b_) else "ok"
                m_is_A = len(trades[a_][0]) >= len(trades[b_][0])
                Q = cross.prep_exit_mk(P, *(trades[a_] if m_is_A else trades[b_]), m_is_A)
                for th in g["thetas"]:
                    ent = outlier.entries_for(P, th)
                    for lat in outlier.LATS_TT:
                        key = (coin, a_, b_)
                        arr, un = outlier.simulate_tt(P, ent, lat, "full")
                        put("tt", key, (th, lat, "c-t"), cross.ext9(arr, lat), un, grp, lat)
                        for nm, x in (("x2", th / 2), ("x1", th)):
                            arr, un = cross.simulate_cross(P, ent, lat, x)
                            put("tt", key, (th, lat, nm + "-t"), arr, un, grp, lat)
                        arr, un = cross.simulate_flip(P, ent, lat, th)
                        put("tt", key, (th, lat, "flip"), arr, un, grp, lat)
                        if Q is not None:
                            for nm, x in (("c", 0.0), ("x2", th / 2), ("x1", th)):
                                arr, un = cross.simulate_cross_mk(P, ent, lat, Q, x, x > 0)
                                put("tt", key, (th, lat, nm + "-m"), arr, un, grp, lat)
    if "mk" in g["modes"]:
        for m_ in exs:
            for h_ in exs:
                if m_ == h_ or not 0.9 < info[m_] / info[h_] < 1.1:
                    continue
                P = cross.prep_mk_x(books[m_], books[h_], trades[m_][0], trades[m_][1], t0, t1, g["d_ms"])
                if P is None:
                    continue
                grp = "bingx" if h_ == "bingx" else "ok"
                key = (coin, m_, h_)
                for th in g["thetas"]:
                    cand = maker.candidates(P, th)
                    for lat in maker.LATS:
                        for var, nm in (("tt", "c-t"), ("mt", "c-m")):
                            arr, un = maker.simulate(P, cand, lat, var)
                            put("mk", key, (th, lat, nm), cross.ext9(arr, lat, True), un, grp, lat)
                        for xn, x in (("x2", th / 2), ("x1", th)):
                            for var, vn in (("tt", "t"), ("mt", "m")):
                                arr, un = cross.simulate_mk_x(P, cand, lat, var, x, True)
                                put("mk", key, (th, lat, f"{xn}-{vn}"), arr, un, grp, lat)
    return rows, hs


def main(bwork, twork, t0, t1, out, par=3):
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
    funding = json.load(open(os.environ["FUNDING"]))["rates"] if os.environ.get("FUNDING") else {}
    thetas = tuple(float(x) for x in os.environ["THETAS"].split(",")) if os.environ.get("THETAS") else THETAS
    modes = os.environ.get("MODES", "tt,mk").split(",")
    G.update({"bwork": str(Path(bwork, "parsed")), "twork": str(Path(twork, "parsed")), "t0": t0, "t1": t1, "coins": coins, "meta": meta,
              "d_ms": maker.D_MS, "thetas": thetas, "modes": modes, "funding": funding})
    orun.G.update({"dead": dead})
    only = os.environ.get("COINS")
    names = sorted(coins) if not only else only.split(",")
    t = time.time()
    rows, hs = {}, {}
    with Pool(par, maxtasksperchild=int(os.environ.get("MAXTASKS", "4"))) as p:
        for k, (r, h) in enumerate(p.imap_unordered(work_coin, names, chunksize=1)):
            rows.update(r)
            for key, v in h.items():
                a = hs.setdefault(key, [np.zeros(len(x), np.int64) for x in v])
                for a_, b_ in zip(a, v):
                    a_ += b_
            if k % 20 == 0:
                print(k, len(names), len(rows), round(time.time() - t), flush=True)
    with open(out, "wb") as f:
        pickle.dump({"t0": t0, "t1": t1, "days": orun.day_edges(t0, t1), "dead": dead, "rows": rows, "hist": hs, "thetas": thetas, "lats_tt": LATS_TT, "lats_mk": LATS_MK,
                     "exits_tt": EXITS_TT, "exits_mk": EXITS_MK, "fee_tt4": cross.FEE_TT4, "fee_mx": cross.FEE_MX, "fee_mk": {"tt": maker.FEE_TT, "mt": maker.FEE_MT},
                     "d_ms": maker.D_MS, "size_cap": orun.SIZE_CAP, "coins": len(names), "funding_symbols": {ex: len(v) for ex, v in funding.items()}},
                    f, protocol=4)
    print({"coins": len(names), "rows": len(rows), "s": round(time.time() - t, 1)})


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), sys.argv[5], int(sys.argv[6]) if len(sys.argv) > 6 else 3)
