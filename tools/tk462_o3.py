#!/usr/bin/env python3
"""TK-462 шаг 0 ч.4 (P-TK462 §1, §8): выписка O3 (bp G8.1) в bps + фандинг по факту. Логика события/исхода — bp_g8.py (TK-458) без изменений.
python tk462_o3.py <каталог минут> <fund_cache> <out_dir> <SYM>...   (рядом лежат bp_g8.py, sancho_l3_costs.py)
Строка = событие x цель m: coin, side, t_range_ms (как ts в bp-g8-events), t_in_ms, t_out_ms, m, reason(stop|tgt|timeout), E, rho_bps, gross_R, gross_bps, fund_bps, f_chk (= gross_R-2*FEE*E/rho, сверка с f0..f2 кэша)."""
import os, sys, datetime as dt
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bp_g8 as g
from sancho_l3_costs import funding_month, funding_bps


def payoff_x(hh, ll, cc, E, rho, sign):
    out = []
    for m in g.MS:
        if sign < 0: stop = hh >= E + rho; tgt = ll <= E - m * rho
        else: stop = ll <= E - rho; tgt = hh >= E + m * rho
        a = np.flatnonzero(stop | tgt)
        if len(a): out.append((-1.0 if stop[a[0]] else m, int(a[0]), "stop" if stop[a[0]] else "tgt"))
        else: out.append((sign * (cc[-1] - E) / rho, len(cc) - 1, "timeout"))
    return out


def coin(path, fcache):
    sym = os.path.basename(path).split(".")[0]; m0, h, l, c = g.minutes(path)
    bh, bl, bc, atr = g.m5(h, l, c); rows = []; fc = {}
    for day in pd.date_range(pd.Timestamp(m0 * 60, unit="s").date(), pd.Timestamp((m0 + len(h)) * 60, unit="s").date()):
        b = (day - pd.Timedelta(days=1)).date()
        s0 = int(dt.datetime(b.year, b.month, b.day, 19, tzinfo=g.NY).astimezone(g.UTC).timestamp() // 60) - m0
        e = g.event(h, l, c, bh, bl, bc, atr, s0)
        if e is None: continue
        sign, i, E, rho, a, t_cl, t_end = e
        hh, ll, cc = h[t_cl:t_end], l[t_cl:t_end], c[t_cl:t_end]
        if len(hh) < 1 or not g.clean(hh, ll, cc): continue
        t_in = (m0 + t_cl) * 60000
        for mm, (R, k, why) in zip(g.MS, payoff_x(hh, ll, cc, E, rho, sign)):
            t_out = (m0 + t_cl + k + 1) * 60000  # конец минуты выхода
            ym = pd.Timestamp(t_in, unit="ms").strftime("%Y-%m"), pd.Timestamp(t_out, unit="ms").strftime("%Y-%m")
            fr = []
            for y in sorted(set(ym)):
                if (sym, y) not in fc:
                    try: fc[(sym, y)] = funding_month(sym, y, fcache)
                    except Exception: fc[(sym, y)] = None
                if fc[(sym, y)] is not None: fr.append(fc[(sym, y)])
            if len(fr) == len(set(ym)):
                d = pd.concat(fr); fund = funding_bps(d.t.values, d.r.values, t_in, t_out, sign)  # лонг(sign=+1) платит, + = расход
            else: fund = np.nan
            rb = rho / E * 1e4
            rows.append((sym, "short" if sign < 0 else "long", (m0 + s0) * 60000, t_in, t_out, mm, why, E, rb, R, R * rb, fund, R - 2 * g.FEE * E / rho))
    return rows


if __name__ == "__main__":
    src, fcache, out = sys.argv[1:4]
    cols = ["coin", "side", "ts_utc_ms", "t_in_ms", "t_out_ms", "m", "reason", "E", "rho_bps", "gross_R", "gross_bps", "fund_bps", "f_chk"]
    for s in sys.argv[4:]:
        pd.DataFrame(coin(f"{src}/{s}.csv.gz", fcache), columns=cols).to_csv(f"{out}/{s}.csv.gz", index=False)
