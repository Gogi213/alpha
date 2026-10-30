#!/usr/bin/env python3
"""TK-461 / P-TK461 §5 C-X, шаг D2a: контроль к базе выходов — до NC=5 случайных баров той же монеты и ТФ, того же слота суток UTC
(кроме дня события), ±30 сут, ATR14/цена в пределах ×1,25, без требования сигнала; направление и выход события, seed 461.
python tk461_ctrl.py <csv.gz минуты> <tf> <events.csv.gz> <funding_cache> <out.csv.gz>   (PYTHONPATH=/data/tk0455:<этот каталог>)
Строка как у tk461_scan + evk (ключ события: tt0 события) и cj (номер контроля). ev = события «C-<ev>»."""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk461_scan as K  # noqa: E402
from sancho import bars, atr14  # noqa: E402
from sancho_l3_trades import Coin  # noqa: E402

NC, DAY, RATIO, SEED = 5, 86400000, 1.25, 461


def controls(path, tf, ev):
    a = bars(path, tf)
    ok = a.ok.values
    h, l, c = (a[x].values.astype(float) for x in "hlc")
    atr = atr14(h, l, c)
    ab = 1e4 * np.r_[np.nan, atr[:-1]] / np.abs(c)
    good = ok & np.r_[False, ok[:-1]] & np.isfinite(ab)
    ts = a.t.values
    slot = ts % DAY
    day = ts // DAY
    rng = np.random.default_rng(SEED)
    out = []
    for s in ev.to_dict("records"):
        sl = s["tt0"] % DAY
        d0 = s["tt0"] // DAY
        m = good & (slot == sl) & (np.abs(day - d0) <= 30) & (day != d0) & (ab >= s["atr_bps"] / RATIO) & (ab <= s["atr_bps"] * RATIO)
        idx = np.flatnonzero(m)
        if len(idx) > NC:
            idx = np.sort(rng.choice(idx, NC, replace=False))
        for cj, j in enumerate(idx):
            out.append(dict(sym=s["sym"], tf=tf, ev="C-" + s["ev"], dir=s["dir"], side=s["side"], t0=int(j), tt0=int(ts[j]),
                            atr_bps=float(ab[j]), evk=int(s["tt0"]), cj=cj))
    return pd.DataFrame(out)


if __name__ == "__main__":
    path, tf, evp, fc, outp = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4], sys.argv[5]
    ev = pd.read_csv(evp)
    cd = controls(path, tf, ev)
    cn = Coin(path, tf, 2, fc)
    rows = []
    for s in cd.to_dict("records"):
        d = int(s["side"])
        for cell in K.CELLS:
            r = K.one(cn, s, d, cell)
            base = dict(sym=cn.sym, tf=tf, ev=s["ev"], dir=s["dir"], t0=s["tt0"], side=d, stop=cell[0], take=cell[2], par=cell[3],
                        evk=s["evk"], cj=s["cj"])
            rows.append(dict(**base, reason=r) if isinstance(r, str) else dict(**base, **r))
    pd.DataFrame(rows).to_csv(outp, index=False, compression="gzip")
    print(outp, len(cd), len(rows))
