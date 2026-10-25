#!/usr/bin/env python3
"""TK-402 Gate фьюч: событийный BBO из потока изменений orderbooks (TK-379), БЕЗ корзин и секундных сеток.
Строка = каждая метка времени файла (как есть, десятые доли секунды), на которой BBO изменился; состояние — после всех строк с этой меткой.
Как tk394-gate-bbo-ms.py: set/make/take, округление 9 зн.; выход OUTDIR/COIN/<YYYYMMDDHH>.npy (N x 3 float64: t_ms метки события, bid, ask; nan = нет стороны).
Запуск: tk402-gate-bbo-ev.py OUTDIR COIN HOURFILE..."""
import sys, os, gzip, heapq
import numpy as np
def hour(path):
    bids = {}; asks = {}; hb = []; ha = []; out = []
    def best():
        while hb and bids.get(-hb[0], 0) <= 0: heapq.heappop(hb)
        while ha and asks.get(ha[0], 0) <= 0: heapq.heappop(ha)
        return (-hb[0] if hb else np.nan), (ha[0] if ha else np.nan)
    cur = None; last = (None, None)
    def emit():
        b, k = best()
        if (b, k) != last and not (b != b and k != k and last == (None, None)) :
            out.append((cur, b, k)); return (b, k)
        return last
    for line in gzip.open(path, 'rt'):
        t, a, p, s, _, _ = line.split(',')
        k = int(round(float(t) * 1000)); p = float(p); s = float(s)
        if cur is not None and k != cur: last = emit()
        cur = k
        isb = s > 0; d = bids if isb else asks; q = abs(s)
        if a == 'set': d[p] = q
        elif a == 'make': d[p] = round(d.get(p, 0) + q, 9)
        else: d[p] = round(d.get(p, 0) - q, 9)
        if d[p] <= 0: del d[p]
        else: heapq.heappush(hb, -p) if isb else heapq.heappush(ha, p)
    if cur is not None: emit()
    return np.array(out, dtype=np.float64).reshape(-1, 3)
if __name__ == '__main__':
    od, coin = sys.argv[1], sys.argv[2]
    os.makedirs(f'{od}/{coin}', exist_ok=True)
    for f in sys.argv[3:]:
        h = os.path.basename(f).split('-')[-1].split('.')[0]; o = f'{od}/{coin}/{h}.npy'
        if os.path.exists(o): continue
        np.save(o + '.tmp.npy', hour(f)); os.replace(o + '.tmp.npy', o)
    print(coin, 'ok', flush=True)
