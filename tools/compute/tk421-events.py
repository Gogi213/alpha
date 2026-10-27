#!/usr/bin/env python3
"""TK-421 шаг B2a (протокол P-TK421 v5): события L0 (V0, объединение по T) + признаки воронки по инструменту.
Использование: tk421-events.py <корень свечей> <b1-thresh.json> <выход-каталог> [процессов] [макс. инструментов] [месяц]
Минута t2 проходит, если a_T(t2) >= a*_T (b1, срез «м», по бирже-рынку, знаку) хотя бы на одном T; окна [t2-T, t2]
перекрылись -> одно событие. Дамп = памп на инверсной цене (1/low, 1/high). Пик/старт/конец — «Границы события» протокола.
Признаки: κ = ход / медиана суточного размаха монеты; m = Σquote_v[старт,пик] / (медиана суточного оборота/1440 × минут);
дормантность: оборот и σ минутных логдоходностей за 1440 мин до старта относительно медиан суток монеты (окно полное —
иначе pre_полное=0, L1 не проходит); qh — часы UTC, где у инструмента есть проходящая минута (для доли монет рынка).
Оговорка: m и σ_до — по медианам суток (не по окнам равной длины), Gate-минуты без сделок как есть (В-172)."""
import bisect
import gzip
import importlib.util
import json
import math
import os
import statistics
import sys
from multiprocessing import Pool

HERE = os.path.dirname(os.path.abspath(__file__))
sp = importlib.util.spec_from_file_location("mv", os.path.join(HERE, "tk421-moves.py"))
mv = importlib.util.module_from_spec(sp)
sp.loader.exec_module(mv)
TS, MIN, DAY = mv.TS, mv.MIN, mv.DAY
TH = {}


def load_q(path, t0, t1):
    t, h, l, c, q = [], [], [], [], []
    with gzip.open(path, "rt") as f:
        f.readline()
        for line in f:
            p = line.split(",", 7)
            ti = int(p[0])
            if ti < t0 or ti >= t1:
                continue
            hi, lo, ci = float(p[2]), float(p[3]), float(p[4])
            if ci <= 0 or lo <= 0 or hi <= 0:
                continue
            t.append(ti); h.append(hi); l.append(lo); c.append(ci); q.append(float(p[6] or 0))
    return t, h, l, c, q


def bounds(t, h, l, ipk, tmin, astar):
    """старт и конец вокруг пика ipk (логика пампа: h — цена, l — минимум)."""
    w = tmin * MIN
    j, lowmin, ilow, last_ext = ipk, l[ipk], ipk, t[ipk]
    while j > 0:
        j -= 1
        if l[j] <= lowmin:
            lowmin, ilow, last_ext = l[j], j, t[j]
        elif math.log(h[j] / lowmin) >= astar or last_ext - t[j] >= w:
            break
    n = len(t)
    j, pk, last_hi, iend, typ = ipk, h[ipk], t[ipk], ipk, "плато"
    while j + 1 < n:
        j += 1
        if h[j] > pk:
            pk, last_hi = h[j], t[j]
        iend = j
        if math.log(pk / l[j]) >= astar:
            typ = "откат"
            break
        if t[j] - last_hi >= w:
            break
    return ilow, iend, typ


def one(arg):
    ex, mk, d, sym, t0, t1, mkey = arg
    t, h, l, c, q = load_q(os.path.join(d, sym + ".csv.gz"), t0, t1)
    base = {"биржа": ex, "рынок": mk, "инструмент": sym, "минут": len(t)}
    if len(t) < 100:
        return dict(base, пропуск="мало минут", events=[], qh={})
    nd = (t1 - t0) // DAY
    dr, dq = {}, {}
    for i, ti in enumerate(t):
        k = (ti - t0) // DAY
        r = dr.setdefault(k, [0.0, 1e300])
        r[0] = max(r[0], h[i]); r[1] = min(r[1], l[i])
        dq[k] = dq.get(k, 0.0) + q[i]
    med_rng = statistics.median(v[0] / v[1] - 1 for v in dr.values())
    med_q = statistics.median(dq.get(k, 0.0) for k in range(nd))
    rets = {}
    for i in range(1, len(t)):
        rets.setdefault((t[i] - t0) // DAY, []).append(math.log(c[i] / c[i - 1]))
    sig_day = [statistics.pstdev(v) for v in rets.values() if len(v) > 30]
    med_sig = statistics.median(sig_day) if sig_day else 0.0
    cq = [0.0]
    for x in q:
        cq.append(cq[-1] + x)
    out = {**base, "med_rng": med_rng, "med_q": med_q, "med_sig": med_sig, "events": [], "qh": {}}
    for sign, up in (("+", True), ("-", False)):
        hh, ll = (h, l) if up else ([1 / x for x in l], [1 / x for x in h])
        A = {T: mv.moves(t, hh, ll, T, True) for T in TS}
        qual = []
        for i in range(len(t)):
            for T in TS:
                th = TH.get("%s|%d%s|м" % (mkey, T, sign))
                if th is not None and A[T][i] >= th:
                    qual.append((i, T))
                    break
        out["qh"][sign] = sorted({(t[i] - t0) // 3600000 for i, _ in qual})
        eps, cur = [], None
        for i, T in qual:
            if cur and t[i] - T * MIN <= t[cur[1]]:
                cur[1] = i
                cur[2] = min(cur[2], T)
            else:
                if cur:
                    eps.append(cur)
                cur = [i, i, T, T]  # первый, последний, T_min, T первой
        if cur:
            eps.append(cur)
        for i0, i1, tmin, t_first in eps:
            lo_i = bisect.bisect_left(t, t[i0] - t_first * MIN)
            ipk = max(range(lo_i, i1 + 1), key=lambda j: hh[j])
            astar = TH["%s|%d%s|м" % (mkey, tmin, sign)]
            ist, iend, typ = bounds(t, hh, ll, ipk, tmin, astar)
            a = math.log(hh[ipk] / ll[ist])
            move = (math.exp(a) - 1) * (1 if up else -1)
            dur = max(1, (t[iend] - t[ist]) // MIN)
            legm = max(1, (t[ipk] - t[ist]) // MIN)
            m = (cq[ipk + 1] - cq[ist]) / (med_q / 1440 * legm) if med_q > 0 else None
            tpre = t[ist] - 1440 * MIN
            full = tpre >= t0
            pre_q = pre_sig = flat = None
            if full:
                a0 = bisect.bisect_left(t, tpre)
                pre_q = (cq[ist] - cq[a0]) / med_q if med_q > 0 else None
                cc = c[a0:ist]
                if len(cc) > 30 and med_sig > 0:
                    pre_sig = statistics.pstdev([math.log(cc[k] / cc[k - 1]) for k in range(1, len(cc))]) / med_sig
                flat = sum(1 for k in range(a0, ist) if h[k] == l[k]) / max(1, ist - a0)
            out["events"].append({
                "знак": sign, "t_start": t[ist], "t_peak": t[ipk], "t_end": t[iend], "ход_%": round(move * 100, 2),
                "a": round(a, 4), "длит_мин": dur, "T_min": tmin, "конец": typ,
                "κ": round(abs(move) / med_rng, 3) if med_rng > 0 else None,
                "m": round(m, 3) if m is not None else None,
                "pre_полное": int(full), "оборот_до_отн": None if pre_q is None else round(pre_q, 4),
                "σ_до_отн": None if pre_sig is None else round(pre_sig, 4),
                "плоских_до": None if flat is None else round(flat, 3),
                "aT": {T: round(A[T][ipk], 4) for T in TS}})
    return out


def main():
    root, thf, outd = sys.argv[1:4]
    procs = int(sys.argv[4]) if len(sys.argv) > 4 else 15
    lim = int(sys.argv[5]) if len(sys.argv) > 5 else 0
    ym = sys.argv[6] if len(sys.argv) > 6 else "2026-09"
    for k, v in json.load(open(thf))["результат"].items():
        TH[k] = v["x_star"] if isinstance(v, dict) else None
    t0, t1 = mv.month_bounds(ym)
    os.makedirs(outd, exist_ok=True)
    with Pool(procs) as pool:
        for ex, mk, sub in mv.MKT:
            d = os.path.join(root, sub)
            syms = sorted(f[:-7] for f in os.listdir(d) if f.endswith(".csv.gz"))
            if lim:
                syms = syms[:lim]
            n = ne = 0
            with open(os.path.join(outd, "events-%s-%s-%s.jsonl" % (ym, ex, mk)), "w") as fo:
                for r in pool.imap_unordered(one, [(ex, mk, d, s, t0, t1, sub) for s in syms], chunksize=4):
                    fo.write(json.dumps(r, ensure_ascii=False) + "\n")
                    n += 1
                    ne += len(r["events"])
            print(ex, mk, n, "инструментов", ne, "событий L0", flush=True)


if __name__ == "__main__":
    main()
