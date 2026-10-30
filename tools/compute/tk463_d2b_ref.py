#!/usr/bin/env python3
"""TK-463: эталон Python для режимов D2b ядра tk463-sim (P-TK461 §12.2: SS, TW-свинг, БУ, ОТ1/3/4/5) через tk461_sim.trade(swing=,bar_end=,ot=).
python tk463_d2b_ref.py mk-in <events.csv.gz> <out.csv> [max]   — входы: ev,dir,side,t0,tt0,atr_bps + СИНТЕТИЧЕСКИЕ ss_bps (1,7·ATR, каждое 5-е пусто) и ot1_bps (0,9·ATR); проверка механики, не данные Исследователя
python tk463_d2b_ref.py cells <out.csv>                         — набор ячеек гейта (stop,m,take,par,be_r,ot,otp)
python tk463_d2b_ref.py ref <минуты.csv.gz> <tf> <входы.csv> <funding_cache> <ячейки.csv> <out.csv.gz>
Свинг в пересчёте на минуты и ОТ — независимо от Rust (numpy/pandas, searchsorted по подтверждению; бар ОТ — pandas groupby)."""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk461_sim as S  # noqa: E402
from tk461_scan import MK, TK, N  # noqa: E402
from sancho_l3_trades import Coin  # noqa: E402
from sancho_l3_costs import funding_bps  # noqa: E402
from sancho import swings  # noqa: E402

CELLS = [("SS", "", "TN", 0.0, "", "", ""), ("SS", "", "TP", 2.0, 0.5, "", ""), ("SF1", 1.0, "TW", "", "", "", ""),
         ("SF1", 1.0, "TN", 0.0, "", "OT1", 0.2), ("SF1", 1.0, "TN", 0.0, "", "OT3", 3), ("SF1", 1.0, "TR", 1.0, 0.5, "OT4", 2),
         ("SF2", 2.0, "TP", 2.0, "", "OT5", 0.5), ("ST", 0.0, "TW", "", "", "", ""), ("ST", 0.0, "TN", 0.0, "", "OT3", 2),
         ("SS", "", "TW", "", 0.5, "OT1", 0.5), ("SF0.5", 0.5, "PT", 1.0, "", "OT3", 2), ("SS", "", "TN", 0.0, "", "OT5", 0.25)]


def tables(cn, d):
    """бары ТФ по ориентации d: o,h,l,c; ok; свинг-low по minute-массиву (последний подтверждённый на конец минуты)."""
    w = cn.tf * 60000
    o, h, l, c = cn.px[d]
    g = cn.tm // w
    df = pd.DataFrame(dict(g=g, o=o, h=h, l=l, n=1)).groupby("g").agg(o=("o", "first"), h=("h", "max"), l=("l", "min"), n=("n", "sum"))
    full = df.reindex(range(int(g[0]), int(g[-1]) + 1))
    ok = (full.n.fillna(0) == cn.tf).to_numpy()
    bl = full.l.to_numpy(float)
    idx = swings(-bl, ok, 2)
    p = (g - g[0]).astype(int)
    cb = np.where((cn.tm + 60000) % w == 0, p, p - 1)
    k = np.searchsorted(idx + 2, cb, side="right") - 1
    sw = np.full(len(cb), np.nan)
    m = k >= 0
    sw[m] = bl[idx[k[m]]]
    return p, full.o.to_numpy(float), full.h.to_numpy(float), bl, sw


def one(cn, T, s, d, cell, ft):
    stk, m, tk, par, be_r, ot, otp = cell
    if (stk == "SS" and not np.isfinite(s["ss_bps"])) or (ot == "OT1" and not np.isfinite(s["ot1_bps"])):
        return None
    tf = cn.tf
    o, h, l, c = cn.px[d]
    p, bo, bh, bl, sw = T[d]
    e = int(np.searchsorted(cn.tm, s["tt0"] + tf * 60000))
    last = int(np.searchsorted(cn.tm, s["tt0"] + (N + 1) * tf * 60000)) - 1
    base = dict(sym=cn.sym, tf=tf, ev=s["ev"], dir=s["dir"], t0=s["tt0"], side=d, stop=stk, take=tk,
                par=par if par != "" else np.nan, be_r=be_r if be_r != "" else np.nan, ot=ot if ot else np.nan,
                otp=otp if otp != "" else np.nan)
    if e > last:
        return dict(**base, reason="nofill")
    ep = o[e]
    atr = s["atr_bps"]
    kw, stop, risk = {}, -np.inf, np.nan
    if stk != "ST":
        dist = (s["ss_bps"] / 1e4 * abs(ep)) if stk == "SS" else (m * atr / 1e4 * abs(ep))
        stop, risk = ep - dist, dist
        if tk == "TP":
            kw["tgt"] = ep + par * risk
        elif tk == "TR":
            kw["trail"] = par * atr / 1e4 * abs(ep)
        elif tk == "PT":
            kw["part"] = (ep + risk, 0.5, 1.0 * atr / 1e4 * abs(ep))
        if be_r != "":
            kw["be"] = ep + be_r * risk
    if tk == "TW":
        kw["swing"] = sw
    otf = None
    if ot:
        atr_px = atr / 1e4 * abs(ep)
        lvl = ep - (s["ot1_bps"] + (otp or 0) * atr) * 1e-4 * abs(ep) if ot == "OT1" else None

        def otf(mm, st):
            b = p[mm]
            if ot == "OT1":
                return bo[b] < lvl and st["c"] < lvl
            if ot == "OT3":
                return st["since"] >= otp
            if ot == "OT4":
                return st["c"] < bo[b] and (bh[b] - bl[b]) / atr_px >= otp
            return st["mfe"] >= ep + otp * risk and st["c"] <= ep
    w = tf * 60000
    bar_end = [mm for mm in range(e, last + 1) if (cn.tm[mm] + 60000) % w == 0] if ot else ()
    legs, mx, reason, mfe = S.trade(o, h, l, c, e, last, ep, stop, bar_end=bar_end, ot=otf, **kw)
    g = S.gross_bps(legs, ep)
    fund = funding_bps(cn.ft, cn.fr, int(cn.tm[e]), int(cn.tm[mx]) + 60000, d)
    rb = np.nan if stk == "ST" else (s["ss_bps"] if stk == "SS" else m * atr)
    return dict(**base, reason=reason, gross=g, fee=S.fee_bps(legs, False, MK, TK), fee_mk=MK + sum(f * MK for f, _, _ in legs),
                fee_tk=TK + sum(f * TK for f, _, _ in legs), fund=fund, bars=(mx - e + 1) / tf, mfe_bps=1e4 * (mfe - ep) / abs(ep), risk_bps=rb)


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "mk-in":
        ev = pd.read_csv(sys.argv[2])
        if len(sys.argv) > 4:
            ev = ev.head(int(sys.argv[4]))
        ev = ev[["ev", "dir", "side", "t0", "tt0", "atr_bps"]].copy()
        ev["ss_bps"] = 1.7 * ev.atr_bps
        ev.loc[ev.index[::5], "ss_bps"] = np.nan
        ev["ot1_bps"] = 0.9 * ev.atr_bps
        ev.to_csv(sys.argv[3], index=False)
    elif cmd == "cells":
        pd.DataFrame(CELLS, columns=["stop", "m", "take", "par", "be_r", "ot", "otp"]).to_csv(sys.argv[2], index=False)
    elif cmd == "ref":
        cn = Coin(sys.argv[2], int(sys.argv[3]), 2, sys.argv[5])
        cells = [tuple("" if (isinstance(x, float) and np.isnan(x)) else x for x in r)
                 for r in pd.read_csv(sys.argv[6], keep_default_na=True).itertuples(index=False)]
        T = {}
        for d in (1, -1):
            p, bo, bh, bl, sw = tables(cn, d)
            T[d] = (p, bo, bh, bl, sw)
        ev = pd.read_csv(sys.argv[4])
        rows = [r for s in ev.to_dict("records") for cell in cells if (r := one(cn, T, s, int(s["side"]), cell, None)) is not None]
        pd.DataFrame(rows).to_csv(sys.argv[7], index=False, compression="gzip" if sys.argv[7].endswith(".gz") else None)
    else:
        sys.exit(__doc__)
