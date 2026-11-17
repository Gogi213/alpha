"""Проверяющий arb-tokyo: тейкер-тейкер с РАЗНОЙ задержкой на каждой ноге (L биржи = путь заявки Токио → движок этой биржи).
Логика — cross.simulate_cross («из − в +») и outlier.simulate_tt «к центру» (rearm, решение по состоянию пары), но нога A исполняется в t + L_A, нога B — в t + L_B;
поиск выхода начинается с max(t + L_A, t + L_B). Задержка данных не моделируется (как в отчёте: сигнал — по E биржи).
Запуск: python3 tokyo_leg.py <каталог книг (с parsed/)> <t0> <t1> <выход.pkl> [PAR]; COINS, DEAD — как tokyo_run.
"""
import json, os, pickle, sys, time
from multiprocessing import Pool
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).parent))
import tokyo_run as tr  # noqa: E402  (подменяет core.pair_series отсечкой 60 с)
import cross, outlier, maker_run, outlier_run as orun  # noqa: E402
from arb_live import symmap_for  # noqa: E402
from common import load_pool, norm  # noqa: E402

EX3 = ("binance", "bybit", "okx")
THS = (20.0, 30.0)
SCEN = []
for n in (1, 2):
    for f in (1, 2, 5, 10, 20, 35, 50):
        SCEN.append(("bybit", n, f))        # Binance и OKX — L = n, Bybit — f
        SCEN.append(("bybit+okx", n, f))    # Binance — n, Bybit и OKX — f
SCEN += [("bybit", 5, 5), ("bybit", 10, 10)]   # симметричные L = 5 / 10
EXSHIFT = {k: int(v) for k, v in (x.split(":") for x in os.environ.get("EXSHIFT", "").split(",") if x)}   # сдвиг меток книги биржи, мс (шкала движка)
G = {}


def lat_of(scen, ex):
    kind, n, f = scen
    return f if ex == "bybit" or (ex == "okx" and kind == "bybit+okx") else n


def idx(b, t):
    return int(np.searchsorted(b["e"], t, "right")) - 1


def sim(P, ent, la, lb, th, exit_):
    A, B, T, t1 = P["A"], P["B"], P["T"], P["t1"]
    Z, X = cross.exit_sig(P), {1: P["Dm"], -1: -P["Dm"]}
    out, unclosed, t_free = [], 0, P["t0"]
    while True:
        nxt = cross._next_entry(P, ent, t_free)
        if nxt is None:
            break
        side, i = nxt
        ts = int(T[i])
        ta, tb = ts + la, ts + lb
        tx = max(ta, tb)
        if tx >= t1:
            break
        ia, ib = idx(A, ta), idx(B, tb)
        if side > 0:
            edge, size = (A["bid"][ia] / B["ask"][ib] - 1) * 1e4, min(A["dbid"][ia], B["dask"][ib])
        else:
            edge, size = (A["ask"][ia] / B["bid"][ib] - 1) * 1e4, min(A["dask"][ia], B["dbid"][ib])
        k0 = max(int(np.searchsorted(T, tx, "right")) - 1, 0)
        ke = cross.first_ge(Z[side], k0, th) if exit_ == "x1-t" else outlier.first_le(X[side], k0, 0.0)
        if ke < 0:
            out.append((ts, side, edge, 0, size, np.nan, np.nan, np.nan, 0, tx, np.nan)); unclosed = 1; break
        tsig = max(int(T[ke]), tx)
        tea, teb = tsig + la, tsig + lb
        te = max(tea, teb)
        if te >= t1:
            out.append((ts, side, edge, 0, size, np.nan, np.nan, np.nan, 0, tx, np.nan)); unclosed = 1; break
        ja, jb = idx(A, tea), idx(B, teb)
        gross = edge - (A["ask"][ja] / B["bid"][jb] - 1) * 1e4 if side > 0 else (A["bid"][ja] / B["ask"][jb] - 1) * 1e4 - edge
        out.append((ts, side, edge, 0, size, gross - cross.FEE_TT4, te - ts, gross, 0, tx, te))
        t_free = te
        if exit_ == "x1-t":
            kr = cross.first_le(X[-side], ke, 0.0)
            if kr < 0:
                break
            t_free = max(te, int(T[kr]))
    return np.array(out, float).reshape(-1, cross.NC), unclosed


def work_coin(coin):
    g = G
    books = {}
    for ex, (sym, mult, _) in g["coins"][coin].items():
        if ex not in EX3:
            continue
        unit = float((g["meta"][ex].get(sym) or {}).get("ctVal") or 1.0) if ex == "okx" else 1.0
        a = maker_run.load_book(g["bwork"], ex, sym, mult, unit)
        if a is not None and len(a["e"]) >= 20:
            if EXSHIFT.get(ex):
                a = dict(a)
                a["e"] = a["e"] + EXSHIFT[ex]
            books[ex] = a
    rows = {}
    exs = [e for e in EX3 if e in books]
    for i, a_ in enumerate(exs):
        for b_ in exs[i + 1:]:
            ma, mb = (float(np.median((books[x]["bid"] + books[x]["ask"]) / 2)) for x in (a_, b_))
            if not 0.9 < ma / mb < 1.1:
                continue
            P = outlier.prep_tt(books[a_], books[b_], g["t0"], g["t1"])
            if P is None:
                continue
            for th in THS:
                ent = outlier.entries_for(P, th)
                for sc in SCEN:
                    la, lb = lat_of(sc, a_), lat_of(sc, b_)
                    for ex_ in ("x1-t", "c-t"):
                        arr, un = sim(P, ent, la, lb, th, ex_)
                        arr = orun.drop_dead(arr, orun.dead_of(a_, b_), max(la, lb), g["t1"])
                        if len(arr):
                            st = tr.stats_x(arr, un, g["t0"], g["t1"])
                            if st is not None:
                                rows[(coin, a_, b_, th, ex_, sc)] = st
    print(coin, len(rows), flush=True)
    return rows


def main(bwork, t0, t1, out, par=4):
    symmap, day = symmap_for(time.strftime("%Y-%m-%d", time.gmtime(t1 / 1000)))
    meta = {ex: load_pool(ex, day)[1] for ex in EX3}
    coins = {}
    for ex, m in symmap.items():
        for sym, (coin, mult) in m.items():
            cur = coins.setdefault(coin, {}).get(ex)
            exact = norm(ex, sym)[0] == coin
            if cur is None or (exact and not cur[2]):
                coins[coin][ex] = (sym, mult, exact)
    dead = json.load(open(os.environ["DEAD"])) if os.environ.get("DEAD") else {}
    dead.setdefault("binance", []).append([1794766338000, 1794766460000])
    dead["binance"].sort()
    orun.G.update({"dead": dead})
    G.update({"bwork": str(Path(bwork, "parsed")), "t0": t0, "t1": t1, "coins": coins, "meta": meta})
    names = os.environ["COINS"].split(",") if os.environ.get("COINS") else sorted(coins)
    rows = {}
    with Pool(par, maxtasksperchild=4) as p:
        for r in p.imap_unordered(work_coin, names, chunksize=1):
            rows.update(r)
    pickle.dump({"t0": t0, "t1": t1, "rows": rows, "scen": SCEN}, open(out, "wb"), protocol=4)
    print("done", len(rows))


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4], int(sys.argv[5]) if len(sys.argv) > 5 else 4)
