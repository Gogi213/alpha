"""Проверяющий arb-tokyo: независимый пересчёт жизни расхождения ≥ θ для выборки пар (чистый Python-цикл по событиям, свой центр), сравнение с life.csv отчёта.
Запуск: python3 indep_life.py <книги parsed/> <t0> <t1> COIN:exA:exB ...
"""
import glob, json, sys, statistics
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).parent))
from arb_live import symmap_for

CAP = 60_000


def load(work, ex, sym, mult):
    fs = sorted(glob.glob(f"{work}/{ex}/{sym}.*.npz"))
    rows = []
    for f in fs:
        z = np.load(f)
        rows += list(zip(z["e"].tolist(), z["rx"].tolist(), z["bid"].tolist(), z["ask"].tolist()))
    rows.sort()
    out, last = [], None
    for e, rx, b, a in rows:
        if b <= 0 or a <= 0 or b > a:
            continue
        if last is not None and (b, a) == last:
            continue
        last = (b, a)
        out.append((e, b / mult, a / mult))
    return out


def life(A, B, t0, t1, th):
    # объединённый ряд по моментам изменения любой ноги (одна строка на мс-метку)
    i = j = 0
    ser = []
    times = sorted(set([x[0] for x in A] + [x[0] for x in B]))
    for t in times:
        if t >= t1:
            break
        while i + 1 < len(A) and A[i + 1][0] <= t:
            i += 1
        while j + 1 < len(B) and B[j + 1][0] <= t:
            j += 1
        if A[i][0] > t or B[j][0] > t:
            continue
        ok = t - A[i][0] <= CAP and t - B[j][0] <= CAP
        S = (A[i][1] / B[j][2] - 1) * 1e4
        Bu = (A[i][2] / B[j][1] - 1) * 1e4
        ser.append((t, S, Bu, ok))
    # центр: медиана закрытий (S+Bu)/2 минутных баров [m−60, m−1] по годным строкам, ≥ 30 баров
    close = {}
    for t, S, Bu, ok in ser:
        if ok:
            close[t // 60000] = (S + Bu) / 2
    cen = {}
    durs = {1: [], -1: []}
    st = {1: None, -1: None}
    for t, S, Bu, ok in ser:
        m = t // 60000
        if m not in cen:
            w = [close[k] for k in range(m - 60, m) if k in close]
            cen[m] = statistics.median(w) if len(w) >= 30 else None
        c = cen[m]
        for side, E in ((1, None if c is None else S - c), (-1, None if c is None else c - Bu)):
            on = ok and t >= t0 and E is not None and E >= th
            if on and st[side] is None:
                st[side] = t
            elif not on and st[side] is not None:
                durs[side].append(t - st[side])
                st[side] = None
    d = durs[1] + durs[-1]
    return d


def main(work, t0, t1, *specs):
    symmap, _ = symmap_for("2026-11-16")
    inv = {}
    for ex, m in symmap.items():
        for s, (c, mult) in m.items():
            inv.setdefault((c, ex), (s, mult))
    res = {}
    for sp in specs:
        coin, a, b = sp.split(":")
        A, B = load(work, a, *inv[(coin, a)]), load(work, b, *inv[(coin, b)])
        for th in (20.0, 30.0):
            d = np.array(life(A, B, t0, t1, th), float)
            r = {"n": len(d), "p10": float(np.percentile(d, 10)), "p50": float(np.percentile(d, 50)), "p90": float(np.percentile(d, 90)),
                 "gt": [round(100 * float((d > L).mean()), 1) for L in (1, 2, 5, 10, 20, 50)]}
            res[f"{sp}:{th}"] = r
            print(sp, th, r, flush=True)
    json.dump(res, open("/data/arb-tokyo-review/indep_life.json", "w"), indent=1)


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), *sys.argv[4:])
