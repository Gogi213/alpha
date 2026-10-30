#!/usr/bin/env python3
"""TK-462 шаг 4б (P-TK462 §6 X1/X2): форма B якорь (E0·S1·TS·N=10), реальные сигналы Санчо, симулятор TK-455 (sancho_l3_trades.run_signal).
python tk462_step4b.py <csv.gz минуты> <tf> <fund_dir> <out.csv.gz>   (запуск из каталога с sancho_l3_*.py)
X2: задержка входа +0/+1/+2 минуты (tt0 сдвинут; стоп = экстремум, цель = свинг по данным <= t0 — не меняются).
X1 (delay 0): обе границы затронуты в минуте выхода (l<=стоп и h>цель) — симулятор в этом случае берёт стоп (консервативно)."""
import sys
import numpy as np, pandas as pd
import sancho_l3_trades as T
from sancho_l3_sig import sig_table
path, tf, fc, out = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4]
MK, TK = 1.26, 3.15
cn = T.Coin(path, tf, 2, fc)
sg = sig_table(path, tf, 2, ctl=False)
rows = []
for s in sg.to_dict("records"):
    if s.get("role") != "real":
        continue
    d = T.side_of(s["ev"], s["dir"])
    for dl in (0, 1, 2):
        s2 = dict(s); s2["tt0"] = s["tt0"] + dl * 60000
        res, info = T.run_signal(cn, s2, d, "B", "E0", 0, "S1", 0, "TS", 0, 10)
        if isinstance(res, str):
            rows.append(dict(sym=cn.sym, tf=tf, t0=s["tt0"], delay=dl, reason=res)); continue
        fee = TK + (MK if res["reason"] == "target" else TK)
        both = np.nan
        if dl == 0:
            o, h, l, c = cn.px[d]; ep = info["ep"]; mx = info["mx"]
            stop = ep - res["risk"] / 1e4 * abs(ep); tp = cn.target_s(d, s["t0"], abs(ep))
            both = float(tp is not None and l[mx] <= stop and h[mx] > d * tp) if res["reason"] in ("stop", "target") else 0.0
        rows.append(dict(sym=cn.sym, tf=tf, t0=s["tt0"], delay=dl, reason=res["reason"], gross=res["gross"], fee=fee, fund=res["fund"],
                         risk=res["risk"], both=both, year=pd.Timestamp(s["tt0"], unit="ms").year))
pd.DataFrame(rows).to_csv(out, index=False, compression="gzip")
