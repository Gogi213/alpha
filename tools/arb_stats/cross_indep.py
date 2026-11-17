"""Независимый пересчёт выходов «через центр» и «переворот» (проверяющий): свой код по событиям, чистым Python, без cross.py / outlier.py / core.py
(общая только загрузка книг maker_run.load_book). Сравнение с /data/arb-cross/cross.pkl по выборке пар: закрыто, не закрыто, Σ чистого итога, $/сут.

Правила — словами отчёта: Sell = bid_A/ask_B − 1, Buy = ask_A/bid_B − 1 (bps); центр — медиана середин закрытия минутных баров за [m−60, m−1], ≥ 30 баров;
вход +1: Sell − c ≥ θ, −1: c − Buy ≥ θ (при равенстве времени — +1); выход +1: c − Buy ≥ x, −1: Sell − c ≥ x; после выхода вход разрешён, когда середина
вернулась через центр; «переворот» — выход по x = θ и тут же обратный вход по тем же ценам; комиссия 4 × 3,15 bps на круг; размер ≤ 100 $ и ≤ объёма вершины.
Запуск: python3 cross_indep.py <каталог книг parsed> <cross.pkl> <выход.json> [монеты через запятую]; DEAD — пропуски сбора.
"""
import bisect
import json
import os
import pickle
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import maker_run  # noqa: E402
from arb_live import symmap_for  # noqa: E402
from common import EXCHANGES, load_pool, norm  # noqa: E402

FEE = 4 * 3.5 * 0.9


def series(A, B, t1):
    ea, eb = A["e"].tolist(), B["e"].tolist()
    ba, aa, bb, ab = A["bid"].tolist(), A["ask"].tolist(), B["bid"].tolist(), B["ask"].tolist()
    T = sorted(set(ea) | set(eb))
    out = []
    ia = ib = -1
    for t in T:
        if t >= t1:
            break
        while ia + 1 < len(ea) and ea[ia + 1] <= t:
            ia += 1
        while ib + 1 < len(eb) and eb[ib + 1] <= t:
            ib += 1
        if ia < 0 or ib < 0:
            continue
        out.append((t, (ba[ia] / ab[ib] - 1) * 1e4, (aa[ia] / bb[ib] - 1) * 1e4))
    return out


def centres(ser):
    close = {}
    for t, s, b in ser:
        close[t // 60000] = (s + b) / 2
    cen = {}
    for m in close:
        w = [close[k] for k in range(m - 60, m) if k in close]
        cen[m] = statistics.median(w) if len(w) >= 30 else None
    return cen


class Book:
    def __init__(self, a):
        self.e, self.bid, self.ask, self.db, self.da = (a[k].tolist() for k in ("e", "bid", "ask", "dbid", "dask"))

    def at(self, t):
        return bisect.bisect_right(self.e, t) - 1


def px(A, B, t):
    i, j = A.at(t), B.at(t)
    return (A.bid[i] / B.ask[j] - 1) * 1e4, (A.ask[i] / B.bid[j] - 1) * 1e4, i, j


def run(ser, cen, A, B, t0, t1, th, x, mode, lat):
    """mode: x — через центр с порогом выхода x; flip; ct — к центру (без перевзвода). -> список кругов (ts, net, size) и не закрыто."""
    n = len(ser)
    Ts = [r[0] for r in ser]
    c = [cen.get(t // 60000) for t in Ts]
    rounds, k, pos = [], bisect.bisect_left(Ts, t0), None
    armed, t_free = True, t0
    while k < n:
        t, s, b = ser[k]
        ck = c[k]
        if pos is None:
            if armed is not True:       # ждём возврата середины через центр
                if ck is not None and armed * ((s + b) / 2 - ck) <= 0:
                    armed = True
                else:
                    k += 1
                    continue
            if ck is None or t < t0 or t < t_free:
                k += 1
                continue
            side = 1 if s - ck >= th else (-1 if ck - b >= th else 0)
            if not side:
                k += 1
                continue
            tx = t + lat
            if tx >= t1:
                break
            sx, bx, i, j = px(A, B, tx)
            edge = sx if side > 0 else bx
            size = min(A.db[i], B.da[j]) if side > 0 else min(A.da[i], B.db[j])
            pos = (t, side, edge, size, tx)
            k = max(bisect.bisect_right(Ts, tx) - 1, 0)
            continue
        ts, side, edge, size, tx = pos
        hit = False
        if ck is not None:
            if mode == "ct":
                hit = side * ((s + b) / 2 - ck) <= 0
            else:
                xx = th if mode == "flip" else x
                hit = (ck - b >= xx) if side > 0 else (s - ck >= xx)
        if not hit:
            k += 1
            continue
        te = max(t, tx) + lat
        if te >= t1:
            break
        sx, bx, i, j = px(A, B, te)
        gross = edge - bx if side > 0 else sx - edge
        rounds.append((ts, te, gross - FEE, size))
        pos, t_free = None, te
        if mode == "flip":
            ns = -side
            pos = (max(t, tx), ns, sx if ns > 0 else bx, min(A.db[i], B.da[j]) if ns > 0 else min(A.da[i], B.db[j]), te)
            k = max(bisect.bisect_right(Ts, te) - 1, 0)
            continue
        if mode == "x":
            armed = -side         # возврат через центр: множитель m, вход разрешён после m·(mid − c) ≤ 0 (закрыта +1 — середина ≥ центра)
        k = bisect.bisect_left(Ts, te) if mode == "ct" else k
    return rounds, int(pos is not None)


def main(bwork, pkl, outp, coins_arg):
    day = time.strftime("%Y-%m-%d", time.gmtime(1794778200000 / 1000))
    symmap, day = symmap_for(day)
    meta = {ex: load_pool(ex, day)[1] for ex in EXCHANGES}
    coins = {}
    for ex, m in symmap.items():
        for sym, (coin, mult) in m.items():
            cur = coins.setdefault(coin, {}).get(ex)
            exact = norm(ex, sym)[0] == coin
            if cur is None or (exact and not cur[2]):
                coins[coin][ex] = (sym, mult, exact)
    ref = pickle.load(open(pkl, "rb"))
    t0, t1, rows = ref["t0"], ref["t1"], ref["rows"]
    dead = json.load(open(os.environ["DEAD"])) if os.environ.get("DEAD") else {}
    win_d = (t1 - t0) / 86_400_000
    res = []
    for coin in coins_arg.split(","):
        bk = {}
        for ex, (sym, mult, _) in coins[coin].items():
            if ex == "bingx":
                continue
            unit = float((meta[ex].get(sym) or {}).get("ctVal") or 1.0) if ex == "okx" else 1.0
            a = maker_run.load_book(bwork, ex, sym, mult, unit)
            if a is not None and len(a["e"]) >= 20:
                bk[ex] = a
        exs = [e for e in EXCHANGES if e in bk]
        for ii, a_ in enumerate(exs):
            for b_ in exs[ii + 1:]:
                if ("tt", coin, a_, b_, (20.0, 0, "x1-t")) not in rows:
                    continue
                tt = time.time()
                ser = series(bk[a_], bk[b_], t1)
                cen = centres(ser)
                A, B = Book(bk[a_]), Book(bk[b_])
                dv = sorted([(s, e + 120_000) for ex in (a_, b_) for s, e in dead.get(ex, [])])
                for th, lat, nm, mode, x in ((20.0, 0, "x1-t", "x", 20.0), (20.0, 0, "x2-t", "x", 10.0), (20.0, 0, "flip", "flip", 20.0),
                                             (20.0, 0, "c-t", "ct", 0.0), (10.0, 0, "x2-t", "x", 5.0), (10.0, 0, "flip", "flip", 10.0),
                                             (20.0, 50, "x1-t", "x", 20.0)):
                    rr, un = run(ser, cen, A, B, t0, t1, th, x, mode, lat)
                    rr = [r for r in rr if not any(s < r[1] + lat and e > r[0] for s, e in dv)]
                    cap = sum(min(r[3], 100.0) * r[2] / 1e4 for r in rr) / win_d
                    rf = rows.get(("tt", coin, a_, b_, (th, lat, nm)))
                    res.append({"coin": coin, "a": a_, "b": b_, "th": th, "lat": lat, "exit": nm, "closed": len(rr), "unclosed": un,
                                "net_med": statistics.median([r[2] for r in rr]) if rr else None, "usd": cap,
                                "ref_closed": rf and rf["closed"], "ref_unclosed": rf and rf["unclosed"], "ref_usd": rf and rf["usd_cap"],
                                "ref_net_med": rf and rf["net"][0]})
                    print(json.dumps(res[-1]), round(time.time() - tt, 1), flush=True)
    json.dump(res, open(outp, "w"), indent=1)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4])
