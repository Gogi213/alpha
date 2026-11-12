#!/usr/bin/env python3
"""TK-394 возврат 2 (Судья): доделка по 6 монетам-кандидатам (ERA IOST DOOD BMT FLOCK WAL).
Запуск (calc): tk394-cand.py OUTDIR COIN... -> OUTDIR/COIN.json
 1) BBO Gate фьюча в 100-мс корзинах из стакана TK-379 (с размерами на BBO, контрактов) -> доля сделок Gate фьюча в [бид;аск] по дням:
    strict = BBO состояния ДО корзины сделки, win = [мин бид; макс аск] по строкам корзины сделки включительно;
 2) сигналы как в tk394-spread3.py (FF/FS, базис 1ч/24ч по прошлому, свежесть) -> медиана размера Gate на бите ноги, $ (контрактов x множитель x цена),
    медиана спреда Bybit в бп и медиана дневного d (проверка контракта: отношение цен);
 3) по дням: net против круга = g_mean - 2*вход (и со скидкой Gate 35 %) из spread3/COIN.json."""
import sys, os, json, gzip, glob, heapq, bisect, importlib.util
import numpy as np
D = os.path.dirname(os.path.abspath(__file__))
def load(name, f):
    sp = importlib.util.spec_from_file_location(name, os.path.join(D, f)); m = importlib.util.module_from_spec(sp); sp.loader.exec_module(m); return m
S3 = load('s3', 'tk394-spread3.py'); S = S3.S
OB = '/srv/alpha-sda/tk379-ob/futures_usdt/orderbooks/202610'; TR = '/srv/alpha-sda/tk372-gate/raw/fut'
QM = {'ERA': 10, 'IOST': 10, 'DOOD': 1000, 'BMT': 10, 'FLOCK': 1, 'WAL': 10}   # quanto_multiplier Gate (REST contracts, 25.10)
T0 = S3.T0; ND = S3.ND; N = S3.N
def hour(path, out):
    """строки (корзина 100 мс, бид, размер бида, аск, размер аска) после последнего события корзины"""
    bids = {}; asks = {}; hb = []; ha = []
    def best():
        while hb and bids.get(-hb[0], 0) <= 0: heapq.heappop(hb)
        while ha and asks.get(ha[0], 0) <= 0: heapq.heappop(ha)
        b = -hb[0] if hb else None; k = ha[0] if ha else None
        return b, (bids[b] if b is not None else 0.0), k, (asks[k] if k is not None else 0.0)
    cur = None
    for line in gzip.open(path, 'rt'):
        t, a, p, s, _, _ = line.split(',')
        ts = int(float(t) * 10); p = float(p); s = float(s)
        if cur is not None and ts != cur: out.append((cur,) + best())
        cur = ts
        isb = s > 0; d = bids if isb else asks; q = abs(s)
        if a == 'set': d[p] = q
        elif a == 'make': d[p] = round(d.get(p, 0) + q, 9)
        else: d[p] = round(d.get(p, 0) - q, 9)
        if d[p] <= 0: del d[p]
        else: heapq.heappush(hb, -p) if isb else heapq.heappush(ha, p)
    if cur is not None: out.append((cur,) + best())
def bbo(coin):
    out = []
    for f in sorted(glob.glob(f'{OB}/{coin}_USDT-*.csv.gz')): hour(f, out)
    out.sort(key=lambda r: r[0])
    a = np.array([(r[0], np.nan if r[1] is None else r[1], r[2], np.nan if r[3] is None else r[3], r[4]) for r in out], dtype=float)
    return a   # cols: bin, bid, bidsz, ask, asksz
def trade_check(coin, a):
    """по дням: доля сделок в [бид;аск] (strict / win) по 100-мс корзинам"""
    res = []
    bins = a[:, 0].astype(np.int64)
    for di in range(ND):
        day = f'2026-10-{15 + di}'
        f = f'{TR}/{coin}_USDT/{day}.csv.gz'
        if not os.path.exists(f): res.append(None); continue
        tn = ti = tw = 0
        for line in gzip.open(f, 'rt'):
            x = line.split(',')
            if x[0] == 'id': continue
            tb = int(float(x[3]) * 10); pr = float(x[5])
            j = int(np.searchsorted(bins, tb, side='left')) - 1
            if j < 0 or not np.isfinite(a[j, 1]) or not np.isfinite(a[j, 3]): continue
            tn += 1; ti += (a[j, 1] <= pr <= a[j, 3])
            k = int(np.searchsorted(bins, tb, side='right'))
            w = a[j + 1:k] if k > j + 1 else a[j:j + 1]
            w = w[np.isfinite(w[:, 1]) & np.isfinite(w[:, 3])]
            lo = min(a[j, 1], w[:, 1].min()) if len(w) else a[j, 1]; hi = max(a[j, 3], w[:, 3].max()) if len(w) else a[j, 3]
            tw += (lo <= pr <= hi)
        res.append(dict(day=day, trades=tn, strict=ti / max(tn, 1), win=tw / max(tn, 1)))
    return res
def signals(coin, a, qm):
    """глубина Gate на нужной ноге в свежих сигналах, спред Bybit, дневной d"""
    # 1-с сетка: состояние на конец секунды = последняя корзина <= sec*10+9
    grid = np.arange(T0, T0 + N, dtype=np.int64)
    i = np.searchsorted(a[:, 0], grid * 10 + 9, side='right') - 1
    ok = i >= 0; ii = np.clip(i, 0, None)
    age = np.where(ok, grid * 10 + 9 - a[ii, 0], 10**9) / 10.0
    gb = a[ii, 1]; gk = a[ii, 3]; gbs = a[ii, 2]; gas = a[ii, 4]
    gfresh = age <= 1
    syms = {}
    for mk in ('fut', 'spot'):
        for pre in ('', '1000', '1000000', '10000'):
            s = f'{pre}{coin}USDT'
            if (mk, s) in S.TK and glob.glob(f'{S.BYB}/{mk}/2026-10-15/{s}-2026-10-15.csv.gz'): syms[mk] = s
    out = []
    for pair, mk, fb in (('FF', 'fut', S.FEE['BF']), ('FS', 'spot', S.FEE['BS'])):
        if mk not in syms: continue
        r = S.bybit(syms[mk], mk)
        if r is None: continue
        bb, _ = S3.asof_age(r[0], r[1], grid); bk, bage = S3.asof_age(r[0], r[2], grid)
        bfresh = (bage <= 1) & np.isfinite(bk)
        entry = S.FEE['GF'] + fb
        ev = np.isfinite(gb) & np.isfinite(gk) & np.isfinite(bb) & np.isfinite(bk) & (age <= 300)
        mid = np.where(ev, (gb + gk + bb + bk) / 4, np.nan)
        midA = (gb + gk) / 2; midB = (bb + bk) / 2
        d = np.where(ev, (np.log(midA) - np.log(midB)) * 1e4, np.nan)
        e1 = (bb - gk) / mid * 1e4; e2 = (gb - bk) / mid * 1e4
        fresh = ev & gfresh & bfresh
        row = dict(coin=coin, pair=pair, d_med_by_day=[float(np.nanmedian(d[k * 86400:(k + 1) * 86400])) for k in range(ND)],
                   price_ratio_med=float(np.nanmedian(midA / midB)))
        for base, (win, step) in (('1h', (3600, 60)), ('24h', (86400, 600))):
            b = S3.past_median(d, win, step)
            e1d = e1 + b; e2d = e2 - b
            side = np.where(e1d >= e2d, 1, 2); ed = np.where(side == 1, e1d, e2d)
            idx = np.arange(0, N - 1)
            m = np.isfinite(ed[idx]) & (ed[idx] > entry) & fresh[idx] & fresh[idx + 1]
            ix = idx[m] + 1                       # исполнение в t = i+1
            s1 = side[ix - 1] == 1                # 1: купить Gate (аск Gate) / продать Bybit; 2: продать Gate (бид Gate) / купить Bybit
            gsz = np.where(s1, gas[ix], gbs[ix]) * qm * np.where(s1, gk[ix], gb[ix])    # $ на BBO Gate-ноги
            spr_b = (bk[ix] - bb[ix]) / midB[ix] * 1e4
            spr_g = (gk[ix] - gb[ix]) / midA[ix] * 1e4
            row[base] = dict(n_sig=int(len(ix)), gate_bbo_usd_med=float(np.median(gsz)) if len(ix) else None,
                             gate_bbo_usd_p25=float(np.percentile(gsz, 25)) if len(ix) else None,
                             bybit_spread_bp_med=float(np.median(spr_b)) if len(ix) else None, gate_spread_bp_med=float(np.median(spr_g)) if len(ix) else None)
        out.append(row)
    return out
if __name__ == '__main__':
    od = sys.argv[1]; os.makedirs(od, exist_ok=True)
    for c in sys.argv[2:]:
        a = bbo(c); qm = QM[c]
        tc = trade_check(c, a)
        sg = signals(c, a, qm)
        s3 = json.load(open(f'/data/tk0394/spread3/{c}.json'))
        json.dump(dict(coin=coin_ if False else c, trade_check=tc, signals=sg, s3=s3), open(f'{od}/{c}.json', 'w')); print(c, flush=True)
