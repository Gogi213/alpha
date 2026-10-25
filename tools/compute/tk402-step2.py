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
import datetime as dt
def _ms(t): return int(t.replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
_VER = None
def bad_intervals(coin, sym, days):
    """запретные интервалы [t0,t1) мс (A7/разрывы): Gate — час без файла или вырожденный (.n<100), Bybit — сутки с verify gaps>0 или invariants>0 (verify.tsv)"""
    global _VER
    if _VER is None:
        _VER = {}
        for r in csv.DictReader(open('/data/tk0402/verify.tsv', encoding='utf-8'), delimiter='	'):
            _VER[(r['sym'], r['day'])] = r['admit'] == '1'
    out = []; why = {'gate_missing': 0, 'gate_degenerate': 0, 'bybit_day': 0}
    for x in days:
        d0 = dt.datetime.strptime(x, '%Y-%m-%d')
        if not _VER.get((sym, x), False): out.append((_ms(d0), _ms(d0) + 86400000)); why['bybit_day'] += 1
        for h in range(24):
            t = d0 + dt.timedelta(hours=h); f = f"{GEV}/{coin}/{t.strftime('%Y%m%d%H')}"
            if not os.path.exists(f + '.npy'): k = 'gate_missing'
            else:
                try: n = int(open(f + '.n').read())
                except OSError: n = -1
                k = 'gate_degenerate' if n < 100 else None
            if k: out.append((_ms(t), _ms(t) + 3600000)); why[k] += 1
    return out, why
def tape(g, b, bad=()):
    """объединённая лента: ts (union), as-of Gate и Bybit котировки, ok, d (бп, log-разница середин)"""
    ts = np.union1d(g[0], b[0])
    def asof(src):
        i = np.searchsorted(src[0], ts, side='right') - 1; ii = np.clip(i, 0, None)
        return np.where(i >= 0, src[1][ii], np.nan), np.where(i >= 0, src[2][ii], np.nan)
    gb, ga = asof(g); bb, ba = asof(b)
    ok = (np.isfinite(gb) & np.isfinite(ga) & np.isfinite(bb) & np.isfinite(ba) & (gb < ga) & (bb < ba)).astype(np.uint8)
    for t0, t1 in bad: ok[(ts >= t0) & (ts < t1)] = 0
    with np.errstate(all='ignore'):
        d = np.where(ok > 0, (np.log((gb + ga) / 2) - np.log((bb + ba) / 2)) * 1e4, np.nan)
    return ts, gb, ga, bb, ba, ok, d
def probe(coin, day):
    L = LIB.load(); sym = coin + 'USDT'
    days = day.split(',')  # опора kind 2 (прошлые сутки) требует ленты минимум из двух подряд суток
    gs = [gate_day(coin, x) for x in days]; bs = [bybit_day(sym, x) for x in days]
    assert all(x is not None for x in gs + bs), ('нет данных', coin, day)
    g = tuple(np.concatenate([x[i] for x in gs]) for i in range(3)); b = tuple(np.concatenate([x[i] for x in bs]) for i in range(3))
    bad, why = bad_intervals(coin, sym, days); print('запретные интервалы', len(bad), why, flush=True)
    tp = tape(g, b, bad); ts, gb, ga, bb, ba, ok, d = tp
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

MIN = 60000.; HR = 3600000.
# опоры П-28 §4: A (0) + B: медианы 5м/30м/1ч/4ч/24ч, EMA 5м/1ч/24ч, медиана прошлых суток; «накопленный фандинг к выплате» — отдельным шагом (нужны времена выплат, в ядре нет)
ANCHORS = [('A0', 3, 0.), ('med5m', 0, 5 * MIN), ('med30m', 0, 30 * MIN), ('med1h', 0, HR), ('med4h', 0, 4 * HR), ('med24h', 0, 24 * HR),
           ('ema5m', 1, 5 * MIN), ('ema1h', 1, HR), ('ema24h', 1, 24 * HR), ('prevday', 2, 0.)]
CIRCLE = {'full': 2 * (5.0 + 5.5), 'disc35': 2 * (5.0 * 0.65 + 5.5)}  # FF, taker 4 ноги; Gate VIP0 5 бп (со скидкой 35 %: x0,65), Bybit 5,5
def grid():
    """клетки на опору: порог (k x круг, 2 тарифа) x задержка (0 справочно, 100/250/500/1000) x тайм-аут §4; порядок: порог снаружи (список кандидатов ядра переиспользуется)"""
    TO = [1000, 5000, 30000, 60000, 90000, 120000, 3600000, 14400000]; DL = [0, 100, 250, 500, 1000]
    G = [(fee, k, D, T) for fee in CIRCLE for k in (1.0, 1.5, 2.0) for D in DL for T in TO]
    return (np.array([k * CIRCLE[fee] for fee, k, D, T in G]), np.array([D for _, _, D, _ in G]), np.array([T for *_, T in G]), G)
def _avail(coin, sym, day):
    d = day.replace('-', '')
    return bool(glob.glob(f'{GEV}/{coin}/{d}??.npy')) and os.path.exists(f'{BEV}/fut/{day}/{sym}-{day}.csv.gz')
def run(coin, days, out):
    """боевой цикл одной монеты: сутки без любой из двух лент рвут ряд на отрезки (as-of не переносим через дыру, опоры не сшиваем); на отрезок — клетки зонда (15) x 3 опоры, режим 0 (отбор = полный проверен в probe);
    результат — npz с почасовыми массивами sg, fg, cnt, ep, c1, c2, dk по отрезкам для max-T"""
    L = LIB.load(); sym = coin + 'USDT'; res = {}; segs = []; cur = []
    for x in days:
        if _avail(coin, sym, x): cur.append(x)
        else:
            if cur: segs.append(cur)
            cur = []
    if cur: segs.append(cur)
    try:
        fd = json.load(open('/data/tk0402/funding459.json'))[coin]
        fg = (np.array([a[0] * 1000 for a in fd['g']], np.int64), np.cumsum([a[1] * 1e4 for a in fd['g']]))
        fb = (np.array([a[0] * 1000 for a in fd['b']], np.int64), np.cumsum([a[1] * 1e4 for a in fd['b']])) if fd['b'] else None
    except Exception as e:
        raise SystemExit('фандинг пуст/нет: %s %r (B11: пусто = ошибка)' % (coin, e))
    thr, Dl, To, GRID = grid()
    for si, sd in enumerate(segs):
        gs = [gate_day(coin, x) for x in sd]; bs = [bybit_day(sym, x) for x in sd]
        g = tuple(np.concatenate([x[i] for x in gs]) for i in range(3)); b = tuple(np.concatenate([x[i] for x in bs]) for i in range(3))
        bad, why = bad_intervals(coin, sym, sd); tp = tape(g, b, bad); ts, gb, ga, bb, ba, ok, d = tp
        t0 = int(ts[0]); tend = int(ts[-1]); NH = int((tend - t0) // 3600000) + 1
        rk, ds = LIB.ranks(d, ok > 0); res[f's{si}_t0'] = np.array([t0, tend]); res[f's{si}_ok'] = np.array([ok.mean(), len(ts)])
        for name, kind, par in ANCHORS:
            a = np.where(ok > 0, 0.0, np.nan) if kind == 3 else LIB.anchor(L, ts, d, ok, rk, ds, kind, par)  # kind 3 = режим A (опора 0)
            for n, v in zip(('sg', 'fg', 'cnt', 'ep', 'c1', 'c2', 'dk'), LIB.cells(L, tp, a, 0, thr, Dl, To, NH, t0, tend, fg, fb)): res[f's{si}_{name}_{n}'] = v
    np.savez_compressed(out, segs=np.array([','.join(s) for s in segs]), grid=np.array([f'{f},{k},{D},{T}' for f, k, D, T in GRID]), **res)
    print(coin, 'segs', [(s[0], s[-1]) for s in segs], '->', out, flush=True)
if __name__ == '__main__':
    if sys.argv[1] == 'probe': probe(sys.argv[2], sys.argv[3])
    elif sys.argv[1] == 'run': run(sys.argv[2], sys.argv[3].split(','), sys.argv[4])
