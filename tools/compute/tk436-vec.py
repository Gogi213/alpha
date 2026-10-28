#!/usr/bin/env python3
"""TK-436 (G-017): меры окна поверх куба tk436-cube.py — без разбора csv.gz и без python-циклов по минутам; гейт «было/стало».
  tk436-vec.py gate <end-start-th{T}.csv> <legs-th{T}.csv> <куб> <корень свечей> <aug-корень|-> [N окон]
Считает по окнам файла end-start: dv24, natr15/30/60/1d, cons_range, cons_ratio, m_bef/m_aft, v_bef/v_aft
  «стало» = куб (memmap) + срезы numpy; «было» = load_ohlcq + atr_pct + F.mad_demeaned из tk433-end.py (те же функции, что в боевом прогоне);
  сверка: число из «стало» == число из файла end-start (точно) и == «было»; печатает стену обоих и расхождения.
  tk436-vec.py oi <end-start-th{T}.csv> <trades-oi-th{T}.csv прежнего tk433-oi.py> <куб с oi-*.npy> [выход.csv]
K2_pre/K2_post (tk433-oi.py) из OI-куба 5 мин: срез по сетке вместо разбора csv.gz; сверка строк «%.6g» с прежним выходом."""
import csv, importlib.util, math, os, sys, time
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("e433", os.path.join(HERE, "tk433-end.py"))
E = importlib.util.module_from_spec(spec)
spec.loader.exec_module(E)
F = E.F
MIN = 60000
C, H, L, Q = 3, 1, 2, 4      # строки куба: o,h,l,c,q


class Sym:
    """один инструмент: куб (5,T) + первая минута; busy — интервалы ног ≥ 90 мин (исключаются из нормы)."""

    def __init__(self, arr, lo, busy):
        self.c, self.h, self.l, self.q = (np.asarray(arr[i]) for i in (C, H, L, Q))
        self.lo = lo
        self.ok = np.isfinite(self.c)
        self.lr = np.full(len(self.c), np.nan)          # логдоходность закрытия к предыдущей минуте (обе свечи есть)
        both = self.ok[1:] & self.ok[:-1]
        # math.log, не np.log: у numpy SIMD-лог отличается от libm на 1 ulp — гейт «точно» (один проход на инструмент, ~10 мс)
        self.lr[1:][both] = [math.log(x) for x in (self.c[1:][both] / self.c[:-1][both]).tolist()]
        self.qm = self.q.copy()
        for i0, i1 in busy:
            self.qm[max(0, i0 - lo):max(0, i1 - lo + 1)] = np.nan   # норма: без минут ног
        self.qm[~self.ok] = np.nan

    def mad(self, x, y):
        """как F.mad_demeaned(d, x, y): MAD×1,4826 по логдоходностям минут (x+1..y), за вычетом среднего."""
        r = self.lr[x + 1 - self.lo:y + 1 - self.lo]
        r = r[np.isfinite(r)].tolist()
        if len(r) < 5:
            return None
        mu = sum(r) / len(r)
        v = np.sort(np.array(r) - mu)
        md = v[len(v) // 2]
        return float(np.sort(np.abs(v - md))[len(v) // 2] * 1.4826)

    def qsum(self, x, y):
        q = self.q[x - self.lo:y + 1 - self.lo]
        return sum(q[np.isfinite(q)].tolist())

    def norm(self, a):
        q = self.qm[a - 7 * 1440 - self.lo:a - self.lo]
        q = q[np.isfinite(q)]
        if len(q) <= 1440:
            return None
        return float(np.partition(q, len(q) // 2)[len(q) // 2])

    def natr(self, a, tf, n=14):
        e = a - a % tf
        i0 = e - (n + 1) * tf - self.lo
        if i0 < 0:
            return None
        sl = slice(i0, e - self.lo)
        ok = self.ok[sl].reshape(n + 1, tf)
        if not ok.any(axis=1).all():
            return None
        cc, hh, ll = (x[sl].reshape(n + 1, tf) for x in (self.c, self.h, self.l))
        first = ok.argmax(axis=1)
        last = tf - 1 - ok[:, ::-1].argmax(axis=1)
        r = np.arange(n + 1)
        c0, c1 = cc[r, first], cc[r, last]
        hi = np.where(ok, hh, -np.inf).max(axis=1)
        lo_ = np.where(ok, ll, np.inf).min(axis=1)
        hi, lo_, c1 = hi.tolist(), lo_.tolist(), c1.tolist()      # питоновские float: sum() py3.12+ суммирует их с компенсацией (как в боевом atr_pct)
        tr = [max(hi[i] - lo_[i], abs(hi[i] - c1[i - 1]), abs(lo_[i] - c1[i - 1])) for i in range(1, n + 1)]
        return sum(tr) / len(tr) / c1[-1] * 100

    def start(self, a):
        o = {"dv24": self.qsum(a - 1440, a - 1)}
        for nm, tf in (("natr15", 15), ("natr30", 30), ("natr60", 60), ("natr1d", 1440)):
            o[nm] = self.natr(a, tf)
        cl = self.c[a - 60 - self.lo:a - self.lo]
        cl = cl[np.isfinite(cl)]
        v7 = self.mad(a - 7 * 1440, a - 1)
        rng = (float(cl.max()) - float(cl.min())) / float(cl[-1]) if len(cl) > 5 else None
        o["cons_range"] = rng
        o["cons_ratio"] = rng / v7 if rng is not None and v7 else None
        return o

    def around(self, e, norm):
        o = {}
        for nm, (x, y) in (("bef", (e - 60, e)), ("aft", (e + 1, e + 60))):
            o["m_" + nm] = self.qsum(x, y) / (norm * (y - x + 1)) if norm else None
            o["v_" + nm] = self.mad(x, y)
        return o


def old_measures(d, a, e, norm):
    o = {"dv24": sum(d[m][1] for m in range(a - 1440, a) if m in d)}
    for nm, tf in (("natr15", 15), ("natr30", 30), ("natr60", 60), ("natr1d", 1440)):
        o[nm] = E.atr_pct(d, a, tf)
    cl = [d[m][0] for m in range(a - 60, a) if m in d]
    v7 = F.mad_demeaned(d, a - 7 * 1440, a - 1)
    rng = (max(cl) - min(cl)) / cl[-1] if len(cl) > 5 else None
    o["cons_range"] = rng
    o["cons_ratio"] = rng / v7 if rng is not None and v7 else None
    for nm, (x, y) in (("bef", (e - 60, e)), ("aft", (e + 1, e + 60))):
        s = sum(d[m][1] for m in range(x, y + 1) if m in d)
        o["m_" + nm] = s / (norm * (y - x + 1)) if norm else None
        o["v_" + nm] = F.mad_demeaned(d, x, y)
    return o


def cmd_gate(av):
    es, legs, cube, root, aug = av[:5]
    nmax = int(av[5]) if len(av) > 5 else 10 ** 9
    rows = list(csv.DictReader(open(es, encoding="utf-8")))[:nmax]
    busy = {}
    for r in csv.DictReader(open(legs, encoding="utf-8")):
        busy.setdefault((r["биржа"], r["рынок"], r["инструмент"]), []).append((int(r["t_start_ms"]) // MIN, int(r["t_peak_ms"]) // MIN))
    by = {}
    for r in rows:
        by.setdefault((r["биржа"], r["рынок"], r["инструмент"]), []).append(r)
    roots = (aug if aug != "-" else None, root)
    keys = ["dv24", "natr15", "natr30", "natr60", "natr1d", "cons_range", "cons_ratio", "m_bef", "m_aft", "v_bef", "v_aft"]
    t_old = t_new = t_load_new = 0.0
    n = ex_old = ex_new = diff_new = 0
    worst = 0.0
    perkey = {}
    cubes = {}
    for (ex, mk, sym), L in sorted(by.items()):
        sub = "%s-%s" % (ex, mk)
        if sub not in cubes:
            syms = open("%s/%s.syms" % (cube, sub)).read().split()
            t0 = time.time()
            cubes[sub] = (np.load("%s/%s.npy" % (cube, sub), mmap_mode="r"), {s: i for i, s in enumerate(syms)}, int(open("%s/%s.lo" % (cube, sub)).read()))
            t_load_new += time.time() - t0
        arr, ix, lo = cubes[sub]
        if sym not in ix:
            continue
        bz = busy.get((ex, mk, sym), [])
        L = [r for r in L if int(r["start_min"]) - 15 * 1440 - 1440 >= lo]     # окно с историей внутри куба (куб без запаса — не берём первые 16 суток)
        if not L:
            continue
        t0 = time.time()
        d = E.load_ohlcq(roots, sub, sym)
        old = []
        for r in L:
            a, e = int(r["start_min"]), int(r["peak_min"])
            q = [d[m][1] for m in range(a - 7 * 1440, a) if m in d and not any(i0 <= m <= i1 for i0, i1 in bz)]
            norm = F.med(q) if len(q) > 1440 else None
            old.append(old_measures(d, a, e, norm))
        t_old += time.time() - t0
        t0 = time.time()
        s = Sym(np.asarray(arr[ix[sym]]), lo, bz)
        new = []
        for r in L:
            a, e = int(r["start_min"]), int(r["peak_min"])
            o = s.start(a)
            o.update(s.around(e, s.norm(a)))
            new.append(o)
        t_new += time.time() - t0
        for r, o, w in zip(L, old, new):
            n += 1
            for k in keys:
                f = r.get(k, "")
                f = float(f) if f != "" else None
                for src, nm in ((o, "old"), (w, "new")):
                    v = src[k]
                    if (v is None) != (f is None) or (v is not None and v != f):
                        if v is not None and f is not None:
                            worst = max(worst, abs(v - f) / max(abs(f), 1e-300))
                        if nm == "old":
                            ex_old += 1
                        else:
                            ex_new += 1
                            perkey[k] = perkey.get(k, 0) + 1
    print("окон %d инструментов %d мер %d" % (n, len(by), len(keys)))
    print("было (разбор csv.gz + циклы): %.2f с; стало (куб, срезы): %.2f с + открытие куба %.2f с; ускорение ×%.1f" % (t_old, t_new, t_load_new, t_old / max(t_new, 1e-9)))
    print("расхождений с файлом end-start: было %d, стало %d из %d; макс. относит. %.2e; по мерам %s" % (ex_old, ex_new, n * len(keys), worst, perkey))


def cmd_oi(av):
    src, old_fn, cube = av[:3]
    out = av[3] if len(av) > 3 else None
    D = list(csv.DictReader(open(src, encoding="utf-8")))
    old = {r["id"]: (r["K2_pre"], r["K2_post"]) for r in csv.DictReader(open(old_fn, encoding="utf-8"))}
    t0 = time.time()
    cb = {}
    for sub in ("bybit-linear", "gate-futures_usdt"):
        syms = open("%s/oi-%s.syms" % (cube, sub)).read().split()
        chs = open("%s/oi-%s.ch" % (cube, sub)).read().split()
        lo = int(open("%s/oi-%s.lo" % (cube, sub)).read())
        mm = np.load("%s/oi-%s.npy" % (cube, sub), mmap_mode="r")
        cb[sub] = ({s: i for i, s in enumerate(syms)}, lo, mm, chs.index("open_interest/open_interest") if "open_interest/open_interest" in chs else chs.index("oi/open_interest"))
    t_open = time.time() - t0

    def at(a, lo, t_min):
        """последняя запись OI > 0 не старше 15 мин на сетке 5 мин (как at() в tk433-oi.py)."""
        k = (t_min - lo) // 5
        for j in range(k, k - 4, -1):
            if 0 <= j < len(a):
                v = a[j]
                if v > 0:                                  # NaN > 0 == False
                    return v if t_min - (lo + 5 * j) <= 15 else float("nan")
        return float("nan")

    t0 = time.time()
    new, seen = {}, set()
    for r in D:
        pre = post = float("nan")
        sub = "%s-%s" % (r["биржа"], r["рынок"])
        if sub in cb and r["инструмент"] in cb[sub][0]:
            ix, lo, mm, ch = cb[sub]
            a = np.asarray(mm[ix[r["инструмент"]], ch])
            p = int(r["peak_min"])
            x, y, z = at(a, lo, p - 60), at(a, lo, p), at(a, lo, p + 60)
            pre, post = (y - x) / x, (z - y) / y
        if r["id"] not in seen:
            seen.add(r["id"])
            new[r["id"]] = tuple(("%.6g" % v) if v == v else "" for v in (pre, post))
    t_new = time.time() - t0
    bad = [(i, old.get(i), new[i]) for i in new if old.get(i) != new[i]]
    have = sum(1 for v in new.values() if v[1])
    print("id %d, с K2 (стало) %d, (было) %d; расхождений %d; открытие куба %.2f с, срезы %.2f с" % (len(new), have, sum(1 for v in old.values() if v[1]), len(bad), t_open, t_new))
    for b in bad[:10]:
        print("  ", b)
    if out:
        with open(out, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["id", "K2_pre", "K2_post"])
            for i, v in new.items():
                w.writerow([i, v[0], v[1]])
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    {"gate": cmd_gate, "oi": cmd_oi}[sys.argv[1]](sys.argv[2:])
