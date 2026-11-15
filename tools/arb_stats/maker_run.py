"""Счёт режима «мейкер + тейкер-хедж» по всем упорядоченным парам бирж (модель — maker.py) на разобранных книгах и сделках.

Запуск: python3 maker_run.py <каталог книг (state/, parsed/)> <каталог сделок (parsed/)> <t0_мс> <t1_мс> <выход.pkl> [PAR]
Пишет pickle: {"t0", "t1", "rows": {(монета, M, H): {(θ-индекс, L, вариант): сводка}}, "rounds": {... круги ...}, "info", "mismatch"}.
"""
import glob
import pickle
import sys
import time
from multiprocessing import Pool
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import core  # noqa: E402
import maker  # noqa: E402
from arb_live import symmap_for  # noqa: E402
from common import EXCHANGES, load_pool, norm, unit_of  # noqa: E402

G = {}


def _cat(files, keys):
    parts = [dict(np.load(f)) for f in files]
    return {k: np.concatenate([p[k] for p in parts]) for k in keys}


def load_book(work, ex, sym, mult, unit):
    fs = sorted(glob.glob(str(Path(work, ex, sym + ".*.npz"))))
    if not fs:
        return None
    a = _cat(fs, ("e", "rx", "lag", "bid", "ask", "bsz", "asz"))
    a["dbid"], a["dask"] = a["bsz"] * a["bid"] * unit, a["asz"] * a["ask"] * unit   # $ на лучшем уровне, как в analyze.load
    del a["bsz"], a["asz"]
    a["bid"], a["ask"] = a["bid"] / mult, a["ask"] / mult
    return core.clean(a)


def load_trades(work, ex, sym, mult):
    fs = sorted(glob.glob(str(Path(work, ex, sym + ".*.npz"))))
    if not fs:
        return None, None
    a = _cat(fs, ("e", "et", "rx", "p"))
    tt = a["e"] - a["et"]   # время сделки T (у Binance aggTrade E позже T на 0…200 мс, книга же стоит по E = T)
    o = np.lexsort((a["rx"], tt))
    return tt[o], a["p"][o] / mult


def work_coin(coin):
    g = G
    books, trades, info = {}, {}, {}
    for ex, (sym, mult, _) in g["coins"][coin].items():
        unit = unit_of(ex, g["meta"][ex].get(sym))
        a = load_book(g["bwork"], ex, sym, mult, unit)
        if a is None or len(a["e"]) < 20:
            continue
        te, tp = load_trades(g["twork"], ex, sym, mult)
        books[ex] = a
        trades[ex] = (te if te is not None else np.zeros(0, np.int64), tp if tp is not None else np.zeros(0))
        info[ex] = {"sym": sym, "mid": float(np.median((a["bid"] + a["ask"]) / 2)), "trades": int(len(trades[ex][0]))}
    rows, rounds, mism = {}, {}, []
    exs = [e for e in EXCHANGES if e in books]
    for m_ in exs:
        for h_ in exs:
            if m_ == h_:
                continue
            ratio = info[m_]["mid"] / info[h_]["mid"]
            if not 0.9 < ratio < 1.1:
                mism.append({"coin": coin, "M": m_, "H": h_, "ratio": ratio})
                continue
            P = maker.prep(books[m_], books[h_], trades[m_][0], trades[m_][1], g["t0"], g["t1"], g["d_ms"])
            if P is None:
                continue
            res = maker.run_pair(P)
            rows[(coin, m_, h_)] = {k: v[1] for k, v in res.items()}
            rounds[(coin, m_, h_)] = {k: v[0] for k, v in res.items() if len(v[0])}
    return coin, rows, rounds, mism, info


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
    G.update({"d_ms": int(__import__("os").environ.get("D_MS", maker.D_MS)), "bwork": str(Path(bwork, "parsed")), "twork": str(Path(twork, "parsed")), "t0": t0, "t1": t1, "coins": coins, "meta": meta})
    only = __import__("os").environ.get("COINS")
    names = sorted(coins) if not only else only.split(",")
    t = time.time()
    with Pool(par) as p:
        res = p.map(work_coin, names, chunksize=2)
    rows, rounds, mism, info = {}, {}, [], {}
    for coin, r, rd, m, i in res:
        rows.update(r)
        rounds.update(rd)
        mism += m
        info[coin] = i
    with open(out, "wb") as f:
        pickle.dump({"t0": t0, "t1": t1, "rows": rows, "rounds": rounds, "info": info, "mismatch": mism,
                     "model": {"D_MS": G["d_ms"], "THETAS": maker.THETAS, "LATS": maker.LATS, "FEE_TT": maker.FEE_TT, "FEE_MT": maker.FEE_MT,
                               "SIZE_USD": maker.SIZE_USD}}, f, protocol=4)
    print({"coins": len(names), "pairs": len(rows), "mismatch": len(mism), "s": round(time.time() - t, 1)})


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), sys.argv[5], int(sys.argv[6]) if len(sys.argv) > 6 else 3)
