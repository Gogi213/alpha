#!/usr/bin/env python3
"""TK-461 / P-TK461 §3–§6, шаг D2a: база выходов (стоп SF m·ATR × тейк TN/TP/TS/TR-ATR/PT + ST) на реальных событиях Санчо, вход E0.
python tk461_scan.py <csv.gz минуты> <tf> <events.csv.gz> <funding_cache> <out.csv.gz> [--max-events N]
Эталонный путь: одна сделка = tk461_sim.trade (проверен якорем с TK-455). Тариф В-63 (1,26/3,15 bps по ногам), фандинг по факту.
Строка = (событие × ячейка): gross, fee (смесь/все мейкер/все тейкер), fund, причина, баров. Не входят (след. шаги): SS, TR-свинг, БУ, ОТ, E1, C-X."""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk461_sim as S  # noqa: E402
from sancho_l3_trades import Coin  # noqa: E402
from sancho_l3_costs import funding_bps  # noqa: E402

MK, TK, N = 1.26, 3.15, 10
MS = (0.5, 1.0, 2.0)
CELLS = [("ST", 0.0, "TN", 0.0)] + [(f"SF{m}", m, t, r) for m in MS for t, r in
         [("TN", 0.0)] + [("TP", r) for r in MS] + [("TS", 0.0)] + [("TR", c) for c in MS] + [("PT", 1.0)]]


def one(cn, s, d, cell):
    """возврат dict строки либо причина пропуска (str)."""
    tf = cn.tf
    o, h, l, c = cn.px[d]
    e = int(np.searchsorted(cn.tm, s["tt0"] + tf * 60000))
    last = int(np.searchsorted(cn.tm, s["tt0"] + (N + 1) * tf * 60000)) - 1
    if e > last:
        return "nofill"
    ep = o[e]
    stk, m, tk, r = cell
    kw = {}
    if stk == "ST":
        stop = -np.inf
    else:
        dist = m * s["atr_bps"] / 1e4 * abs(ep)
        stop = ep - dist
        risk = dist
        if tk == "TP":
            kw["tgt"] = ep + r * risk
        elif tk == "TS":
            tp = cn.target_s(d, s["t0"], abs(ep))
            if tp is None or not d * tp > ep:
                return "no_target"
            kw["tgt"] = d * tp
        elif tk == "TR":
            kw["trail"] = r * s["atr_bps"] / 1e4 * abs(ep)
        elif tk == "PT":
            kw["part"] = (ep + risk, 0.5, 1.0 * s["atr_bps"] / 1e4 * abs(ep))
    bar_end = ()
    legs, mx, reason, mfe = S.trade(o, h, l, c, e, last, ep, stop, bar_end=bar_end, **kw)
    g = S.gross_bps(legs, ep)
    fee = S.fee_bps(legs, False, MK, TK)
    fee_mk = MK + sum(f * MK for f, _, _ in legs)
    fee_tk = TK + sum(f * TK for f, _, _ in legs)
    fund = funding_bps(cn.ft, cn.fr, int(cn.tm[e]), int(cn.tm[mx]) + 60000, d)
    return dict(reason=reason, gross=g, fee=fee, fee_mk=fee_mk, fee_tk=fee_tk, fund=fund, bars=(mx - e + 1) / tf,
                mfe_bps=1e4 * (mfe - ep) / abs(ep), risk_bps=(m * s["atr_bps"]) if stk != "ST" else np.nan)


def run(path, tf, evp, fc, maxev=None):
    cn = Coin(path, tf, 2, fc)
    ev = pd.read_csv(evp)
    if maxev:
        ev = ev.head(maxev)
    rows = []
    for s in ev.to_dict("records"):
        d = int(s["side"])
        for cell in CELLS:
            r = one(cn, s, d, cell)
            base = dict(sym=cn.sym, tf=tf, ev=s["ev"], dir=s["dir"], t0=s["tt0"], side=d, stop=cell[0], take=cell[2], par=cell[3])
            rows.append(dict(**base, reason=r) if isinstance(r, str) else dict(**base, **r))
    return pd.DataFrame(rows)


if __name__ == "__main__":
    a = [x for x in sys.argv[1:] if not x.startswith("--")]
    mx = int(sys.argv[sys.argv.index("--max-events") + 1]) if "--max-events" in sys.argv else None
    if mx:
        a = [x for x in a if x != str(mx)]
    df = run(a[0], int(a[1]), a[2], a[3], mx)
    df.to_csv(a[4], index=False, compression="gzip")
    print(a[4], len(df))
