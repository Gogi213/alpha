#!/usr/bin/env python3
"""TK-617 v2: путь сделки ДО касания уровня (по ленте aggTrades) — исходные данные для замера «защиты до касания».

Факт, из которого вырос этап (tk617_stops.py): все 37 стопов L_base не касались уровня, у остальных 584 сделок касание было. До касания у движка
(`tick.rs`, `Pos::on_trade`) защиты нет, кроме жёсткого стопа 5 % (`rt` и `tx` взводятся только после касания).

Для каждой сделки рядов L_base / S_base / L_sig / S_sig пишет `pretouch.parquet`:
  tc_<X>, xc_<X> — через сколько мс от входа цена впервые ушла на ≥ X бп против входа ДО касания уровня, и бп (в пользу стороны) на первой сделке ленты
                   со временем ≥ момент + 4 мс (задержка выхода движка), X ∈ XS; NaN — такого пересечения до касания не было;
  tch_<k>, xk_<k> — коснулась ли цена уровня за k минут от входа, и бп на первой сделке ленты ≥ вход + k мин + 4 мс (контрольная точка), k ∈ KS;
  pre_mae, t_pre_mae — худший ход против до касания (бп) и его момент.
Цены ленты — как в build (`tk617_build._path`): бп от цены входа движка (`entry_px_e8`), знак стороны. Окно — до выхода сделки (база) или 60 мин (сигнальные ряды).
Ничего позже 13.11 не читается.
"""
import argparse
import datetime
import os
from multiprocessing import Pool

import numpy as np
import pandas as pd

import tk617_build as B

XS = (25, 50, 75, 100, 150, 200, 300, 400)
KS = (1, 2, 3, 5, 10, 15, 30)
SETS = ("L_base", "S_base", "L_sig", "S_sig")
LAT = 4


def _one(ev, t, p, tin):
    s, p0, lp = ev["s"], ev["p0"], ev["lp"]
    r = {}
    i0 = int(np.searchsorted(t, ev["t_in"], "left"))
    hend = ev["t_out"] if ev["base"] else ev["t_in"] + B.H60
    ie = int(np.searchsorted(t, hend, "right"))
    if ie <= i0:
        return r
    x = s * (p[i0:ie] / p0 - 1.0) * 1e4
    y = s * (p[i0:ie] - lp) / lp * 1e4
    tt = t[i0:ie] - ev["t_in"]
    hit = np.flatnonzero(y >= 0)
    ntouch = int(hit[0]) if len(hit) else len(x)                 # число сделок до касания (касание — на индексе ntouch)
    t_touch = int(tt[hit[0]]) if len(hit) else -1
    r["t_touch_h"] = t_touch
    if ntouch > 0:
        seg = x[:ntouch]
        j = int(seg.argmin())
        r["pre_mae"], r["t_pre_mae"] = float(seg[j]), int(tt[j])
        for X in XS:
            w = np.flatnonzero(seg <= -X)
            if len(w):
                tc = int(tt[w[0]])
                k = int(np.searchsorted(tt, tc + LAT, "left"))
                if k < len(x):
                    r["tc_%d" % X], r["xc_%d" % X] = tc, float(x[k])
    for k in KS:
        tk = k * 60_000
        if len(tt) == 0 or tt[-1] < tk:
            continue
        r["tch_%d" % k] = 1 if (t_touch >= 0 and t_touch <= tk) else 0
        q = int(np.searchsorted(tt, tk + LAT, "left"))
        if q < len(x):
            r["xk_%d" % k] = float(x[q])
    return r


def _sym(args):
    sym, evs = args
    evs = sorted(evs, key=lambda e: e["t_in"])
    cache, out = {}, []
    for e in evs:
        hend = e["t_out"] if e["base"] else e["t_in"] + B.H60
        d0 = datetime.datetime.fromtimestamp(e["t_in"] / 1000, datetime.timezone.utc).date()
        d1 = datetime.datetime.fromtimestamp(hend / 1000, datetime.timezone.utc).date()
        ts, ps = [], []
        for k in range((d1 - d0).days + 1):
            d = d0 + datetime.timedelta(days=k)
            if d not in cache:
                for kk in [kk for kk in cache if kk < d - datetime.timedelta(days=2)]:
                    del cache[kk]
                cache[d] = B._tape_day(sym, d)
            if cache[d] is not None:
                ts.append(cache[d][0])
                ps.append(cache[d][1])
        if not ts:
            out.append((e["i"], {}))
            continue
        t, p = (np.concatenate(ts), np.concatenate(ps)) if len(ts) > 1 else (ts[0], ps[0])
        out.append((e["i"], _one(e, t, p, e["t_in"])))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="/data/p42-tk617")
    ap.add_argument("--procs", type=int, default=3)
    a = ap.parse_args()
    T = pd.read_parquet(os.path.join(a.dir, "trades_tape.parquet"))
    T = T[T["set"].isin(SETS)].reset_index(drop=True)
    by = {}
    for i, r in enumerate(T.itertuples()):
        by.setdefault(r.sym, []).append({"i": i, "s": int(r.s), "p0": r.entry_px_e8 / 1e8, "lp": abs(r.level_px_e8) / 1e8, "t_in": int(r.t_in), "t_out": int(r.t_out),
                                         "base": r.set.endswith("base")})
    jobs = sorted(by.items(), key=lambda kv: -len(kv[1]))
    res = {}
    with Pool(a.procs) as pool:
        for k, lst in enumerate(pool.imap_unordered(_sym, jobs, chunksize=1)):
            for i, r in lst:
                res[i] = r
            if k % 25 == 0:
                print("pretouch", k, len(jobs), flush=True)
    rows = pd.DataFrame([res.get(i, {}) for i in range(len(T))])
    keep = T[["set", "win", "sym", "level", "sig_ms", "t_out", "s", "abs_day", "kind", "gf", "natr_e8", "entry_px_e8"]]
    out = pd.concat([keep.reset_index(drop=True), rows], axis=1)
    out.to_parquet(os.path.join(a.dir, "pretouch.parquet"))
    print("rows", len(out), "cols", len(out.columns))


if __name__ == "__main__":
    main()
