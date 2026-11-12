#!/usr/bin/env python3
"""TK-394 возврат 3 (Судья): скан сдвига времени книги Gate фьюча относительно ленты сделок, -2..+2 с шаг 0.1 с.
Запуск (calc, 1 ядро): tk394-shift.py OUT.json COIN:DAY ...
strict = доля сделок в [бид;аск] по BBO состояния ДО (момент сделки + сдвиг), 100-мс корзины."""
import sys, os, json, gzip, glob, importlib.util
import numpy as np
D = os.path.dirname(os.path.abspath(__file__))
sp = importlib.util.spec_from_file_location('cand', os.path.join(D, 'tk394-cand.py')); C = importlib.util.module_from_spec(sp); sp.loader.exec_module(C)
res = []
for spec in sys.argv[2:]:
    coin, day = spec.split(':')
    out = []
    for f in sorted(glob.glob(f'{C.OB}/{coin}_USDT-*.csv.gz')):
        if day.replace('-', '') in f: C.hour(f, out)
    out.sort(key=lambda r: r[0])
    a = np.array([(r[0], np.nan if r[1] is None else r[1], r[2], np.nan if r[3] is None else r[3], r[4]) for r in out], dtype=float)
    bins = a[:, 0].astype(np.int64)
    ok = np.isfinite(a[:, 1]) & np.isfinite(a[:, 3])
    tr = []
    for line in gzip.open(f'{C.TR}/{coin}_USDT/{day}.csv.gz', 'rt'):
        x = line.split(',')
        if x[0] == 'id': continue
        tr.append((int(float(x[3]) * 10), float(x[5])))
    tr = np.array(tr)
    r = {'coin': coin, 'day': day, 'trades': len(tr), 'bbo_rows': len(a), 'shift': {}}
    for s in range(-20, 21):
        j = np.searchsorted(bins, tr[:, 0] + s, side='left') - 1
        m = (j >= 0) & ok[np.clip(j, 0, None)]
        jj = j[m]; p = tr[m, 1]
        r['shift'][f'{s / 10:.1f}'] = round(float(((a[jj, 1] <= p) & (p <= a[jj, 3])).mean()), 4)
    best = max(r['shift'], key=r['shift'].get)
    r['best_shift'] = best; r['best_strict'] = r['shift'][best]; r['strict0'] = r['shift']['0.0']
    res.append(r)
    print(coin, day, len(tr), 'strict0', r['strict0'], 'best', best, r['best_strict'], flush=True)
json.dump(res, open(sys.argv[1], 'w'))
