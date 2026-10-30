#!/usr/bin/env python3
"""TK-469: турбо tk461_open.py — тот же вывод (содержимое после gunzip байт в байт), быстрее.
python tk469_open2.py <csv.gz минуты> <tf>:<d2b-ev.csv.gz>:<out.csv.gz> [...]
Отличия от tk461_open.py: (1) минуты читаются один раз на монету для всех ТФ (были: Coin() на пару — повторное чтение, bars, свинги,
фандинг с HTTP; open берёт из Coin только px и tm); (2) три стопа SF0.5/1/2 + SS одного события — один проход
(максимум high и минимум low нарастающим итогом, первое касание стопа через searchsorted по убывающему минимуму) вместо
trade_fast на стоп; (3) бары паузы/против — одним reshape-максимумом; (4) gzip уровня 1 (содержимое то же).
Окно с нечисловыми значениями — откат на trade_fast (эталон)."""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tk461_fast import trade_fast  # noqa: E402
from tk461_open import MS, N, OPEN_END, is_open_coin  # noqa: E402


def load(path):
    d = pd.read_csv(path, usecols=[0, 1, 2, 3, 4])
    tm, o, h, l, c = (d.iloc[:, i].values for i in range(5))
    o, h, l, c = o.astype(float), h.astype(float), l.astype(float), c.astype(float)
    return tm, {1: (o, h, l, c), -1: (-o, -l, -h, -c)}


def rows_for(tm, px, tf, s):
    d = int(s["side"])
    o, h, l, c = px[d]
    e = int(np.searchsorted(tm, s["tt0"] + tf * 60000))
    last = int(np.searchsorted(tm, s["tt0"] + (N + 1) * tf * 60000)) - 1
    if e > last or last >= len(o) or e + tf * N > len(o):
        return []
    ep = o[e]
    key = (s["ev"], s["dir"], d)
    out = []
    stops = [(f"SF{m}", m * s["atr_bps"]) for m in MS]
    if np.isfinite(s.get("ss_bps", np.nan)):
        stops.append(("SS", float(s["ss_bps"])))
    H, L, O = h[e:last + 1], l[e:last + 1], o[e:last + 1]
    dists = [rb / 1e4 * abs(ep) for _, rb in stops]
    if np.isfinite(H).all() and np.isfinite(L).all() and np.isfinite(O).all():
        ext = np.maximum.accumulate(H)
        nlow = -np.minimum.accumulate(L)  # неубывающий; первое k с L[k] <= stop  <=>  -cummin[k] >= -stop
        ks = np.searchsorted(nlow, -(ep - np.array(dists)), side="left")
        for (name, _), dist, k in zip(stops, dists, ks.tolist()):
            m = ext[-1] if k >= len(H) else (ext[k - 1] if k > 0 else ep)
            out.append(("mfe_r", *key, name, (max(ep, m) - ep) / dist))
    else:
        for (name, _), dist in zip(stops, dists):
            _, _, _, mfe = trade_fast(o, h, l, c, e, last, ep, ep - dist)
            out.append(("mfe_r", *key, name, (mfe - ep) / dist))
    nb = min(N, (len(o) - e) // tf)
    if nb > 0:
        atr_px = s["atr_bps"] / 1e4 * abs(ep)
        span = slice(e, e + nb * tf)
        bhs = h[span].reshape(nb, tf).max(axis=1).tolist()
        bls = l[span].reshape(nb, tf).min(axis=1).tolist()
        against = (c[e + tf - 1:e + nb * tf:tf] < o[span][::tf]).tolist()
        mx, run = ep, 0
        for j in range(nb):
            if bhs[j] > mx:
                if run:
                    out.append(("pause", *key, "", float(run)))
                mx, run = bhs[j], 0
            else:
                run += 1
            if against[j]:
                out.append(("against", *key, "", (bhs[j] - bls[j]) / atr_px))
        if run:
            out.append(("pause", *key, "", float(run)))
    return out


if __name__ == "__main__":
    path, jobs = sys.argv[1], [a.split(":") for a in sys.argv[2:]]
    sym = os.path.basename(path).split(".")[0]
    loaded = load(path) if is_open_coin(sym) else None
    for tf, evp, outp in jobs:
        tf, rows = int(tf), []
        if loaded is not None:
            tm, px = loaded
            ev = pd.read_csv(evp)
            ev = ev[(ev.tt0 <= OPEN_END) & ev.atr_bps.notna() & (ev.atr_bps > 0)]
            for s in ev.to_dict("records"):
                rows += rows_for(tm, px, tf, s)
        pd.DataFrame(rows, columns=["kind", "ev", "dir", "side", "stop", "val"]).assign(sym=sym, tf=tf).to_csv(
            outp, index=False, compression={"method": "gzip", "compresslevel": 1})
        print(outp, len(rows))
