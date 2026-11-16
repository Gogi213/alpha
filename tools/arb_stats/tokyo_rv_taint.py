"""Проверяющий arb-tokyo: по всем монетам (без BingX) тейкер-тейкер с отсечкой 60 с (tokyo_run) — доля $/сут кругов, задетых участками аудита collect-gap,
и фандинг за удержание. θ 0,2 / 0,3 %, L 1 / 2 / 5 / 10, выходы x1-t и c-t.
Запуск: python3 taint.py <книги> <t0> <t1> <out.pkl> [PAR]; DEAD, AUDIT (каталог out/ аудита), FUNDING (json funding_fetch.py).
"""
import json, os, pickle, sys, time
from multiprocessing import Pool
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).parent))
import tokyo_run as tr  # noqa: E402
import cross, outlier, maker_run, outlier_run as orun  # noqa: E402
from cross_review import load_audit, overlap  # noqa: E402
from arb_live import symmap_for  # noqa: E402
from common import load_pool, norm  # noqa: E402

EX3 = ("binance", "bybit", "okx")
G = {}


def work_coin(coin):
    g = G
    books, fund = {}, {}
    for ex, (sym, mult, _) in g["coins"][coin].items():
        if ex not in EX3:
            continue
        unit = float((g["meta"][ex].get(sym) or {}).get("ctVal") or 1.0) if ex == "okx" else 1.0
        a = maker_run.load_book(g["bwork"], ex, sym, mult, unit)
        if a is not None and len(a["e"]) >= 20:
            books[ex] = a
            f = g["funding"].get(ex, {}).get(sym)
            fund[ex] = cross.funding_cum(f) if f else None
    res = {}
    exs = [e for e in EX3 if e in books]
    for i, a_ in enumerate(exs):
        for b_ in exs[i + 1:]:
            ma, mb = (float(np.median((books[x]["bid"] + books[x]["ask"]) / 2)) for x in (a_, b_))
            if not 0.9 < ma / mb < 1.1:
                continue
            P = outlier.prep_tt(books[a_], books[b_], g["t0"], g["t1"])
            if P is None:
                continue
            sa, sb = g["coins"][coin][a_][0], g["coins"][coin][b_][0]
            iv = g["audit"].get(a_, {}).get(sa, []) + g["audit"].get(b_, {}).get(sb, [])
            for th in (20.0, 30.0):
                ent = outlier.entries_for(P, th)
                for lat in (1, 2, 5, 10):
                    for nm in ("x1-t", "c-t"):
                        arr, un = cross.simulate_cross(P, ent, lat, th) if nm == "x1-t" else outlier.simulate_tt(P, ent, lat, "full")
                        arr = np.asarray(arr, float)
                        if nm == "c-t":
                            arr = cross.ext9(arr, lat)
                        arr = orun.drop_dead(arr, orun.dead_of(a_, b_), lat, g["t1"])
                        cl = arr[~np.isnan(arr[:, 5])]
                        if not len(cl):
                            continue
                        te = cl[:, 0] + cl[:, 6]
                        usd = np.minimum(cl[:, 4], orun.SIZE_CAP) * cl[:, 5] / 1e4
                        fb = cross.funding_bps(cl, fund.get(a_), fund.get(b_))
                        fu = np.minimum(cl[:, 4], orun.SIZE_CAP) * np.nan_to_num(fb) / 1e4
                        aud = overlap(iv, cl[:, 0], te)
                        res[(coin, a_, b_, th, lat, nm)] = (float(usd.sum()), float(usd[aud].sum()), int(aud.sum()), len(cl), float(fu.sum()), int(np.isnan(fb).sum()))
    return res


def main(bwork, t0, t1, out, par=2):
    symmap, day = symmap_for(time.strftime("%Y-%m-%d", time.gmtime(t1 / 1000)))
    meta = {ex: load_pool(ex, day)[1] for ex in EX3}
    coins = {}
    for ex, m in symmap.items():
        for sym, (coin, mult) in m.items():
            cur = coins.setdefault(coin, {}).get(ex)
            exact = norm(ex, sym)[0] == coin
            if cur is None or (exact and not cur[2]):
                coins[coin][ex] = (sym, mult, exact)
    dead = json.load(open(os.environ["DEAD"]))
    dead.setdefault("binance", []).append([1794766338000, 1794766460000])
    dead["binance"].sort()
    orun.G.update({"dead": dead})
    G.update({"bwork": str(Path(bwork, "parsed")), "t0": t0, "t1": t1, "coins": coins, "meta": meta, "audit": load_audit(os.environ["AUDIT"]),
              "funding": json.load(open(os.environ["FUNDING"]))["rates"]})
    names = sorted(coins)
    res, t = {}, time.time()
    with Pool(par, maxtasksperchild=4) as p:
        for k, r in enumerate(p.imap_unordered(work_coin, names, chunksize=1)):
            res.update(r)
            if k % 50 == 0:
                print(k, len(names), round(time.time() - t), flush=True)
    pickle.dump({"t0": t0, "t1": t1, "rows": res}, open(out, "wb"), protocol=4)
    print("done", len(res), round(time.time() - t))


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4], int(sys.argv[5]) if len(sys.argv) > 5 else 2)
