#!/usr/bin/env python3
"""TK-444: события «тренд ≥ q99 за D мин» по свечам 1 м linear прошлых месяцев (упрощённый аналог v11, цена only).
events.py <каталог свечей <мес>/candles/bybit-linear> <мес> <out.csv>
D ∈ {90,180,360,720,1440}; ход + = max(high[t+1..t+D])/close[t]-1, − = симметрично по low; X_D = q99 по (D, знак) на пуле монет месяца
(в v11 порог — q99 сентября; здесь месяц свой, пул ≤16 монет). Жадно без перекрытий внутри (монета, D, знак): старт, затем пропуск до t_peak.
Формат строк как pump-v11-events-*.csv (нужные скрипту поля)."""
import sys, glob, gzip, os, csv
import numpy as np
from scipy.ndimage import maximum_filter1d, minimum_filter1d
DS = (90, 180, 360, 720, 1440)

def load(p):
    a = np.genfromtxt(gzip.open(p, "rt"), delimiter=",", skip_header=1, usecols=(0, 2, 3, 4))
    if a.ndim < 2 or len(a) < 2000: return None
    t = (a[:, 0] // 60000).astype(np.int64); t0 = t[0]; n = t[-1] - t0 + 1
    h = np.full(n, np.nan); l = np.full(n, np.nan); c = np.full(n, np.nan)
    h[t - t0] = a[:, 1]; l[t - t0] = a[:, 2]; c[t - t0] = a[:, 3]
    last = np.nan
    for i in range(n):
        if np.isnan(c[i]): c[i] = last; h[i] = l[i] = last
        else: last = c[i]
    return t0, h, l, c

def fwd(arr, D, fn):
    # fn по окну [t+1, t+D]: фильтр по окну D с центром -> сдвиг
    w = fn(arr, size=D, origin=-(D // 2), mode="nearest")  # окно [t, t+D-1] при origin = -(D//2) для нечётного/чётного размера
    return np.roll(w, -1)

def main(cdir, month, out):
    data = {}
    for p in sorted(glob.glob(cdir + "/*.csv.gz")):
        sym = os.path.basename(p)[:-7]
        r = load(p)
        if r is not None: data[sym] = r
    mv = {}
    for sym, (t0, h, l, c) in data.items():
        for D in DS:
            up = fwd(h, D, maximum_filter1d) / c - 1; dn = fwd(l, D, minimum_filter1d) / c - 1
            up[-D:] = np.nan; dn[-D:] = np.nan
            mv[(sym, D)] = (up, -dn)
    rows = []
    for D in DS:
        for sg in (0, 1):
            allv = np.concatenate([mv[(s, D)][sg][~np.isnan(mv[(s, D)][sg])] for s in data])
            X = np.quantile(allv, .99)
            for sym, (t0, h, l, c) in data.items():
                v = mv[(sym, D)][sg]; i = 0; n = len(v)
                cand = np.where(v >= X)[0]
                skip = -1
                for i in cand:
                    if i <= skip: continue
                    seg = (h if sg == 0 else -l)[i + 1:i + D + 1]
                    k = int(np.argmax(seg)) + i + 1
                    skip = k
                    rows.append((D, "%s|%s|%d" % (sym, "+-"[sg], t0 + i), sym, "+-"[sg], (t0 + i) * 60000, (t0 + k) * 60000, k - i, float(v[i]) * 100, X * 100))
    with open(out, "w", encoding="utf-8", newline="") as o:
        w = csv.writer(o)
        w.writerow(["D_мин", "id", "биржа_рынок", "инструмент", "знак", "t_start_ms", "t_peak_ms", "длит_мин", "ход_%", "X_%"])
        for D, i, sym, sg, a, b, d, m, X in rows:
            w.writerow([D, "bybit-linear|" + i, "bybit-linear", sym, sg, a, b, d, "%.3f" % m, "%.3f" % X])
    print(month, len(rows), "событий;", len(data), "монет")

if __name__ == "__main__":
    main(*sys.argv[1:4])
