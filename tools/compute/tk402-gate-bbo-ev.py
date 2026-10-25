#!/usr/bin/env python3
"""TK-402 Gate фьюч: событийный BBO из потока изменений orderbooks (TK-379), БЕЗ корзин и секундных сеток.
Строка = каждая метка времени файла (как есть, десятые доли секунды), на которой BBO изменился; состояние — после всех строк с этой меткой.
Как tk394-gate-bbo-ms.py: set/make/take, округление 9 зн.; выход OUTDIR/COIN/<YYYYMMDDHH>.npy (N x 5 float64: t_ms метки события, bid, ask, bq, aq — контракты на лучшем уровне; nan = нет стороны). v2 (25.10, Судья tk402-p28v3 п.в): событие = смена цены ИЛИ размера лучшего уровня (как BBO Bybit TK-399); рядом OUTDIR/COIN/<час>.n — число строк сырого часа (причина дыр).
Запуск: tk402-gate-bbo-ev.py OUTDIR COIN HOURFILE..."""
import sys, os, gzip, heapq
import numpy as np
def hour(path):
    bids = {}; asks = {}; hb = []; ha = []; out = []
    def best():
        while hb and bids.get(-hb[0], 0) <= 0: heapq.heappop(hb)
        while ha and asks.get(ha[0], 0) <= 0: heapq.heappop(ha)
        b = -hb[0] if hb else np.nan; k = ha[0] if ha else np.nan
        return b, k, (bids[b] if hb else np.nan), (asks[k] if ha else np.nan)
    cur = None; last = (None, None); nraw = 0
    def emit():
        b, k, bq, aq = best()
        if (b, k, bq, aq) != last and not (b != b and k != k and last == (None, None)):
            out.append((cur, b, k, bq, aq)); return (b, k, bq, aq)
        return last
    for line in gzip.open(path, 'rt'):
        t, a, p, s, _, _ = line.split(',')
        nraw += 1; k = int(round(float(t) * 1000)); p = float(p); s = float(s)
        if cur is not None and k != cur: last = emit()
        cur = k
        isb = s > 0; d = bids if isb else asks; q = abs(s)
        if a == 'set': d[p] = q
        elif a == 'make': d[p] = round(d.get(p, 0) + q, 9)
        else: d[p] = round(d.get(p, 0) - q, 9)
        if d[p] <= 0: del d[p]
        else: heapq.heappush(hb, -p) if isb else heapq.heappush(ha, p)
    if cur is not None: emit()
    return np.array(out, dtype=np.float64).reshape(-1, 5), nraw
if __name__ == '__main__':
    od, coin = sys.argv[1], sys.argv[2]
    os.makedirs(f'{od}/{coin}', exist_ok=True)
    import glob
    OB = '/srv/alpha-sda/tk379-ob/futures_usdt/orderbooks/202610'
    fl = sys.argv[3:] or sorted(glob.glob(f'{OB}/{coin}_USDT-20261015*.csv.gz') + glob.glob(f'{OB}/{coin}_USDT-2026101[6-9]*.csv.gz') + glob.glob(f'{OB}/{coin}_USDT-2026102[0-2]*.csv.gz'))
    for f in fl:
        h = os.path.basename(f).split('-')[-1].split('.')[0]; o = f'{od}/{coin}/{h}.npy'
        if os.path.exists(o): continue
        a, nraw = hour(f); np.save(o + '.tmp.npy', a); os.replace(o + '.tmp.npy', o)
        open(f'{od}/{coin}/{h}.n', 'w').write(str(nraw))
    print(coin, 'ok', flush=True)
