#!/usr/bin/env python3
"""TK-402 П-28 v3.1: загрузчик ленты (gev2 Gate + TK-399 Bybit) для C-ядра tk402-step2lib.py. Пока — проба `probe COIN DAY` (1 монета x 1 сутки):
лента = объединение событий обеих книг по мс, as-of котировки (последняя строка с t<=T), ok = обе стороны обеих книг есть (разрывы/verify — следующий шаг);
считает клетки режимом 0 (отбор) и 1 (полный перебор), печатает сверку байт в байт и счётчики. Запуск (calc): /data/tk0380/npvenv/bin/python tk402-step2.py probe SOL 2026-10-16"""
import sys, os, glob, csv, json
import numpy as np
D0 = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, D0)
import importlib.util
sp = importlib.util.spec_from_file_location('s2lib', os.path.join(D0, 'tk402-step2lib.py')); LIB = importlib.util.module_from_spec(sp); sp.loader.exec_module(LIB)
GEV = '/data/tk0402/gev2'; BEV = '/data/tk0399/bbo'
TK = {}
for r in csv.DictReader(open('/data/tk0394/bybit-ticks.csv', encoding='utf-8-sig'), delimiter=';'):
    TK[('spot' if r['market'].startswith('сп') else 'fut', r['symbol'])] = float(r['tick'])
def mult(sym):
    for p, m in (('1000000', 1e6), ('10000', 1e4), ('1000', 1e3)):
        if sym.startswith(p): return m
    return 1.0
def gate_day(coin, day):
    d = day.replace('-', ''); fs = sorted(glob.glob(f'{GEV}/{coin}/{d}??.npy'))
    a = [np.load(f) for f in fs]; a = [x for x in a if len(x)]
    if not a: return None
    a = np.concatenate(a); a = a[np.argsort(a[:, 0], kind='stable')]
    return a[:, 0].astype(np.int64), a[:, 1], a[:, 2]
def bybit_day(sym, day, mk='fut'):
    f = f'{BEV}/{mk}/{day}/{sym}-{day}.csv.gz'
    if not os.path.exists(f): return None
    a = np.loadtxt(f, delimiter=',', skiprows=1, usecols=(0, 1, 2), dtype=np.int64, ndmin=2)
    if len(a) == 0: return None
    tick = TK[(mk, sym)]; m = mult(sym)
    return a[:, 0], np.where(a[:, 1] > 0, a[:, 1] * tick / m, np.nan), np.where(a[:, 2] > 0, a[:, 2] * tick / m, np.nan)
def tape(g, b):
    """объединённая лента: ts (union), as-of Gate и Bybit котировки, ok, d (бп, log-разница середин)"""
    ts = np.union1d(g[0], b[0])
    def asof(src):
        i = np.searchsorted(src[0], ts, side='right') - 1; ii = np.clip(i, 0, None)
        return np.where(i >= 0, src[1][ii], np.nan), np.where(i >= 0, src[2][ii], np.nan)
    gb, ga = asof(g); bb, ba = asof(b)
    ok = (np.isfinite(gb) & np.isfinite(ga) & np.isfinite(bb) & np.isfinite(ba) & (gb < ga) & (bb < ba)).astype(np.uint8)
    with np.errstate(all='ignore'):
        d = np.where(ok > 0, (np.log((gb + ga) / 2) - np.log((bb + ba) / 2)) * 1e4, np.nan)
    return ts, gb, ga, bb, ba, ok, d
def probe(coin, day):
    L = LIB.load(); sym = coin + 'USDT'
    days = day.split(',')  # опора kind 2 (прошлые сутки) требует ленты минимум из двух подряд суток
    gs = [gate_day(coin, x) for x in days]; bs = [bybit_day(sym, x) for x in days]
    assert all(x is not None for x in gs + bs), ('нет данных', coin, day)
    g = tuple(np.concatenate([x[i] for x in gs]) for i in range(3)); b = tuple(np.concatenate([x[i] for x in bs]) for i in range(3))
    tp = tape(*(g, b)); ts, gb, ga, bb, ba, ok, d = tp
    t0 = int(ts[0]); tend = int(ts[-1]); NH = int((tend - t0) // 3600000) + 1
    print(coin, day, 'gate rows', len(g[0]), 'bybit rows', len(b[0]), 'tape', len(ts), 'ok share %.4f' % ok.mean(), 'median |d| %.2f бп' % np.nanmedian(np.abs(d)), flush=True)
    rk, ds = LIB.ranks(d, ok > 0)
    fund = None
    try:
        fd = json.load(open('/data/tk0402/funding459.json'))[coin]
        fg = (np.array([a[0] * 1000 for a in fd['g']], np.int64), np.cumsum([a[1] * 1e4 for a in fd['g']]))
        fb = (np.array([a[0] * 1000 for a in fd['b']], np.int64), np.cumsum([a[1] * 1e4 for a in fd['b']])) if fd['b'] else None
    except Exception as e:
        fg = fb = None; print('фандинг: нет в funding459 (' + repr(e) + ') — нули, только для пробы')
    GFEE, BFEE = 5.0, 5.5; circle = 2 * (GFEE + BFEE)
    thr = np.array([k * circle for k in (1.0, 1.5, 2.0)] * 5); Dl = np.repeat([0, 100, 250, 500, 1000], 3); To = np.full(15, 30000)
    for kind, par in ((0, 3600000.), (1, 3600000.), (2, 0.)):
        a = LIB.anchor(L, ts, d, ok, rk, ds, kind, par); print('anchor', kind, par, 'valid share %.3f' % np.isfinite(a).mean(), flush=True)
        r0 = LIB.cells(L, tp, a, 0, thr, Dl, To, NH, t0, tend, fg, fb); r1 = LIB.cells(L, tp, a, 1, thr, Dl, To, NH, t0, tend, fg, fb)
        same = all(np.array_equal(x, y) for x, y in zip(r0, r1))
        print('  сделок', int(r0[2].sum()), 'отбор==полный байт в байт:', same, 'drops', r0[6].sum(0).tolist(), 'gross/сделка (бп) %.2f' % (r0[0].sum() / max(r0[2].sum(), 1)), flush=True)
        if not same: sys.exit(3)
if __name__ == '__main__':
    if sys.argv[1] == 'probe': probe(sys.argv[2], sys.argv[3])
