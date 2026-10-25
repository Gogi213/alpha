#!/usr/bin/env python3
"""TK-402 П-28 v3.1 шаг 0/(а): сделки Gate фьюча вне [bid; ask] BBO gev2. Запуск (calc): /data/tk0380/npvenv/bin/python tk402-gate-out.py OUTDIR COIN... -> OUTDIR/COIN.json + COIN.bad.npy.
Сделка 'вне', если её цена вне [bid; ask] и состояния ДО (события t'<t), и ПОСЛЕ (t'<=t) — тогда BBO в этот момент устарел/дыра: запретный интервал = [последнее событие <= t; следующее событие > t) (исполнение запрещено, п.(а) Судьи).
Сделки до первого события и в часы без gev2 (вырожденные/нет файла) в счёт не идут — их режет маска часов. Считает также долю 'вне' по монете и сутки (отброс монеты: >=95 % сделок вне — шаг 0.6)."""
import sys, os, glob, gzip, json
import numpy as np
GEV = '/data/tk0402/gev2'; TR = '/srv/alpha-sda/tk372-gate/raw/fut'; DAYS = [f'2026-10-{d}' for d in range(15, 23)]
def coin_run(coin, out):
    fs = sorted(glob.glob(f'{GEV}/{coin}/??????????.npy')); a = [np.load(f) for f in fs]; a = [x for x in a if len(x)]
    if not a: return dict(coin=coin, error='нет gev2')
    a = np.concatenate(a); a = a[np.argsort(a[:, 0], kind='stable')]; t, bid, ask = a[:, 0].astype(np.int64), a[:, 1], a[:, 2]
    bad = []; days = {}
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
        if not tt: continue
        tt = np.array(tt, np.int64); pp = np.array(pp)
        pre = np.searchsorted(t, tt, side='left') - 1; post = np.searchsorted(t, tt, side='right') - 1
        have = pre >= 0; pr = np.clip(pre, 0, None); po = np.clip(post, 0, None); eps = 1e-12 * np.abs(pp) + 1e-12
        in_pre = (pp >= bid[pr] - eps) & (pp <= ask[pr] + eps); in_post = (pp >= bid[po] - eps) & (pp <= ask[po] + eps)
        outm = have & ~in_pre & ~in_post
        for i in np.flatnonzero(outm):
            n1 = post[i] + 1; bad.append((int(t[post[i]]), int(t[n1]) if n1 < len(t) else int(tt[i]) + 1))
        days[day] = dict(trades=int(len(tt)), before_first=int((~have).sum()), out=int(outm.sum()), strict_in_pre=int((in_pre & have).sum()))
    b = np.array(sorted(set(bad)), np.int64).reshape(-1, 2)
    if len(b):  # слить пересекающиеся
        m = [list(b[0])]
        for s, e in b[1:]:
            if s <= m[-1][1]: m[-1][1] = max(m[-1][1], e)
            else: m.append([s, e])
        b = np.array(m, np.int64)
    np.save(f'{out}/{coin}.bad.npy', b)
    tot = sum(v['trades'] for v in days.values()); o = sum(v['out'] for v in days.values())
    r = dict(coin=coin, trades=tot, out=o, out_share=round(o / tot, 6) if tot else None, bad_ms=int((b[:, 1] - b[:, 0]).sum()) if len(b) else 0, bad_n=int(len(b)), days=days)
    json.dump(r, open(f'{out}/{coin}.json', 'w')); return r
if __name__ == '__main__':
    out = sys.argv[1]; os.makedirs(out, exist_ok=True)
    for c in sys.argv[2:]:
        r = coin_run(c, out); print(c, {k: v for k, v in r.items() if k != 'days'}, flush=True)
