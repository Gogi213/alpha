#!/usr/bin/env python3
"""TK-435 (2): меры волатильности окна события v11 (черновик под протокол v11 §2, параметры — по умолчанию).
Использование: tk435-vol.py <windows-*.csv (tk435-windows.py)> <D> <X> <выход.csv> <корень свечей> [ещё корни (август)…] [--procs 15] [--limit N]
  [--cube <корень куба tk436-cube.py>] — свечи из куба (memmap) вместо разбора csv.gz; корни свечей не читаются; сетка и выход те же, что без --cube
  [--cut <абс. минута>] — без куба: отбросить свечи раньше минуты (для гейта «было/стало» с кубом: --cut = первая минута куба, файл <куб>/<sub>.lo)
События = строки с исключено="" и представитель=1 для заданных D (мин) и X (%). Для каждого события и до PER=5 контролей
(та же монета-рынок, та же длина окна, тот же час UTC, ±30 сут, окно [старт−60, пик] не пересекает ни одного события инструмента;
md5 seed 433 — как tk433-features.py) считаются 22 варианта в двух положениях: pre — окно кончается в t_start, in — в t_peak:
  NATR1_N, N ∈ {14,60,180,720,1440} (среднее TR по 1 м свечам за N свечей / close ×100); NATR5_N, N ∈ {12,18,36,72,288} (5 м);
  RV1_w, RV5_w = √Σr² (1 м / 5 м close, w ∈ {60, 90} мин); PK_w, GK_w (5 м h/l[/o/c]); RNG_w = (max h − min l)/close; MAD_w = MAD×1,4826
  1 м логдоходностей за вычетом среднего. Колонки на вариант: <в>_<pre|in> (событие), <в>_<pre|in>_ctl (медиана контролей),
  <в>_<pre|in>_norm (значение события / медиана той же меры по точкам раз в час за 7 сут до старта вне окон событий; < 3 сут истории — пусто)."""
import csv
import gzip
import hashlib
import math
import os
import sys
from collections import defaultdict
from multiprocessing import Pool

import numpy as np

MIN = 60000
SEED, PRE, PER, SPAN, NORM_D, MIN_HIST_D = 433, 60, 5, 30, 7, 3
N1, N5, WS = (14, 60, 180, 720, 1440), (12, 18, 36, 72, 288), (60, 90)
VARS = (["NATR1_%d" % n for n in N1] + ["NATR5_%d" % n for n in N5] +
        ["%s_%d" % (m, w) for m in ("RV1", "RV5", "PK", "GK", "RNG", "MAD") for w in WS])
LN2 = math.log(2)
EXCL = {}
CUBE, CUT, _MM = None, 0, {}


def load_cube(sub, sym):
    """то же, что load(), из куба tk436: фильтр o,h,l,c > 0 как в load(); сетка — от суток первой свечи до конца суток последней."""
    if sub not in _MM:
        _MM[sub] = (np.load("%s/%s.npy" % (CUBE, sub), mmap_mode="r"), open("%s/%s.syms" % (CUBE, sub)).read().split(),
                    int(open("%s/%s.lo" % (CUBE, sub)).read()))
    mm, syms, lo = _MM[sub]
    if sym not in syms:
        return None
    x = np.array(mm[syms.index(sym)])
    if CUT > lo:  # --cut как в load(): свечи раньше минуты CUT не берутся (без --cut — вся история куба)
        x[:, :CUT - lo] = np.nan
    with np.errstate(invalid="ignore"):
        x[:, ~((x[0] > 0) & (x[1] > 0) & (x[2] > 0) & (x[3] > 0))] = np.nan
    ok = np.flatnonzero(~np.isnan(x[3]))
    if not len(ok):
        return None
    a, b = int(ok[0]) + lo, int(ok[-1]) + lo
    g0 = a // 1440 * 1440
    n = (b // 1440 + 1) * 1440 - g0
    A = np.full((5, n), np.nan)
    k0, k1 = g0 - lo, min(g0 - lo + n, x.shape[1])
    A[:, :k1 - k0] = x[:, k0:k1]
    return g0, A


def load(roots, sub, sym):
    """сетка минут (абс. минута − g0): o,h,l,c,q; g0 кратно 1440; несколько корней склеиваются."""
    if CUBE:
        return load_cube(sub, sym)
    rows = []
    for r in roots:
        fn = os.path.join(r, sub, sym + ".csv.gz")
        if os.path.exists(fn):
            with gzip.open(fn, "rt") as f:
                f.readline()
                for line in f:
                    p = line.split(",", 7)
                    ti = int(p[0])
                    o, h, l, c = float(p[1]), float(p[2]), float(p[3]), float(p[4])
                    if c > 0 and l > 0 and h > 0 and o > 0 and ti // MIN >= CUT:
                        rows.append((ti // MIN, o, h, l, c, float(p[6] or 0)))
    if not rows:
        return None
    rows.sort()
    m = np.array([r[0] for r in rows], dtype=np.int64)
    g0 = int(m[0]) // 1440 * 1440
    n = (int(m[-1]) // 1440 + 1) * 1440 - g0
    A = np.full((5, n), np.nan)
    A[:, m - g0] = np.array([r[1:] for r in rows]).T
    return g0, A


class Series:
    """префиксные суммы по сетке: вариант в окне, кончающемся в минуте e (абс.), длиной L мин — O(1) для средних."""

    def __init__(self, g0, A):
        o, h, l, c, q = A
        n = len(c)
        self.g0, self.n, self.c, self.h, self.l = g0, n, c, h, l
        pc = np.concatenate([[np.nan], c[:-1]])
        tr = np.fmax(np.fmax(h - l, np.abs(h - pc)), np.abs(l - pc))
        tr = np.where(np.isnan(pc), h - l, tr)
        self.tr1 = self.cs(tr / c * 100)
        r = np.log(c / pc)
        self.r1 = self.cs(r * r)
        self.r1v = r
        k = n // 5
        v = ~np.isnan(c[:k * 5].reshape(k, 5))
        ok = v.any(axis=1)
        with np.errstate(all="ignore"):
            h5 = np.nanmax(h[:k * 5].reshape(k, 5), axis=1)
            l5 = np.nanmin(l[:k * 5].reshape(k, 5), axis=1)
        first, last = v.argmax(axis=1), 4 - v[:, ::-1].argmax(axis=1)
        o5 = np.where(ok, o[:k * 5].reshape(k, 5)[np.arange(k), first], np.nan)
        c5 = np.where(ok, c[:k * 5].reshape(k, 5)[np.arange(k), last], np.nan)
        pc5 = np.concatenate([[np.nan], c5[:-1]])
        tr5 = np.fmax(np.fmax(h5 - l5, np.abs(h5 - pc5)), np.abs(l5 - pc5))
        tr5 = np.where(np.isnan(pc5), h5 - l5, tr5)
        self.tr5 = self.cs(tr5 / c5 * 100)
        r5 = np.log(c5 / pc5)
        self.r5 = self.cs(r5 * r5)
        self.pk = self.cs(np.log(h5 / l5) ** 2 / (4 * LN2))
        self.gk = self.cs(0.5 * np.log(h5 / l5) ** 2 - (2 * LN2 - 1) * np.log(c5 / o5) ** 2)

    @staticmethod
    def cs(x):
        return np.concatenate([[0.0], np.cumsum(np.nan_to_num(x))]), np.concatenate([[0], np.cumsum(~np.isnan(x))])

    @staticmethod
    def mean(S, a, b):
        """среднее по индексам [a, b) префикса; None если нет значений."""
        if a < 0 or b > len(S[0]) - 1 or b <= a:
            return None
        k = S[1][b] - S[1][a]
        return (S[0][b] - S[0][a]) / k if k >= max(1, (b - a) // 2) else None

    def val(self, var, e):
        """значение варианта в окне, кончающемся включительно в абс. минуте e."""
        j = e - self.g0 + 1                      # индекс-конец (исключая) на 1 м
        j5 = j // 5
        nm, _, p = var.partition("_")
        p = int(p)
        if nm == "NATR1":
            return self.mean(self.tr1, j - p, j)
        if nm == "NATR5":
            return self.mean(self.tr5, j5 - p, j5)
        if nm == "RV1":
            return self._sum(self.r1, j - p, j)
        if nm == "RV5":
            return self._sum(self.r5, j5 - p // 5, j5)
        if nm in ("PK", "GK"):
            m = self.mean(self.pk if nm == "PK" else self.gk, j5 - p // 5, j5)
            return math.sqrt(m) if m is not None and m > 0 else None
        if j - p < 0 or j > self.n:
            return None
        hs, ls, cs = self.h[j - p:j], self.l[j - p:j], self.c[j - p:j]
        if np.isnan(cs).sum() > p // 2:
            return None
        if nm == "RNG":
            return float((np.nanmax(hs) - np.nanmin(ls)) / cs[~np.isnan(cs)][-1])
        r = self.r1v[j - p:j]
        r = r[~np.isnan(r)]
        if len(r) < 5:
            return None
        x = np.sort(r - r.mean())
        return float(np.median(np.abs(x - x[len(x) // 2])) * 1.4826)

    def _sum(self, S, a, b):
        m = self.mean(S, a, b)
        return math.sqrt(m * (b - a)) if m is not None else None


def med(x):
    y = sorted(v for v in x if v is not None)
    return y[len(y) // 2] if y else None


def one(arg):
    sub, sym, evs, roots = arg
    ld = load(roots, sub, sym)
    if ld is None:
        return []
    S = Series(*ld)
    g0 = S.g0
    avail = ~np.isnan(S.c)
    first = int(np.argmax(avail)) + g0
    busy = sorted((int(e["t_start_ms"]) // MIN, int(e["t_peak_ms"]) // MIN) for e in evs)
    out = []
    for e in evs:
        a, b = int(e["t_start_ms"]) // MIN, int(e["t_peak_ms"]) // MIN
        dur = b - a
        eid = "%s|%s|%d" % (sub, sym, a)
        frac = int(avail[a - g0:b - g0 + 1].sum()) / (dur + 1)
        hs = a - a % 60
        cand = []
        for dd in range(-SPAN, SPAN + 1):
            if dd == 0:
                continue
            for k in range(0, 60, 5):
                s = hs + dd * 1440 + k
                if s - PRE < g0 or s + dur - g0 >= S.n or any(not (s + dur < b0 - PRE or s - PRE > b1) for b0, b1 in busy):
                    continue
                if not (avail[s - g0] and avail[s + dur - g0]) or int(avail[s - g0:s + dur - g0 + 1].sum()) < frac * (dur + 1):
                    continue
                cand.append((hashlib.md5(("%d|%s|%d" % (SEED, eid, s)).encode()).hexdigest(), s))
        cand.sort()
        got = []
        for _, s in cand:
            if all(s + dur < g - PRE or s - PRE > g + dur for g in got):
                got.append(s)
                if len(got) == PER:
                    break
        # норма: точки раз в час за 7 сут до старта вне окон событий; история < 3 сут — без нормы
        hist = (a - first) / 1440
        pts = [p for p in range(a - NORM_D * 1440, a, 60) if p - g0 >= 0 and not any(b0 - PRE <= p <= b1 + 90 for b0, b1 in busy)]
        row = {"id": eid, "биржа_рынок": sub, "инструмент": sym, "знак": e["знак"], "t_start_ms": a * MIN, "t_peak_ms": b * MIN,
               "длит_мин": dur, "контролей": len(got), "история_сут": round(hist, 2)}
        for v in VARS:
            nv = med([S.val(v, p) for p in pts]) if hist >= MIN_HIST_D else None
            for pos, off in (("pre", a - 1), ("in", b)):
                x = S.val(v, off)
                cs = [S.val(v, s + (dur if pos == "in" else -1)) for s in got]
                row["%s_%s" % (v, pos)] = x
                row["%s_%s_ctl" % (v, pos)] = med(cs)
                row["%s_%s_norm" % (v, pos)] = x / nv if x is not None and nv else None
        out.append(row)
    return out


def main():
    global CUBE, CUT, SPAN
    CUBE = sys.argv[sys.argv.index("--cube") + 1] if "--cube" in sys.argv else None
    CUT = int(sys.argv[sys.argv.index("--cut") + 1]) if "--cut" in sys.argv else 0
    procs = int(sys.argv[sys.argv.index("--procs") + 1]) if "--procs" in sys.argv else 15
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else 0
    SPAN = int(sys.argv[sys.argv.index("--span") + 1]) if "--span" in sys.argv else SPAN
    av = [a for i, a in enumerate(sys.argv[1:], 1) if not a.startswith("--") and sys.argv[i - 1] not in ("--procs", "--limit", "--cube", "--cut", "--span")]
    wf, D, X, outf = av[:4]
    roots = av[4:]
    by = defaultdict(list)
    for r in csv.DictReader(open(wf, encoding="utf-8")):
        if r["исключено"] == "" and r["представитель"] == "1" and int(r["D"]) == int(D) and float(r["X"]) == float(X):
            by[("%s-%s" % (r["биржа"], r["рынок"]), r["инструмент"])].append(r)
    jobs = [(s, i, v, roots) for (s, i), v in sorted(by.items())]
    if limit:
        jobs = jobs[:limit]
    rows = []
    with Pool(procs) as pool:
        for r in pool.imap_unordered(one, jobs, chunksize=1):
            rows += r
    rows.sort(key=lambda r: (r["биржа_рынок"], r["инструмент"], r["t_start_ms"]))
    cols = list(rows[0]) if rows else []
    with open(outf, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, cols, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if v is None else (round(v, 8) if isinstance(v, float) else v)) for k, v in r.items()})
    print(len(rows), "событий,", sum(r["контролей"] for r in rows), "контролей,", len(VARS), "вариантов")


if __name__ == "__main__":
    main()
