#!/usr/bin/env python3
"""Анализ F (строка «структурный разрыв»): длинные эпизоды (серия g >= g_entry дольше 30 / 60 минут) при глубине >= 50 bps - сколько их, на скольких активах, какая доля на парах с Bybit,
и точный стратифицированный по активам тест «Bybit-концентрация» (внутри актива длинные эпизоды размещены случайно среди его эпизодов). Эпизоды - §5.2 (таблица ep)."""
import argparse
import csv
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ts_stats as S                  # noqa: E402
from ts_analyze_e import hyper_pmf    # noqa: E402


def strat(ai, flag, event):
    pmf = np.array([1.0])
    mean = var = 0.0
    for a in np.unique(ai[event]):
        mm = ai == a
        s = int((event & mm).sum())
        pa = hyper_pmf(int(mm.sum()), int((flag & mm).sum()), s)
        pmf = np.convolve(pmf, pa)
        js = np.arange(s + 1)
        e1 = float((pa * js).sum())
        mean += e1
        var += float((pa * js * js).sum()) - e1 * e1
    obs = int((event & flag).sum())
    return obs, mean, math.sqrt(max(var, 0.0)), float(pmf[obs:].sum()) if obs < len(pmf) else 0.0


def fund_by_flag(tr, outdir):
    """Фандинг на сделку (P&L-эффект, bps) по признакам ноги: шорт на Bybit / лонг на Bybit / без Bybit; средний, интервал по активам."""
    import ts_types as TY
    d = TY.derive(dict(tr))
    rows = []
    for un, ui in (("V1", 2), ("V2", 3), ("B3", 1)):
        for hv, hn in ((0, "F"), (1, "W")):
            base = (tr["uni"] == ui) & (tr["half"] == hv) & (tr["mtm"] == 0)
            for nm, msk in (("все", base), ("шорт на Bybit", base & d["byb_short"]), ("лонг на Bybit", base & d["byb_long"]), ("без Bybit", base & (tr["byb"] == 0))):
                idx = np.flatnonzero(msk)
                if len(idx) < 10:
                    continue
                cl = S.cluster_ids(tr["ai"][idx])
                row = dict(uni=un, half=hn, group=nm, n=len(idx), assets=int(cl.max()) + 1, share_nonzero=float((tr["fund"][idx] != 0).mean()), share_neg=float((tr["fund"][idx] < 0).mean()),
                           share_pos=float((tr["fund"][idx] > 0).mean()))
                for col in ("fund", "gross", "net"):
                    pt, lo, hi, se, _ = S.ratio_boot(cl, tr[col][idx], np.ones(len(idx)))
                    row.update({col: float(pt[0]), col + "_lo": float(lo[0]), col + "_hi": float(hi[0])})
                row["hold_mean"] = float(tr["hold"][idx].mean())
                rows.append(row)
    cols = list(rows[0].keys())
    with open(os.path.join(outdir, "F_fund_by_leg.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(cols)
        for r in rows:
            w.writerow([("%.6g" % r[c]) if isinstance(r[c], float) else r[c] for c in cols])
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tables", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    tr, ep, hints, act, meta = S.load_tables(a.tables)
    rows = []
    for hv, hn in ((0, "F"), (1, "W")):
        for gmin in (50.0, 80.0):
            for dmin in (30, 60):
                m = (ep["half"] == hv) & (ep["g_in"] >= gmin)
                ai = ep["ai"][m].astype(int)
                byb = ep["byb"][m] == 1
                ev = ep["D"][m] > dmin
                if ev.sum() == 0:
                    continue
                obs, mean, sd, p = strat(ai, byb, ev)
                rows.append(dict(half=hn, g_min=gmin, D_gt=dmin, episodes=int(m.sum()), long_episodes=int(ev.sum()), long_assets=int(len(np.unique(ai[ev]))), long_on_bybit_pairs=obs,
                                 expected_under_H0=mean, sd=sd, p_strat=p, share_bybit_all=float(byb.mean()), share_bybit_long=float((byb & ev).sum() / ev.sum()),
                                 rate_long_bybit=float((byb & ev).sum() / max(byb.sum(), 1)), rate_long_nobybit=float((~byb & ev).sum() / max((~byb).sum(), 1))))
    cols = list(rows[0].keys())
    with open(os.path.join(a.out, "F_long_episodes.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(cols)
        for r in rows:
            w.writerow([("%.6g" % r[c]) if isinstance(r[c], float) else r[c] for c in cols])
    for r in rows:
        print({k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items()})
    for r in fund_by_flag(tr, a.out):
        print({k: (round(v, 3) if isinstance(v, float) else v) for k, v in r.items()})


if __name__ == "__main__":
    main()
