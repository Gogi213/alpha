#!/usr/bin/env python3
"""TK-461 / P-TK461 §8, §12.2 п.4–5: выборки «открытия» для k (БУ, ОТ5 x), M (ОТ3), v (ОТ4). Исходов (P&L) не считает.
python tk461_open.py <csv.gz минуты> <tf> <d2b-ev.csv.gz> <funding_cache> <out.csv.gz>
Открытие: md5(символ) чётный (int(hexdigest,16)%2==0) и tt0 <= 2023-12-31 UTC. Строка выхода: kind,ev,dir,side,stop,val.
 mfe_r  — MFE/R до стопа либо дедлайна N=10 (трейд без цели; стоп SF0.5/1/2 и SS), R = расстояние стопа в bps.
 pause  — длины пауз (баров ТФ подряд без нового максимума MFE, run>=1, окно 10 баров от входа; последняя пауза усечена окном).
 against— (high-low)/ATR14(t0-1, в цене) баров против позиции (close<open в ориентации сделки)."""
import hashlib
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk461_sim as S  # noqa: E402
from tk461_fast import trade_fast  # noqa: E402
from sancho_l3_trades import Coin  # noqa: E402

N, MS = 10, (0.5, 1.0, 2.0)
OPEN_END = int(pd.Timestamp("2023-12-31 23:59:59", tz="UTC").timestamp() * 1000)


def is_open_coin(sym):
    return int(hashlib.md5(sym.encode()).hexdigest(), 16) % 2 == 0


def rows_for(cn, s):
    tf, d = cn.tf, int(s["side"])
    o, h, l, c = cn.px[d]
    e = int(np.searchsorted(cn.tm, s["tt0"] + tf * 60000))
    last = int(np.searchsorted(cn.tm, s["tt0"] + (N + 1) * tf * 60000)) - 1
    if e > last or last >= len(o) or e + tf * N > len(o):
        return []
    ep = o[e]
    key = (s["ev"], s["dir"], d)
    out = []
    stops = [(f"SF{m}", m * s["atr_bps"]) for m in MS]
    if np.isfinite(s.get("ss_bps", np.nan)):
        stops.append(("SS", float(s["ss_bps"])))
    for name, rb in stops:
        dist = rb / 1e4 * abs(ep)
        _, _, _, mfe = trade_fast(o, h, l, c, e, last, ep, ep - dist)
        out.append(("mfe_r", *key, name, (mfe - ep) / dist))
    nb = min(N, (len(o) - e) // tf)
    atr_px = s["atr_bps"] / 1e4 * abs(ep)
    mx, run = ep, 0
    for j in range(nb):
        a, b = e + j * tf, e + (j + 1) * tf
        bh, bl = h[a:b].max(), l[a:b].min()
        if bh > mx:
            if run:
                out.append(("pause", *key, "", float(run)))
            mx, run = bh, 0
        else:
            run += 1
        if c[b - 1] < o[a]:
            out.append(("against", *key, "", (bh - bl) / atr_px))
    if run:
        out.append(("pause", *key, "", float(run)))
    return out


if __name__ == "__main__":
    path, tf, evp, fc, outp = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4], sys.argv[5]
    sym = os.path.basename(path).split(".")[0]
    rows = []
    if is_open_coin(sym):
        cn = Coin(path, tf, 2, fc)
        ev = pd.read_csv(evp)
        ev = ev[(ev.tt0 <= OPEN_END) & ev.atr_bps.notna() & (ev.atr_bps > 0)]
        for s in ev.to_dict("records"):
            rows += rows_for(cn, s)
    pd.DataFrame(rows, columns=["kind", "ev", "dir", "side", "stop", "val"]).assign(sym=sym, tf=tf).to_csv(
        outp, index=False, compression="gzip")
    print(outp, len(rows))
