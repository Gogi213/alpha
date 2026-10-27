#!/usr/bin/env python3
"""TK-420 вар. 3, признак D2 «ускорение потока» (протокол docs/research/P-TK420-pump-dynamics-end-month2-2026-10-27.md, утверждён Судьёй).
D2(N) = q[u] / mean(q[u−N..u−1]), u = τ−1 (последняя завершённая минута), q — оборот минуты в USD из свечей 1 м (TK-411; = Σцена·кол-во сделок минуты, как B+S в tk414-flow.py; минуты без сделок = 0).
Выбранные: D2 ≥ верхнего тертиля (66,7-й процентиль) тех же отношений в минутах u базы [t−минB−360 мин, t−минB) своей монеты (≥60 значений, знаменатель > 0).
Оборот без стороны, поэтому «по знаку события» к D2 неприменимо. Вход: d1-X.csv (tk420-var3-d1.py) -> те же колонки + d2_N, thr2_N, sel_D2_N; колонки sel_5/sel_15 D1 переименованы в sel_D1_5/sel_D1_15.
Использование: tk420-var3-d2.py <биржа> <рынок> <d1-X.csv> <корень свечей> <выход.csv> [процессов=15]"""
import csv, importlib.util, os, sys
from datetime import datetime, timezone
from multiprocessing import Pool

HERE = os.path.dirname(os.path.abspath(__file__))
_s = importlib.util.spec_from_file_location("tk413", os.path.join(HERE, "tk413-episodes.py"))
T13 = importlib.util.module_from_spec(_s)
_s.loader.exec_module(T13)
NS = (5, 15)


def work(arg):
    root, ex, mk, sym, evs = arg
    try:
        t, c, q, n, w = T13.load(root, ex, mk, sym)
    except FileNotFoundError:
        t = []
    m0 = t[0] // 60000 if t else 0
    mq = {x // 60000 - m0: qi for x, qi in zip(t, q)}
    nm = (t[-1] // 60000 - m0 + 1) if t else 0
    arr = [mq.get(i, 0.0) for i in range(nm)]
    cum = [0.0]
    for v in arr:
        cum.append(cum[-1] + v)
    out = []
    for r in evs:
        o = dict(r)
        for N in NS:
            o["d2_%d" % N] = o["thr2_%d" % N] = o["sel_D2_%d" % N] = ""
        if not t:
            out.append(o); continue
        te = int(datetime.strptime(r["t_событие"], "%Y-%m-%dT%H:%M").replace(tzinfo=timezone.utc).timestamp()) // 60 - m0
        mb = int(r["минB"])

        def ratio(u, N):
            if u - N < 0 or u >= nm:
                return None
            den = (cum[u] - cum[u - N]) / N
            return arr[u] / den if den > 0 else None

        for N in NS:
            d2 = ratio(te - 1, N)
            if d2 is None:
                continue
            vals = [x for x in (ratio(u, N) for u in range(te - mb - 360, te - mb)) if x is not None]
            o["d2_%d" % N] = "%.5f" % d2
            if len(vals) >= 60:
                vs = sorted(vals); qq = 2 * (len(vs) - 1) / 3; k = int(qq)
                th = vs[k] + (vs[min(k + 1, len(vs) - 1)] - vs[k]) * (qq - k)
                o["thr2_%d" % N] = "%.5f" % th
                o["sel_D2_%d" % N] = "1" if d2 >= th else "0"
        out.append(o)
    return out


def main():
    ex, mk, src, root, outp = sys.argv[1:6]
    nproc = int(sys.argv[6]) if len(sys.argv) > 6 else 15
    rows = list(csv.DictReader(open(src, newline="", encoding="utf-8")))
    by = {}
    for r in rows:
        for N in NS:
            r["sel_D1_%d" % N] = r.pop("sel_%d" % N)
        by.setdefault(r["инструмент"], []).append(r)
    with Pool(nproc) as p:
        res = [x for ch in p.imap_unordered(work, [(root, ex, mk, k, v) for k, v in by.items()]) for x in ch]
    with open(outp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, list(res[0].keys())); w.writeheader(); w.writerows(res)
    for N in NS:
        print("N=%d: событий %d, D2 есть %d, выбрано %d" % (N, len(res), sum(1 for x in res if x["d2_%d" % N] != ""), sum(1 for x in res if x["sel_D2_%d" % N] == "1")))


if __name__ == "__main__":
    main()
