#!/usr/bin/env python3
"""TK-394 возврат 4: дыры потока стакана Gate (TK-379) и покрытие сделок по валидным окнам.
Окно файла-часа = [первая метка (снимок), последняя метка]; после последней метки до следующего снимка книга устарела.
Запуск: tk394-holes.py OUT.json COIN:DAY ... ; на монету-сутки: доля суток в валидных окнах, часы без событий/обрезанные,
strict-покрытие сделок (BBO строго до корзины, как в tk394-shift) всего / внутри валидных окон / в дырах."""
import sys, os, json, gzip, glob, importlib.util
import numpy as np
D = os.path.dirname(os.path.abspath(__file__))
sp = importlib.util.spec_from_file_location('cand', os.path.join(D, 'tk394-cand.py')); C = importlib.util.module_from_spec(sp); sp.loader.exec_module(C)
res = []
for spec in sys.argv[2:]:
    coin, day = spec.split(':')
    out = []; wins = []
    for f in sorted(glob.glob(f'{C.OB}/{coin}_USDT-*.csv.gz')):
        if day.replace('-', '') in f:
            n0 = len(out); C.hour(f, out)
            if len(out) > n0: wins.append((out[n0][0], out[-1][0], os.path.basename(f)))
    out.sort(key=lambda r: r[0])
    a = np.array([(r[0], np.nan if r[1] is None else r[1], r[2], np.nan if r[3] is None else r[3], r[4]) for r in out], dtype=float)
    bins = a[:, 0].astype(np.int64); ok = np.isfinite(a[:, 1]) & np.isfinite(a[:, 3])
    tr = []
    for line in gzip.open(f'{C.TR}/{coin}_USDT/{day}.csv.gz', 'rt'):
        x = line.split(',')
        if x[0] == 'id': continue
        tr.append((int(float(x[3]) * 10), float(x[5])))
    tr = np.array(tr)
    valid = np.zeros(len(tr), bool); vsec = 0.0; short = []
    for lo, hi, nm in wins:
        valid |= (tr[:, 0] >= lo) & (tr[:, 0] <= hi); vsec += (hi - lo) / 10
        if hi - lo < 35900: short.append([nm[-9:-7], round((hi - lo) / 10)])
    j = np.searchsorted(bins, tr[:, 0], side='left') - 1
    m = (j >= 0) & ok[np.clip(j, 0, None)]
    hit = np.zeros(len(tr), bool); jj = j[m]; p = tr[m, 1]
    hit[m] = (a[jj, 1] <= p) & (p <= a[jj, 3])
    r = {'coin': coin, 'day': day, 'trades': len(tr), 'files_with_rows': len(wins), 'valid_sec': round(vsec), 'valid_share': round(vsec / 86400, 4),
         'short_hours_[hh,sec]': short, 'trades_valid_share': round(float(valid.mean()), 4),
         'strict_all': round(float(hit.mean()), 4), 'strict_valid': round(float(hit[valid].mean()), 4) if valid.any() else None,
         'strict_holes': round(float(hit[~valid].mean()), 4) if (~valid).any() else None}
    res.append(r); print(json.dumps(r), flush=True)
json.dump(res, open(sys.argv[1], 'w'))
