"""Проверка единственного плюса мейкер + хедж входа (отчёт arb-cross-exit, п. 6): θ 0,75 / 1 %, L = 50, выход «через центр θ» тейкером, без BingX-хеджа.
$/сут закрытых (сверка с cross.pkl), незакрытые по цене конца окна, пометка audit (как cross_review.py).
Запуск: python3 cross_review_mk.py <каталог книг> <каталог сделок> <t0> <t1> <выход.json>; DEAD, AUDIT — как у cross_review.py."""
import json
import os
import sys
from multiprocessing import Pool
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import cross  # noqa: E402
import cross_review as cr  # noqa: E402
import maker  # noqa: E402
import maker_run  # noqa: E402
import outlier_run as orun  # noqa: E402
from common import EXCHANGES  # noqa: E402

G = cr.G


def work(coin):
    g = G
    books, trades = {}, {}
    for ex, (sym, mult, _) in g["coins"][coin].items():
        unit = float((g["meta"][ex].get(sym) or {}).get("ctVal") or 1.0) if ex == "okx" else 1.0
        a = maker_run.load_book(g["bwork"], ex, sym, mult, unit)
        if a is None or len(a["e"]) < 20:
            continue
        te, tp = maker_run.load_trades(g["twork"], ex, sym, mult)
        books[ex], trades[ex] = a, (te if te is not None else np.zeros(0, np.int64), tp if tp is not None else np.zeros(0))
    exs = [e for e in EXCHANGES if e in books]
    out = []
    for m_ in exs:
        for h_ in exs:
            if m_ == h_ or h_ == "bingx":
                continue
            im, ih = (float(np.median((books[x]["bid"] + books[x]["ask"]) / 2)) for x in (m_, h_))
            if not 0.9 < im / ih < 1.1:
                continue
            P = cross.prep_mk_x(books[m_], books[h_], trades[m_][0], trades[m_][1], g["t0"], g["t1"])
            if P is None:
                continue
            G["cur"] = (m_, h_)
            iv = g["audit"].get(m_, {}).get(g["coins"][coin][m_][0], []) + g["audit"].get(h_, {}).get(g["coins"][coin][h_][0], [])
            for th in (75.0, 100.0):
                arr, _ = cross.simulate_mk_x(P, maker.candidates(P, th), 50, "tt", th, True)
                unc = arr[np.isnan(arr[:, 5])]
                arr = np.r_[orun.drop_dead(arr[~np.isnan(arr[:, 5])], orun.dead_of(m_, h_), 50, g["t1"]), unc]
                for r in arr:
                    closed = not np.isnan(r[5])
                    te = r[10] if closed else g["t1"]
                    if closed:
                        net = r[5]
                    else:
                        M, H = P["M"], P["H"]
                        i, j = (int(np.searchsorted(M["e"], g["t1"] - 1, "right")) - 1, int(np.searchsorted(H["e"], g["t1"] - 1, "right")) - 1)
                        gross = r[2] - (M["ask"][i] / H["bid"][j] - 1) * 1e4 if r[1] > 0 else (M["bid"][i] / H["ask"][j] - 1) * 1e4 - r[2]
                        net = gross - maker.FEE_TT
                    aud = bool(cr.overlap(iv, np.array([r[0]]), np.array([te]))[0])
                    out.append({"coin": coin, "m": m_, "h": h_, "th": th, "closed": closed, "net": float(net), "size": float(min(r[4], orun.SIZE_CAP)),
                                "ts": float(r[0]), "te": float(te), "audit": aud})
    return out


def main(bwork, twork, t0, t1, outp):
    import time
    from arb_live import symmap_for
    from common import load_pool, norm
    day = time.strftime("%Y-%m-%d", time.gmtime(t1 / 1000))
    symmap, day = symmap_for(day)
    coins = {}
    for ex, m in symmap.items():
        for sym, (coin, mult) in m.items():
            cur = coins.setdefault(coin, {}).get(ex)
            exact = norm(ex, sym)[0] == coin
            if cur is None or (exact and not cur[2]):
                coins[coin][ex] = (sym, mult, exact)
    G.update({"bwork": str(Path(bwork, "parsed")), "twork": str(Path(twork, "parsed")), "t0": t0, "t1": t1, "coins": coins,
              "meta": {ex: load_pool(ex, day)[1] for ex in EXCHANGES}, "audit": cr.load_audit(os.environ["AUDIT"])})
    orun.G.update({"dead": json.load(open(os.environ["DEAD"]))})
    res = []
    with Pool(4, maxtasksperchild=4) as p:
        for r in p.imap_unordered(work, sorted(coins), chunksize=1):
            res += r
    json.dump(res, open(outp, "w"))
    win_d = (t1 - t0) / 86_400_000
    for th in (75.0, 100.0):
        x = [r for r in res if r["th"] == th]
        u = lambda rs: sum(r["size"] * r["net"] / 1e4 for r in rs) / win_d  # noqa: E731
        cl = [r for r in x if r["closed"]]
        un = [r for r in x if not r["closed"]]
        for lab, sel in (("M=bingx", lambda r: r["m"] == "bingx"), ("no bingx", lambda r: r["m"] != "bingx")):
            xs = [r for r in x if sel(r)]
            print(" ", lab, {"closed": sum(r["closed"] for r in xs), "unclosed": sum(not r["closed"] for r in xs),
                             "usd_closed": round(u([r for r in xs if r["closed"]]), 1), "usd_unclosed_mtm": round(u([r for r in xs if not r["closed"]]), 1)})
        print(th, {"closed": len(cl), "unclosed": len(un), "usd_closed": round(u(cl), 1), "usd_unclosed_mtm": round(u(un), 1),
                   "usd_closed_audit": round(u([r for r in cl if r["audit"]]), 1), "usd_closed_clean": round(u([r for r in cl if not r["audit"]]), 1)})


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), sys.argv[5])
