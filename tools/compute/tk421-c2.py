#!/usr/bin/env python3
"""TK-421: контроли C2 (протокол v6 §Контроли: ближайшие промахи). Для события — до --per окон той же монеты, того же знака и T_min:
минуты с a_T в верхнем дециле значений ниже a*_T (по монете-месяцу-T-знаку), эпизоды (окна [t2-T,t2] слиты), без пересечения с ЛЮБЫМ событием монеты ±60 мин
и между собой. Границы окна — как у событий (bounds + инвариант пары). Порядок: эпизоды по убыванию a, детерминированно. Нет --per → меньше, диагностика.
Использование: tk421-c2.py <свечи> <b1-thresh.json> <pump-events.csv> <выход.csv> <месяц> <art.json> [--per 3] [--procs 15] [--diag d.json]"""
import bisect, csv, importlib.util, json, math, os, sys
from collections import defaultdict
from multiprocessing import Pool

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(n, f):
    sp = importlib.util.spec_from_file_location(n, os.path.join(HERE, f))
    m = importlib.util.module_from_spec(sp)
    sp.loader.exec_module(m)
    return m


ev = _load("ev", "tk421-events.py")
mv = ev.mv
MIN, GUARD = mv.MIN, 60 * mv.MIN
PER = 3
TH = {}


def one(arg):
    ex, mk, d, sym, t0, t1, mkey, evs = arg
    t, h, l, c, q = ev.load_q(os.path.join(d, sym + ".csv.gz"), t0, t1)
    res, diag = [], []
    if len(t) < 100:
        return res, [(e["id"], 0, "мало минут") for e in evs]
    busy = [(int(e["t_start_ms"]) - GUARD, int(e["t_end_ms"]) + GUARD) for e in evs]
    groups = defaultdict(list)
    for e in evs:
        groups[(e["знак"], int(e["T_min"]))].append(e)
    for (sign, T), lst in sorted(groups.items()):
        up = sign == "+"
        hh, ll = (h, l) if up else ([1 / x for x in l], [1 / x for x in h])
        A = mv.moves(t, hh, ll, T, True)
        astar = TH.get("%s|%d%s|м" % (mkey, T, sign))
        vals = sorted(a for a in A if a is not None and a < astar)
        if not vals:
            diag += [(e["id"], 0, "нет окон ниже порога") for e in lst]
            continue
        q90 = vals[int(len(vals) * 0.9)]
        cand = [i for i in range(len(t)) if A[i] is not None and q90 <= A[i] < astar]
        eps, cur = [], None
        for i in cand:
            if cur and t[i] - T * MIN <= t[cur[1]]:
                cur[1] = i
                if A[i] > A[cur[2]]:
                    cur[2] = i
            else:
                if cur:
                    eps.append(cur)
                cur = [i, i, i]
        if cur:
            eps.append(cur)
        built = []
        for _, _, ipk in eps:
            ist, iend, typ = ev.bounds(t, hh, ll, ipk, T, astar)
            jlo = bisect.bisect_left(t, t[ipk] - T * MIN)
            if ipk > jlo:
                ist = min(ist, min(range(jlo, ipk), key=lambda j: ll[j]))
            lo, hi = t[ist], t[iend]
            if any(lo - GUARD <= b1 and hi + GUARD >= b0 for b0, b1 in busy):
                continue
            a = math.log(hh[ipk] / ll[ist])
            built.append((A[ipk], ist, ipk, iend, typ, a))
        built.sort(key=lambda x: (-x[0], x[2]))
        used = []
        for e in lst:
            n = 0
            for b in built:
                if n >= PER:
                    break
                lo, hi = t[b[1]], t[b[3]]
                if any(lo <= u1 and hi >= u0 for u0, u1 in used):
                    continue
                used.append((lo, hi))
                n += 1
                move = math.exp(b[5]) - 1 if up else math.exp(-b[5]) - 1
                res.append({"id": "C2-%s-%d" % (e["id"], n), "биржа": ex, "рынок": mk, "инструмент": sym, "t_start": lo, "t_peak": t[b[2]],
                            "t_end": hi, "t_pre": lo - 1440 * MIN, "знак": sign, "роль": "C2", "event_id": e["id"], "T_min": T,
                            "a": round(b[5], 4), "a_порог": astar, "ход_%": round(move * 100, 2), "q90": round(q90, 4), "конец": b[4]})
            diag.append((e["id"], n, "ok"))
    return res, diag


def main():
    global PER
    a = sys.argv[1:]
    root, thf, evf, outf, ym, art = a[:6]
    procs, diagf = 15, None
    if "--per" in a:
        PER = int(a[a.index("--per") + 1])
    if "--procs" in a:
        procs = int(a[a.index("--procs") + 1])
    if "--diag" in a:
        diagf = a[a.index("--diag") + 1]
    mv.load_excl(art)
    for k, v in json.load(open(thf))["результат"].items():
        TH[k] = v["x_star"] if isinstance(v, dict) else None
    t0, t1 = mv.month_bounds(ym)
    byi = defaultdict(list)
    for r in csv.DictReader(open(evf, encoding="utf-8")):
        byi[(r["биржа"], r["рынок"], r["монета"])].append(r)
    jobs = []
    for ex, mk, sub in mv.MKT:
        for (e_, m_, sym), evs in byi.items():
            if (e_, m_) == (ex, mk):
                jobs.append((ex, mk, os.path.join(root, sub), sym, t0, t1, sub, evs))
    cols = ["id", "биржа", "рынок", "инструмент", "t_start", "t_peak", "t_end", "t_pre", "знак", "роль", "event_id", "T_min", "a", "a_порог",
            "ход_%", "q90", "конец"]
    allr, alld = [], []
    with Pool(procs) as p:
        for r, d in p.imap_unordered(one, jobs, chunksize=2):
            allr += r
            alld += d
    allr.sort(key=lambda r: r["id"])
    with open(outf, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, cols)
        w.writeheader()
        w.writerows(allr)
    got = defaultdict(int)
    for i, n, _ in alld:
        got[i] = n
    ne = sum(len(v) for v in byi.values())
    short = sorted(i for i, n in got.items() if n < PER)
    out = {"событий": ne, "окон_C2": len(allr), "событий_с_полным_набором": ne - len(short), "доля_не_полных": round(len(short) / ne, 4),
           "не_полных": short, "причины": sorted({x[2] for x in alld if x[2] != "ok"})}
    if diagf:
        json.dump(out, open(diagf, "w"), ensure_ascii=False, indent=1)
    print({k: v for k, v in out.items() if k != "не_полных"})


if __name__ == "__main__":
    main()
