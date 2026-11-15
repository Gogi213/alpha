"""Разбор кругов одной пары вручную: для заданных (монета, M, H, настройка) печатает цепочку «котировка → сделка за ценой → хедж → закрытие».

Запуск: python3 maker_trace.py <book_dir> <trades_dir> <t0> <t1> <монета> <M> <H> [θ-индекс L вариант] [сколько кругов]
Нужен для проверки модели глазами: цены котировки, H на момент котировки и хеджа, середина пары у закрытия.
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import maker  # noqa: E402
import maker_run  # noqa: E402
from arb_live import symmap_for  # noqa: E402
from common import EXCHANGES, load_pool  # noqa: E402


def main(bdir, tdir, t0, t1, coin, m_, h_, ti=0, lat=300, var="tt", n=8):
    import time
    day = time.strftime("%Y-%m-%d", time.gmtime(t1 / 1000))
    sm, day = symmap_for(day)
    meta = {ex: load_pool(ex, day)[1] for ex in EXCHANGES}
    books, trades = {}, {}
    for ex in (m_, h_):
        sym, mult = next((s, c[1]) for s, c in sm[ex].items() if c[0] == coin)
        unit = float((meta[ex].get(sym) or {}).get("ctVal") or 1.0) if ex == "okx" else 1.0
        books[ex] = maker_run.load_book(str(Path(bdir, "parsed")), ex, sym, mult, unit)
        trades[ex] = maker_run.load_trades(str(Path(tdir, "parsed")), ex, sym, mult)
    P = maker.prep(books[m_], books[h_], trades[m_][0], trades[m_][1], t0, t1)
    th = maker.THETAS[int(ti)]
    cand = maker.candidates(P, th)
    r, un = maker.simulate(P, cand, int(lat), var)
    print(coin, m_, "->", h_, "theta", th, "L", lat, var, "candidates", len(cand[0]), "rounds", len(r), "unclosed", un, "tick", P["tick"])
    H, M = books[h_], books[m_]
    for row in r[: int(n)]:
        t, side, edge, slip, size, net, hold = row
        k = int(np.searchsorted(cand[0], t))
        w = np.flatnonzero(P["tr_e"] == int(t))   # в одну миллисекунду может быть несколько сделок (проход по уровням)
        i_tr = int(w[np.argmax(P["tr_p"][w])] if side > 0 else w[np.argmin(P["tr_p"][w])])
        ih_q, ih_f = int(np.searchsorted(H["e"], t - maker.D_MS, "right")) - 1, int(np.searchsorted(H["e"], t + int(lat), "right")) - 1
        print(f"t={t} side={'SELL' if side > 0 else 'BUY '} trade_p={P['tr_p'][i_tr]:.6g} q={cand[2][k]:.6g} c={P['cq'][i_tr]:.2f} "
              f"H@quote {H['bid'][ih_q]:.6g}/{H['ask'][ih_q]:.6g} H@hedge {H['bid'][ih_f]:.6g}/{H['ask'][ih_f]:.6g} "
              f"edge={edge:.2f} slip={slip:.2f} size={size:.1f} net={net:.2f} hold={hold / 1000:.1f}s")


if __name__ == "__main__":
    a = sys.argv[1:]
    main(a[0], a[1], int(a[2]), int(a[3]), a[4], a[5], a[6], *(a[7:8] or [0]), *(a[8:9] or [300]), *(a[9:10] or ["tt"]), *(a[10:11] or [8]))
