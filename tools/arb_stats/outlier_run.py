"""Счёт «только выбросы» (outlier.py, maker.py) по всем парам: сводки по паре и настройке + гистограммы для итогов.

Запуск: python3 outlier_run.py <каталог книг (parsed/)> <каталог сделок (parsed/)> <t0_мс> <t1_мс> <выход.pkl> [PAR]
Пишет pickle: {"t0", "t1", "rows": {(режим, монета, X, Y, настройка): сводка}, "hist": {(режим, настройка, группа): (net, gross, div)}, ...}.
Режим "tt" — тейкер-тейкер, пара (A, B) в порядке бирж; "mk" — мейкер + хедж, (M, H) упорядочена. Настройка — (θ bps, L мс, закрытие).
Группа "ok" — в рейтинге; "bingx" — вне рейтинга (tt: любая нога BingX; mk: хедж на BingX — книга-снимок ≈ 2,6/с, цена устаревшая).
"""
import os
import pickle
import sys
import time
from multiprocessing import Pool
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import maker  # noqa: E402
import maker_run  # noqa: E402
import outlier  # noqa: E402
from arb_live import symmap_for  # noqa: E402
from common import EXCHANGES, load_pool, norm  # noqa: E402

G = {}
H_LO, H_N = -400, 800        # гистограмма: шаг 1 bps на [−400; 400)
SIZE_CAP = 100.0
PERIODS_H = (1, 4, 6, 24)


def hist(x, lo=H_LO):
    return np.bincount(np.clip(np.floor(x - lo).astype(np.int64), 0, H_N - 1), minlength=H_N).astype(np.int64)


def q(x, p):
    return float(np.percentile(x, p)) if len(x) else None


def pair_stats(arr, un, t0, t1):
    """Сводка кругов одной настройки (bps, $). None — кругов нет."""
    if not len(arr):
        return None
    mid = (t0 + t1) // 2
    win_d, half_d = (t1 - t0) / 86_400_000, (t1 - t0) / 172_800_000
    t = arr[:, 0]
    k = ~np.isnan(arr[:, 5])
    cl = arr[k]
    net, size = cl[:, 5], cl[:, 4]
    cap = np.minimum(size, SIZE_CAP) * net / 1e4
    full = size * net / 1e4
    h1, h2 = cl[:, 0] < mid, cl[:, 0] >= mid
    return {"n": int(len(arr)), "closed": int(len(cl)), "unclosed": int(un),
            "entries": [int((t >= t1 - h * 3600_000).sum()) for h in PERIODS_H],
            "net": [q(net, 50), q(net, 25)], "gross_med": q(cl[:, 7], 50), "div_med": q(arr[:, 8], 50), "pos": float((net > 0).mean()) if len(net) else None,
            "size_med": q(arr[:, 4], 50), "slip_med": q(arr[:, 3], 50), "hold_s": [q(cl[:, 6] / 1000, 50), q(cl[:, 6] / 1000, 95)],
            "usd_cap": float(cap.sum() / win_d), "usd_full": float(full.sum() / win_d),
            "usd_cap_h": [float(cap[h1].sum() / half_d), float(cap[h2].sum() / half_d)],
            "net_med_h": [q(net[h1], 50), q(net[h2], 50)], "n_h": [int(h1.sum()), int(h2.sum())],
            "usd_cap_trim": float((cap.sum() - (cap.max() if len(cap) else 0.0)) / win_d)}, (hist(net), hist(cl[:, 7]), hist(arr[:, 8], 0))


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

    def put(mode, key, cfg, arr, un, grp):
        r = pair_stats(arr, un, t0, t1)
        if r is None:
            return
        st, (hn, hg, hd) = r
        rows[(mode, *key, cfg)] = st
        h = hs.setdefault((mode, cfg, grp), [np.zeros(H_N, np.int64), np.zeros(H_N, np.int64), np.zeros(H_N, np.int64)])
        h[0] += hn
        h[1] += hg
        h[2] += hd

    for i, a_ in enumerate(exs):
        for b_ in exs[i + 1:]:
            if not 0.9 < info[a_] / info[b_] < 1.1:
                continue
            P = outlier.prep_tt(books[a_], books[b_], t0, t1)
            if P is None:
                continue
            grp = "bingx" if "bingx" in (a_, b_) else "ok"
            for th in outlier.THETAS:
                ent = outlier.entries_for(P, th)
                for lat in outlier.LATS_TT:
                    for cl in outlier.CLOSES:
                        arr, un = outlier.simulate_tt(P, ent, lat, cl)
                        put("tt", (coin, a_, b_), (th, lat, cl), arr, un, grp)
    for m_ in exs:
        for h_ in exs:
            if m_ == h_ or not 0.9 < info[m_] / info[h_] < 1.1:
                continue
            P = maker.prep(books[m_], books[h_], trades[m_][0], trades[m_][1], t0, t1, g["d_ms"])
            if P is None:
                continue
            grp = "bingx" if h_ == "bingx" else "ok"
            for cfg, (arr, un) in maker.rounds_for(P, outlier.THETAS, maker.LATS).items():
                put("mk", (coin, m_, h_), cfg, arr, un, grp)
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
    G.update({"bwork": str(Path(bwork, "parsed")), "twork": str(Path(twork, "parsed")), "t0": t0, "t1": t1, "coins": coins, "meta": meta,
              "d_ms": maker.D_MS})
    only = os.environ.get("COINS")
    names = sorted(coins) if not only else only.split(",")
    t = time.time()
    rows, hs = {}, {}
    with Pool(par) as p:
        for r, h in p.imap_unordered(work_coin, names, chunksize=2):
            rows.update(r)
            for k, v in h.items():
                a = hs.setdefault(k, [np.zeros(H_N, np.int64) for _ in range(3)])
                for i in range(3):
                    a[i] += v[i]
    with open(out, "wb") as f:
        pickle.dump({"t0": t0, "t1": t1, "rows": rows, "hist": hs, "thetas": outlier.THETAS, "lats_tt": outlier.LATS_TT, "lats_mk": maker.LATS,
                     "fee_tt4": outlier.FEE_TT4, "fee_mk": {"tt": maker.FEE_TT, "mt": maker.FEE_MT}, "d_ms": maker.D_MS, "size_cap": SIZE_CAP,
                     "coins": len(names)}, f, protocol=4)
    print({"coins": len(names), "rows": len(rows), "s": round(time.time() - t, 1)})


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), sys.argv[5], int(sys.argv[6]) if len(sys.argv) > 6 else 3)
