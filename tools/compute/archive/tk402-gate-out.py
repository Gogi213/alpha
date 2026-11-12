#!/usr/bin/env python3
"""TK-402 П-28 v3.1 шаг 0/(а′, решение Судьи 25.10): вырез только если цена сделки вне ОГИБАЮЩЕЙ [min bid; max ask] состояний с метками в [t-200; t+200] мс (+ последнее до окна) больше чем на 1 тик (тик Gate — измерен: min положительная разность цен монеты).
Старая проба (а), справочно: сделки Gate фьюча вне [bid; ask] BBO gev2. Запуск (calc): /data/tk0380/npvenv/bin/python tk402-gate-out.py OUTDIR COIN... -> OUTDIR/COIN.json + COIN.bad.npy.
(а): сделки Gate фьюча вне [bid; ask] BBO gev2. Запуск (calc): /data/tk0380/npvenv/bin/python tk402-gate-out.py OUTDIR COIN... -> OUTDIR/COIN.json + COIN.bad.npy.
Сделка 'вне', если её цена вне [bid; ask] и состояния ДО (события t'<t), и ПОСЛЕ (t'<=t) — тогда BBO в этот момент устарел/дыра: запретный интервал = [последнее событие <= t; следующее событие > t) (исполнение запрещено, п.(а) Судьи).
Сделки до первого события и в часы без gev2 (вырожденные/нет файла) в счёт не идут — их режет маска часов. Считает также долю 'вне' по монете и сутки (отброс монеты: >=95 % сделок вне — шаг 0.6)."""
import sys, os, glob, gzip, json
import numpy as np
GEV = '/data/tk0402/gev2'; TR = '/srv/alpha-sda/tk372-gate/raw/fut'; DAYS = [f'2026-10-{d}' for d in range(15, 23)]
def coin_run(coin, out):
    W = 200
    fs = sorted(glob.glob(f'{GEV}/{coin}/??????????.npy')); a = [np.load(f) for f in fs]; a = [x for x in a if len(x)]
    if not a: return dict(coin=coin, error='нет gev2')
    a = np.concatenate(a); a = a[np.argsort(a[:, 0], kind='stable')]; t, bid, ask = a[:, 0].astype(np.int64), a[:, 1], a[:, 2]
    bad = []; days = {}; D = {}
    for day in DAYS:
        f = f'{TR}/{coin}_USDT/{day}.csv.gz'
        if not os.path.exists(f): continue
        tt = []; pp = []
        try:
            for line in gzip.open(f, 'rt'):
                x = line.split(',')
                if x[0] == 'id': continue
                tt.append(round(float(x[3]) * 1000)); pp.append(float(x[5]))
        except EOFError: pass
        if tt: D[day] = (np.array(tt, np.int64), np.array(pp))
    if not D: return dict(coin=coin, error='нет сделок')
    allp = np.unique(np.concatenate([v[1] for v in D.values()] + [bid, ask])); df = np.diff(allp); df = df[df > 1e-9 * allp[1:]]
    tick = float(df.min()) if len(df) else 0.0
    import datetime as dt  # часы-маски как в step2.bad_intervals (нет файла / .n<100): сделки в них не считаются (книги там нет), их время вычитается из выреза
    masked = []
    for day in DAYS:
        d0 = dt.datetime.strptime(day, '%Y-%m-%d').replace(tzinfo=dt.timezone.utc)
        for h in range(24):
            f = f"{GEV}/{coin}/{(d0 + dt.timedelta(hours=h)).strftime('%Y%m%d%H')}"
            try: n = int(open(f + '.n').read()) if os.path.exists(f + '.npy') else -1
            except OSError: n = -1
            if n < 100: masked.append((int(d0.timestamp() * 1000) + h * 3600000, int(d0.timestamp() * 1000) + (h + 1) * 3600000))
    mk = np.array(masked, np.int64).reshape(-1, 2); n_masked_tr = 0
    for day, (tt, pp) in D.items():
        im = np.searchsorted(mk[:, 0], tt, side='right') - 1; inmask = (im >= 0) & (tt < mk[np.clip(im, 0, None), 1]) if len(mk) else np.zeros(len(tt), bool)
        n_masked_tr += int(inmask.sum()); tt = tt[~inmask]; pp = pp[~inmask]
        if not len(tt): continue
        pre = np.searchsorted(t, tt, side='left') - 1; post = np.searchsorted(t, tt, side='right') - 1
        have = pre >= 0; pr = np.clip(pre, 0, None); po = np.clip(post, 0, None); eps = 1e-12 * np.abs(pp) + 1e-12
        in_pre = (pp >= bid[pr] - eps) & (pp <= ask[pr] + eps); in_post = (pp >= bid[po] - eps) & (pp <= ask[po] + eps)
        out_a = have & ~in_pre & ~in_post
        lo = np.clip(np.searchsorted(t, tt - W, side='left') - 1, 0, None); hi = np.searchsorted(t, tt + W, side='right')  # состояния lo..hi-1
        mn = bid[lo].copy(); mx = ask[lo].copy(); k = 1
        while True:
            idx = lo + k; act = idx < hi
            if not act.any(): break
            ii = np.flatnonzero(act); mn[ii] = np.minimum(mn[ii], bid[idx[ii]]); mx[ii] = np.maximum(mx[ii], ask[idx[ii]]); k += 1
        outm = have & ((pp < mn - tick - eps) | (pp > mx + tick + eps))
        for i in np.flatnonzero(outm):
            n1 = post[i] + 1; bad.append((int(t[post[i]]), int(t[n1]) if n1 < len(t) else int(tt[i]) + 1))
        days[day] = dict(trades=int(len(tt)), before_first=int((~have).sum()), out=int(outm.sum()), out_a=int(out_a.sum()), strict_in_pre=int((in_pre & have).sum()))
    b = np.array(sorted(set(bad)), np.int64).reshape(-1, 2)
    if len(b):  # слить пересекающиеся
        m = [list(b[0])]
        for s, e in b[1:]:
            if s <= m[-1][1]: m[-1][1] = max(m[-1][1], e)
            else: m.append([s, e])
        b = np.array(m, np.int64)
    np.save(f'{out}/{coin}.bad.npy', b)
    tot = sum(v['trades'] for v in days.values()); o = sum(v['out'] for v in days.values())
    bms = 0
    for s0, s1 in b:
        cov = s1 - s0
        for m0, m1 in mk: cov -= max(0, min(s1, m1) - max(s0, m0))
        bms += cov
    bms = int(bms); mms = int((mk[:, 1] - mk[:, 0]).sum()) if len(mk) else 0
    r = dict(coin=coin, tick=tick, trades=tot, out=o, out_a=sum(v['out_a'] for v in days.values()), out_share=round(o / tot, 6) if tot else None, bad_ms=bms, bad_share=round(bms / (len(DAYS) * 86400000 - mms), 6), masked_h=len(mk), trades_in_masked=n_masked_tr, bad_n=int(len(b)), days=days)
    json.dump(r, open(f'{out}/{coin}.json', 'w')); return r
if __name__ == '__main__':
    out = sys.argv[1]; os.makedirs(out, exist_ok=True)
    for c in sys.argv[2:]:
        r = coin_run(c, out); print(c, {k: v for k, v in r.items() if k != 'days'}, flush=True)
