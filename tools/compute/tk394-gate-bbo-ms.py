#!/usr/bin/env python3
"""TK-394 П-28 мс-шаг: BBO Gate фьюча с шагом 0,1 с (метки книги TK-379 — десятые доли секунды, пол точности). Единица = (монета, час).
Как tk394-gate-bbo.py (set/make/take, округление 9 зн.), но строка на каждую 0,1-с корзину с событиями; метка строки = конец корзины (корзина*100+100 мс):
состояние на T (мс) = последняя строка с t<=T видит только ПОЛНЫЕ корзины — без заглядывания вперёд.
Запуск: tk394-gate-bbo-ms.py OUTDIR COIN HOURFILE... -> OUTDIR/COIN/<YYYYMMDDHH>.npy (N x 3 float64: t_ms, bid, ask; nan = нет стороны)."""
import sys, os, gzip, heapq
import numpy as np
def hour(path):
    bids = {}; asks = {}; hb = []; ha = []; out = []
    def best():
        while hb and bids.get(-hb[0], 0) <= 0: heapq.heappop(hb)
        while ha and asks.get(ha[0], 0) <= 0: heapq.heappop(ha)
        return (-hb[0] if hb else np.nan), (ha[0] if ha else np.nan)
    cur = None
    for line in gzip.open(path, 'rt'):
        t, a, p, s, _, _ = line.split(',')
        k = int(round(float(t) * 10)); p = float(p); s = float(s)
        if cur is not None and k != cur:
            b, kk = best(); out.append((cur * 100 + 100, b, kk))
        cur = k
        isb = s > 0; d = bids if isb else asks; q = abs(s)
        if a == 'set': d[p] = q
        elif a == 'make': d[p] = round(d.get(p, 0) + q, 9)
        else: d[p] = round(d.get(p, 0) - q, 9)
        if d[p] <= 0: del d[p]
        else: heapq.heappush(hb, -p) if isb else heapq.heappush(ha, p)
    if cur is not None:
        b, kk = best(); out.append((cur * 100 + 100, b, kk))
    return np.array(out, dtype=np.float64).reshape(-1, 3)
if __name__ == '__main__':
    od, coin = sys.argv[1], sys.argv[2]
    os.makedirs(f'{od}/{coin}', exist_ok=True)
    for f in sys.argv[3:]:
        h = os.path.basename(f).split('-')[-1].split('.')[0]; o = f'{od}/{coin}/{h}.npy'
        if os.path.exists(o): continue
        np.save(o + '.tmp.npy', hour(f)); os.replace(o + '.tmp.npy', o)
    print(coin, 'ok', flush=True)
