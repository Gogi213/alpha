#!/usr/bin/env python3
"""TK-439: меры волатильности TK-435 (tk435-vol.py) с пакетным расчётом вместо скалярных Series.val: все точки нормы, события и контроли
одного варианта считаются массивом за вызов (префиксные суммы — индексами, RNG/MAD — матрицей окон). Формулы и порядок операций те же, что в
Series.val (без перестановок сумм) — вывод побайтно тот же. Аргументы и выход — как у tk435-vol.py (импортируется как модуль, не правится).
  tk439-vol.py <windows.csv> <D> <X> <выход.csv> [корни] [--cube …] [--cut …] [--procs N] [--limit N] [--span N]"""
import hashlib
import importlib.util
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sp = importlib.util.spec_from_file_location("tk435v", os.path.join(HERE, "tk435-vol.py"))
M = importlib.util.module_from_spec(sp)
sys.modules["tk435v"] = M
sp.loader.exec_module(M)


def _pref(S, a, b, p, pos):
    """среднее по префиксу S на индексах [a, b) длиной p: (массив, маска годности); как Series.mean."""
    ok = (a >= 0) & (b <= len(S[0]) - 1) & (b > a)
    ac, bc = np.where(ok, a, 0), np.where(ok, b, 0)
    k = S[1][bc] - S[1][ac]
    ok &= k >= max(1, p // 2)
    with np.errstate(all="ignore"):
        m = (S[0][bc] - S[0][ac]) / np.where(ok, k, 1)
    return m, ok


def vals(self, var, es):
    """значения варианта var для абс. минут es (массив int64) — список float | None, как [self.val(var, e) for e in es]."""
    es = np.asarray(es, dtype=np.int64)
    nm, _, p = var.partition("_")
    p = int(p)
    j = es - self.g0 + 1
    j5 = j // 5
    if nm in ("NATR1", "NATR5", "RV1", "RV5", "PK", "GK"):
        if nm == "NATR1":
            m, ok = _pref(self.tr1, j - p, j, p, 0)
        elif nm == "NATR5":
            m, ok = _pref(self.tr5, j5 - p, j5, p, 0)
        elif nm == "RV1":
            m, ok = _pref(self.r1, j - p, j, p, 0)
            m = np.sqrt(m * p)
        elif nm == "RV5":
            m, ok = _pref(self.r5, j5 - p // 5, j5, p // 5, 0)
            m = np.sqrt(m * (p // 5))
        else:
            m, ok = _pref(self.pk if nm == "PK" else self.gk, j5 - p // 5, j5, p // 5, 0)
            ok &= m > 0
            m = np.sqrt(np.where(ok, m, 1.0))
        # NATR*: np.float64 как в Series.mean (round() у numpy и у float различается в последнем знаке на больших числах); остальные — float
        return [v if o else None for v, o in zip(list(m) if nm.startswith("NATR") else m.tolist(), ok.tolist())]
    ok = (j - p >= 0) & (j <= self.n)
    jc = np.where(ok, j, p)
    idx = jc[:, None] + np.arange(-p, 0)
    C = self.c[idx]
    nanc = np.isnan(C)
    ok &= nanc.sum(axis=1) <= p // 2
    out = [None] * len(es)
    if nm == "RNG":
        with np.errstate(all="ignore"):
            hi, lo = np.nanmax(self.h[idx], axis=1), np.nanmin(self.l[idx], axis=1)
        last = C[np.arange(len(es)), p - 1 - np.argmax(~nanc[:, ::-1], axis=1)]
        with np.errstate(all="ignore"):
            v = (hi - lo) / last
        for i in np.flatnonzero(ok).tolist():
            out[i] = float(v[i])
        return out
    R = self.r1v[idx]
    nanr = np.isnan(R)
    cnt = p - nanr.sum(axis=1)
    ok &= cnt >= 5
    for m in np.unique(cnt[ok]).tolist():
        rows = np.flatnonzero(ok & (cnt == m))
        V = R[rows][~nanr[rows]].reshape(len(rows), m)
        X = np.sort(V - V.mean(axis=1)[:, None], axis=1)
        D = np.sort(np.abs(X - X[:, m // 2][:, None]), axis=1)
        md = D[:, m // 2] if m % 2 else (D[:, m // 2 - 1] + D[:, m // 2]) / 2
        res = (md * 1.4826).tolist()
        for i, v in zip(rows.tolist(), res):
            out[i] = v
    return out


M.Series.vals = vals


def one(arg):
    """как tk435-vol.one: тот же отбор контролей и точек нормы; значения вариантов — пакетно (Series.vals)."""
    sub, sym, evs, roots = arg
    ld = M.load(roots, sub, sym)
    if ld is None:
        return []
    S = M.Series(*ld)
    g0 = S.g0
    avail = ~np.isnan(S.c)
    first = int(np.argmax(avail)) + g0
    busy = sorted((int(e["t_start_ms"]) // M.MIN, int(e["t_peak_ms"]) // M.MIN) for e in evs)
    PRE, SEED, PER, SPAN, NORM_D, MIN_HIST_D, MIN = M.PRE, M.SEED, M.PER, M.SPAN, M.NORM_D, M.MIN_HIST_D, M.MIN
    ca = np.concatenate([[0], np.cumsum(avail)])
    B0, B1 = np.array([x[0] for x in busy]) - PRE, np.array([x[1] for x in busy])
    DD, KS = np.array([d for d in range(-SPAN, SPAN + 1) if d != 0]), np.arange(0, 60, 5)
    out = []
    for e in evs:
        a, b = int(e["t_start_ms"]) // MIN, int(e["t_peak_ms"]) // MIN
        dur = b - a
        eid = "%s|%s|%d" % (sub, sym, a)
        frac = int(avail[a - g0:b - g0 + 1].sum()) / (dur + 1)
        hs = a - a % 60
        # кандидаты контролей: те же условия, что в tk435-vol.one, но по всем 720 сдвигам сразу (busy и доступность — матрицей/префиксом)
        s = (hs + DD[:, None] * 1440 + KS[None, :]).ravel()
        ok = (s - PRE >= g0) & (s + dur - g0 < S.n)
        sc = np.where(ok, s, g0 + PRE)
        ok &= ~((B0[None, :] <= sc[:, None] + dur) & (sc[:, None] - PRE <= B1[None, :])).any(axis=1)
        i0, i1 = np.clip(sc - g0, 0, S.n - 1), np.clip(sc + dur - g0, 0, S.n - 1)
        ok &= avail[i0] & avail[i1] & ((ca[i1 + 1] - ca[i0]) >= frac * (dur + 1))
        cand = [(hashlib.md5(("%d|%s|%d" % (SEED, eid, x)).encode()).hexdigest(), x) for x in s[ok].tolist()]
        cand.sort()
        got = []
        for _, s in cand:
            if all(s + dur < g - PRE or s - PRE > g + dur for g in got):
                got.append(s)
                if len(got) == PER:
                    break
        hist = (a - first) / 1440
        P = np.arange(a - NORM_D * 1440, a, 60)
        pts = P[(P - g0 >= 0) & ~((B0[None, :] <= P[:, None]) & (P[:, None] <= B1[None, :] + 90)).any(axis=1)].tolist() if hist >= MIN_HIST_D else []
        ng, npt = len(got), len(pts)
        es = pts + [a - 1, b] + [s - 1 for s in got] + [s + dur for s in got]
        row = {"id": eid, "биржа_рынок": sub, "инструмент": sym, "знак": e["знак"], "t_start_ms": a * MIN, "t_peak_ms": b * MIN,
               "длит_мин": dur, "контролей": ng, "история_сут": round(hist, 2)}
        for v in M.VARS:
            r = S.vals(v, es)
            nv = M.med(r[:npt]) if hist >= MIN_HIST_D else None
            for pos, x, cs in (("pre", r[npt], r[npt + 2:npt + 2 + ng]), ("in", r[npt + 1], r[npt + 2 + ng:])):
                row["%s_%s" % (v, pos)] = x
                row["%s_%s_ctl" % (v, pos)] = M.med(cs)
                row["%s_%s_norm" % (v, pos)] = x / nv if x is not None and nv else None
        out.append(row)
    return out


M.one = one

if __name__ == "__main__":
    M.main()
