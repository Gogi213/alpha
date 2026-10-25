#!/usr/bin/env python3
"""TK-402 П-28 v3.1: обёртка ctypes над tk402-step2-core.c (лента событий, опоры, клетки) + самопроверка `--selftest` (синтетика: отбор = полный байт в байт, ручной расчёт сделки).
Загрузчики gev2 / TK-399 / фандинга и агрегация по часам — отдельным шагом (tk402-step2.py)."""
import ctypes, os, sys, subprocess
import numpy as np
D0 = os.path.dirname(os.path.abspath(__file__))
def load(so=None):
    so = so or os.path.join(D0, 'tk402-step2-core' + ('.dll' if os.name == 'nt' else '.so'))
    if not os.path.exists(so): subprocess.check_call(['gcc', '-O2', '-shared', '-fPIC', '-o', so, os.path.join(D0, 'tk402-step2-core.c'), '-lm'])
    L = ctypes.CDLL(so); return L
P = lambda a: a.ctypes.data_as(ctypes.c_void_p)
def ranks(d, ok):
    """ранги ok-значений d (стабильно); -> rank int32[M], dsorted"""
    idx = np.flatnonzero(ok); o = idx[np.argsort(d[idx], kind='stable')]
    rk = np.zeros(len(d), np.int32); rk[o] = np.arange(len(o), dtype=np.int32)
    return rk, d[o].copy()
def anchor(L, ts, d, ok, rk, ds, kind, par):
    out = np.empty(len(ts), np.float64)
    L.anchor(ctypes.c_int64(len(ts)), P(ts), P(d), P(ok), P(rk), P(ds), ctypes.c_int(max(len(ds), 1)), ctypes.c_int(kind), ctypes.c_double(par), P(out)); return out
def cells(L, tape, b, mode, thr, Dl, To, NH, t0, tend, fg_=None, fb_=None, minhold=1000):
    ts, gb, ga, bb, ba, ok, d, *w = tape; gbw, gaw = (w[0], w[1]) if w else (gb, ga); nc = len(thr)
    thr = np.ascontiguousarray(thr, np.float64); Dl = np.ascontiguousarray(Dl, np.int32); To = np.ascontiguousarray(To, np.int32)
    e = (np.zeros(0, np.int64), np.zeros(0))
    pg, cg = fg_ if fg_ is not None else e; pb, cb = fb_ if fb_ is not None else e
    pg = np.ascontiguousarray(pg, np.int64); cg = np.ascontiguousarray(cg, np.float64); pb = np.ascontiguousarray(pb, np.int64); cb = np.ascontiguousarray(cb, np.float64)
    sg = np.zeros((nc, NH)); fg = np.zeros((nc, NH)); cnt = np.zeros((nc, NH), np.int32); ep = np.zeros_like(cnt); c1 = np.zeros_like(cnt); c2 = np.zeros_like(cnt); dk = np.zeros((nc, 4), np.int64)
    L.cells(ctypes.c_int64(len(ts)), P(ts), P(gb), P(ga), P(bb), P(ba), P(ok), P(d), P(b), P(gbw), P(gaw), ctypes.c_int64(tend), ctypes.c_int64(t0), ctypes.c_int(NH), ctypes.c_int(mode), ctypes.c_int(nc),
            P(thr), P(Dl), P(To), ctypes.c_int(minhold), P(pg), P(cg), ctypes.c_int64(len(pg)), P(pb), P(cb), ctypes.c_int64(len(pb)), P(sg), P(fg), P(cnt), P(ep), P(c1), P(c2), P(dk))
    return sg, fg, cnt, ep, c1, c2, dk
def selftest():
    L = load(); rng = np.random.default_rng(1); M = 400000
    ts = np.cumsum(rng.integers(1, 400, M)).astype(np.int64) + 1_760_000_000_000
    lg = np.cumsum(rng.normal(0, 1e-5, M)); gap = np.cumsum(rng.normal(0, 3e-5, M)) * 0.2 + np.sin(np.arange(M) / 5000) * 4e-4
    mb = 100 * np.exp(lg); mg = mb * np.exp(gap); sp = 5e-5
    bb = mb * (1 - sp); ba = mb * (1 + sp); gb = mg * (1 - sp); ga = mg * (1 + sp)
    ok = (rng.random(M) > 0.001).astype(np.uint8); d = np.where(ok > 0, (np.log((gb + ga) / 2) - np.log((bb + ba) / 2)) * 1e4, np.nan)
    rk, ds = ranks(d, ok > 0); tape = (ts, gb, ga, bb, ba, ok, d)
    t0 = int(ts[0]); tend = int(ts[-1]); NH = int((tend - t0) // 3600000) + 1
    fund = ((np.array([t0 + 3_600_000, t0 + 11_000_000, t0 + 30_000_000], np.int64)), np.cumsum([1e-4, -2e-4, 1.5e-4]))
    res = {}
    for kind, par in ((0, 300000.), (0, 3600000.), (1, 3600000.), (1, 300000.), (2, 0.)):
        a = anchor(L, ts, d, ok, rk, ds, kind, par); res[(kind, par)] = a
        print('anchor', kind, par, 'valid share %.3f' % np.isfinite(a).mean(), flush=True)
        a = np.where(np.isfinite(a), a, np.nan)
        thr = np.repeat([8.0, 12.0], 4); Dl = np.tile([0, 100, 250, 1000], 2); To = np.full(8, 5000)
        r0 = cells(L, tape, a, 0, thr, Dl, To, NH, t0, tend, fund, fund)
        r1 = cells(L, tape, a, 1, thr, Dl, To, NH, t0, tend, fund, fund)
        same = all(np.array_equal(x, y) for x, y in zip(r0, r1))
        print('  trades', int(r0[2].sum()), 'отбор==полный байт в байт:', same, 'drops', r0[6].sum(0).tolist(), flush=True)
        assert same
    # ручной расчёт: одна сделка на искусственной ленте
    ts = np.array([0, 1000, 1500, 2100, 3000, 5000], np.int64); gb = np.array([100, 100, 99, 99, 100.5, 100.], float) * 0 + np.array([99.9, 99.9, 98.9, 98.9, 100.4, 99.9])
    ga = gb + 0.2; bb = np.array([100.0] * 6); ba = bb + 0.2; ok = np.ones(6, np.uint8)
    d = (np.log((gb + ga) / 2) - np.log((bb + ba) / 2)) * 1e4; b = np.zeros(6)
    tape = (ts, gb, ga, bb, ba, ok, d)
    sg, fg, cnt, ep, c1, c2, dk = cells(L, tape, b, 0, [10.0], [100], [4000], 1, 0, 5000)
    # решение: первый индекс i с ed>10 бп: i=2 (t=1500, Gate ниже): сторона 1 (лонг Gate/шорт Bybit); te=1600 -> состояние idx 2: покупка Gate по ga=99.1, продажа Bybit по bb=100
    # выход: первое пересечение dev>=0 при t>=te+1000=2600 -> idx 4 (t=3000, d>0); x=3100 -> состояние idx 4: Gate bid 100.4, Bybit ask 100.2
    exp = ((100.4 / 99.1 - 1) + (1 - 100.2 / 100.0)) * 1e4
    print('ручной расчёт: gross %.6f  ядро %.6f  сделок %d' % (exp, sg[0, 0], cnt[0, 0])); assert abs(exp - sg[0, 0]) < 1e-9 and cnt[0, 0] == 1
    print('selftest OK')
if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--selftest': selftest()
