#!/usr/bin/env python3
"""TK-394 возврат 5 (Судья): дыры книги и ленты Gate фьюча + cover по валидным окнам, 104 монеты x сутки 15-22.10.
Запуск (calc): tk394-holes2.py OUTDIR COIN... -> OUTDIR/COIN.json.
Окно часового файла = [первая метка, последняя метка] (как tk394-holes.py); сделка валидна, если её корзина (100 мс) внутри окна.
На монету-сутки: сделок всего/в окнах, сделки по часам (лента), секунды окон по часам (книга), strict/win в окнах,
промахи strict в окнах: расстояние в тиках до ближайшей стороны BBO (тик = мин. положительный шаг цен сделок монеты)."""
import sys, os, json, gzip, glob, importlib.util
import numpy as np
D = os.path.dirname(os.path.abspath(__file__))
sp = importlib.util.spec_from_file_location('cand', os.path.join(D, 'tk394-cand.py')); C = importlib.util.module_from_spec(sp); sp.loader.exec_module(C)
DAYS = [f'2026-10-{d}' for d in range(15, 23)]
def coin_run(coin):
    out = []; wins = {}
    for f in sorted(glob.glob(f'{C.OB}/{coin}_USDT-*.csv.gz')):
        n0 = len(out); C.hour(f, out)
        if len(out) > n0: wins[os.path.basename(f)[-17:-7]] = (out[n0][0], out[-1][0])   # YYYYMMDDHH
    out.sort(key=lambda r: r[0])
    a = np.array([(r[0], np.nan if r[1] is None else r[1], r[2], np.nan if r[3] is None else r[3], r[4]) for r in out], dtype=float)
    bins = a[:, 0].astype(np.int64); ok = np.isfinite(a[:, 1]) & np.isfinite(a[:, 3])
    trs = {}
    for day in DAYS:
        f = f'{C.TR}/{coin}_USDT/{day}.csv.gz'
        if not os.path.exists(f): continue
        tr = []
        for line in gzip.open(f, 'rt'):
            x = line.split(',')
            if x[0] == 'id': continue
            tr.append((float(x[3]), float(x[5])))
        trs[day] = np.array(tr).reshape(-1, 2)
    allp = np.unique(np.round(np.concatenate([t[:, 1] for t in trs.values()]), 12)) if trs else np.array([])
    tick = round(float(np.diff(allp)[np.diff(allp) > 1e-12].min()), 10) if len(allp) > 1 else None
    res = []
    for day in DAYS:
        h0 = int(__import__('calendar').timegm(__import__('time').strptime(day, '%Y-%m-%d')))
        hv = []; 
        for h in range(24):
            k = day.replace('-', '') + f'{h:02d}'
            w = wins.get(k); hv.append(round((w[1] - w[0]) / 10) if w else 0)
        r = dict(day=day, book_sec_by_hour=hv, valid_share=round(sum(hv) / 86400, 4))
        if day in trs:
            t = trs[day]; ts = (t[:, 0] * 10).astype(np.int64); p = t[:, 1]
            r['trades'] = len(t); r['trades_by_hour'] = np.bincount(np.clip(((t[:, 0] - h0) // 3600).astype(int), 0, 23), minlength=24).tolist()
            valid = np.zeros(len(t), bool)
            for k, (lo, hi) in wins.items():
                if k.startswith(day.replace('-', '')): valid |= (ts >= lo) & (ts <= hi)
            j = np.searchsorted(bins, ts, side='left') - 1
            m = (j >= 0) & ok[np.clip(j, 0, None)]
            jj = np.clip(j, 0, None)
            lo_b = np.where(m, a[jj, 1], np.nan); hi_a = np.where(m, a[jj, 3], np.nan)
            strict = m & (lo_b <= p) & (p <= hi_a)
            # win: мин. бид / макс. аск среди строк корзины сделки (строки с корзиной == ts) и предыдущей
            win = strict.copy(); nz = np.where(valid & m & ~strict)[0]
            kk = np.searchsorted(bins, ts[nz], side='right')
            for i, q in zip(nz, kk):
                seg = a[j[i] + 1:q] if q > j[i] + 1 else a[j[i]:j[i] + 1]
                seg = seg[np.isfinite(seg[:, 1]) & np.isfinite(seg[:, 3])]
                lo = min(a[j[i], 1], seg[:, 1].min()) if len(seg) else a[j[i], 1]; hi = max(a[j[i], 3], seg[:, 3].max()) if len(seg) else a[j[i], 3]
                win[i] = lo <= p[i] <= hi
            nv = int(valid.sum())
            r.update(trades_valid=nv, strict_valid=int((strict & valid).sum()), win_valid=int((win & valid).sum()),
                     strict_all=int(strict.sum()), strict_holes=int((strict & ~valid).sum()))
            miss = valid & m & ~strict
            if miss.any() and tick:
                dist = np.where(p[miss] < lo_b[miss], lo_b[miss] - p[miss], p[miss] - hi_a[miss]) / tick
                r.update(miss=int(miss.sum()), miss_ticks_med=round(float(np.median(dist)), 2), miss_ticks_p90=round(float(np.percentile(dist, 90)), 2),
                         miss_le2=int((dist <= 2.5).sum()), miss_gt2=int((dist > 2.5).sum()), miss_nowin=int((~win[miss]).sum()),
                         miss_nowin_gt2=int(((~win[miss]) & (dist > 2.5)).sum()))
        res.append(r)
    return dict(coin=coin, tick=tick, days=res)
if __name__ == '__main__':
    od = sys.argv[1]; os.makedirs(od, exist_ok=True)
    for c in sys.argv[2:]:
        try: json.dump(coin_run(c), open(f'{od}/{c}.json', 'w'))
        except Exception as e: json.dump(dict(coin=c, error=repr(e)), open(f'{od}/{c}.json', 'w'))
        print(c, flush=True)
