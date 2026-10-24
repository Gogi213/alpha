#!/usr/bin/env python3
"""TK-394 / G-016 шаг 1: замер расхождений Gate <-> Bybit по секундам (без сделки, без прогнозов).
Запуск (calc, /data/tk0380/npvenv/bin/python): tk394-spread.py OUTDIR COIN [COIN...]
Данные: Bybit середина/спред стакана по секундам (lob seconds) /data/tk0369/mid/{fut,spot}/<день>/<SYM>-<день>.csv.gz (mid2 = bid+ask в тиках, spr = ask-bid);
Gate фьюч BBO 1 с /data/tk0394/bbo/<COIN>.csv.gz (исправленный, tk394-gate-bbo.py) (sec,bid,ask); Gate спот — последняя цена сделки за секунду (стакана спота нет: только середина-замена, исполнимой разницы нет).
Бид/аск Bybit = (mid2 -/+ spr)/2 * tick; множитель 1000/1000000 в имени символа снимается. Состояние на конец секунды t у обоих; секунды gap=1 и Gate-данные старше 300 с — вне.
Пары (A,B): FF Gate фьюч/Bybit фьюч, FS Gate фьюч/Bybit спот, SF Gate спот/Bybit фьюч, SS Gate спот/Bybit спот, GB Gate фьюч/Gate спот, BB Bybit фьюч/Bybit спот (контроль базиса).
Исполнимая разница e = max(bidB-askA, bidA-askB)/mid бп (только если у обеих ног есть бид/аск). Пороги: 0, вход (taker A + taker B), круг (2x вход)."""
import sys, os, gzip, csv, glob, json
import numpy as np
BYB = '/data/tk0369/mid'; GBBO = '/data/tk0394/bbo'; GTR = '/srv/alpha-sda/tk372-gate/raw/spot'
DAYS = [f'2026-10-{d}' for d in range(15, 22)]
TK = {}
for r in csv.DictReader(open('/data/tk0394/bybit-ticks.csv', encoding='utf-8-sig'), delimiter=';'):
    TK[('spot' if r['market'].startswith('сп') else 'fut', r['symbol'])] = float(r['tick'])
FEE = {'GF': 5.0, 'GS': 10.0, 'BF': 5.5, 'BS': 10.0}   # taker, бп, VIP0
def mult(sym):
    for p, m in (('1000000', 1e6), ('10000', 1e4), ('1000', 1e3)):
        if sym.startswith(p): return m
    return 1.0
def bybit(sym, mk):
    ts, bid, ask = [], [], []
    tick = TK.get((mk, sym)); m = mult(sym)
    if tick is None: return None
    for d in DAYS:
        f = f'{BYB}/{mk}/{d}/{sym}-{d}.csv.gz'
        if not os.path.exists(f): continue
        a = np.genfromtxt(gzip.open(f, 'rt'), delimiter=',', skip_header=1, dtype=np.int64)
        ok = (a[:, 1] > 0) & (a[:, 12] == 0)
        mid2, spr = a[:, 1], a[:, 3]
        b = (mid2 - spr) / 2 * tick / m; k = (mid2 + spr) / 2 * tick / m
        b[~ok] = np.nan; k[~ok] = np.nan
        ts.append(a[:, 0]); bid.append(b); ask.append(k)
    if not ts: return None
    return np.concatenate(ts), np.concatenate(bid), np.concatenate(ask)
def asof(ts_src, val, grid, maxage=300):
    i = np.searchsorted(ts_src, grid, side='right') - 1
    ok = (i >= 0)
    ii = np.clip(i, 0, None)
    ok &= (grid - ts_src[ii]) <= maxage
    out = np.where(ok, val[ii], np.nan)
    return out
def gate_fut(coin):
    f = f'{GBBO}/{coin}.csv.gz'
    if not os.path.exists(f): return None
    a = np.genfromtxt(gzip.open(f, 'rt'), delimiter=',', dtype=float)
    if a.ndim < 2 or len(a) < 100: return None
    a = a[np.argsort(a[:, 0])]
    return a[:, 0].astype(np.int64), a[:, 1], a[:, 2]
def gate_spot(coin):
    t, p = [], []
    for d in DAYS + ['2026-10-22']:
        f = f'{GTR}/{coin}_USDT/{d}.csv.gz'
        if not os.path.exists(f): continue
        for row in csv.reader(gzip.open(f, 'rt')):
            if row[0] == 'id': continue
            t.append(int(row[1])); p.append(float(row[6]))
    if not t: return None
    t = np.array(t, dtype=np.int64); p = np.array(p)
    o = np.argsort(t, kind='stable'); t, p = t[o], p[o]
    last = np.r_[t[1:] != t[:-1], True]
    return t[last], p[last]
def episodes(mask):
    d = np.diff(np.r_[0, mask.astype(np.int8), 0])
    s = np.where(d == 1)[0]; e = np.where(d == -1)[0]
    return e - s
def stat(coin, pair, day, A, B, ca, cb, grid, noexec=False):
    """A,B = (bid,ask) массивы на сетке; ca/cb = тейкер бп ног"""
    n_all = len(grid)
    midA = (A[0] + A[1]) / 2; midB = (B[0] + B[1]) / 2
    v = np.isfinite(midA) & np.isfinite(midB)
    if v.sum() < 600: return None
    d = (np.log(midA[v]) - np.log(midB[v])) * 1e4
    row = dict(coin=coin, pair=pair, day=day, n=int(v.sum()), n_all=n_all,
               d_mean=float(d.mean()), d_med=float(np.median(d)), d_sd=float(d.std()), d_p01=float(np.percentile(d, 1)), d_p99=float(np.percentile(d, 99)),
               d_abs_med=float(np.median(np.abs(d - np.median(d)))))
    entry = ca + cb; rt = 2 * entry
    row['entry_bp'] = entry; row['round_bp'] = rt
    row['share_abs_gt_entry'] = float((np.abs(d - np.median(d)) > entry).mean())
    if not noexec and np.isfinite(A[0]).any() and np.isfinite(B[0]).any() and not np.isnan(A[1]).all():
        bA, kA, bB, kB = A[0], A[1], B[0], B[1]
        ev = np.isfinite(bA) & np.isfinite(kA) & np.isfinite(bB) & np.isfinite(kB)
        mid = np.where(ev, (bA + kA + bB + kB) / 4, np.nan)
        e1 = (bB - kA) / mid * 1e4; e2 = (bA - kB) / mid * 1e4
        e = np.where(ev, np.maximum(e1, e2), np.nan)
        # базис-поправка: константа пары = медиана середин-разницы (у спот-фьюч пары постоянная часть — фандинг/базис)
        row['n_exec'] = int(ev.sum())
        if ev.sum() > 600:
            # задержка 1 с без заглядывания: решение по секунде t-1 (сторона = какая из e1/e2 больше и > входа), исполнение по значению той же стороны на t
            sg = np.where(e1 >= e2, 1, 2)
            g_now = np.where(sg[:-1] == 1, e1[1:], e2[1:])
            sig = ev[:-1] & ev[1:] & (e[:-1] > entry)
            row['lag_n'] = int(sig.sum())
            if sig.sum() > 0:
                gg = g_now[sig]
                row['lag_mean_g'] = float(gg.mean()); row['lag_med_g'] = float(np.median(gg))
                row['lag_share_gt_0'] = float((gg > 0).mean()); row['lag_share_gt_entry'] = float((gg > entry).mean())
                row['lag_mean_net'] = float((gg - entry).mean())
            ee = e[ev]
            row['e_med'] = float(np.median(ee)); row['e_p99'] = float(np.percentile(ee, 99)); row['e_max'] = float(ee.max())
            for nm, th in (('0', 0.0), ('entry', entry), ('round', rt)):
                m = ev & (e > th)
                row[f'sec_gt_{nm}'] = int(m.sum())
                row[f'share_gt_{nm}'] = float(m.sum() / ev.sum())
                ep = episodes(m)
                row[f'ep_gt_{nm}'] = int(len(ep)); row[f'ep_med_len_{nm}'] = float(np.median(ep)) if len(ep) else 0.0
                row[f'ep_p90_len_{nm}'] = float(np.percentile(ep, 90)) if len(ep) else 0.0
    return row
def main():
    out = sys.argv[1]; os.makedirs(out, exist_ok=True)
    for coin in sys.argv[2:]:
        rows = []
        gf = gate_fut(coin); gs = gate_spot(coin)
        syms = {}
        for mk in ('fut', 'spot'):
            for pre in ('', '1000', '1000000', '10000'):
                s = f'{pre}{coin}USDT'
                if (mk, s) in TK and glob.glob(f'{BYB}/{mk}/2026-10-15/{s}-2026-10-15.csv.gz'): syms[mk] = s
        bf = bybit(syms['fut'], 'fut') if 'fut' in syms else None
        bs = bybit(syms['spot'], 'spot') if 'spot' in syms else None
        t0 = 1792022400  # 2026-10-15 00:00 UTC
        for di, day in enumerate(DAYS):
            grid = np.arange(t0 + di * 86400, t0 + (di + 1) * 86400, dtype=np.int64)
            def G(src):  # src=(ts,bid,ask)
                return (asof(src[0], src[1], grid), asof(src[0], src[2], grid)) if src is not None else None
            L = {}
            if gf: L['GF'] = (G(gf), FEE['GF'])
            if gs:
                p = asof(gs[0], gs[1], grid); L['GS'] = ((p, p), FEE['GS'])
            if bf: L['BF'] = (G(bf), FEE['BF'])
            if bs: L['BS'] = (G(bs), FEE['BS'])
            for pair, a, b in (('FF', 'GF', 'BF'), ('FS', 'GF', 'BS'), ('SF', 'GS', 'BF'), ('SS', 'GS', 'BS'), ('GB', 'GF', 'GS'), ('BB', 'BF', 'BS')):
                if a in L and b in L:
                    r = stat(coin, pair, day, L[a][0], L[b][0], L[a][1], L[b][1], grid, noexec=('GS' in (a, b)))
                    if r:
                        r['bybit_fut'] = syms.get('fut', ''); r['bybit_spot'] = syms.get('spot', '')
                        rows.append(r)
        json.dump(rows, open(f'{out}/{coin}.json', 'w'))
        print(coin, len(rows), flush=True)
if __name__ == '__main__': main()

