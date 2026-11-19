#!/usr/bin/env python3
"""TK-617 v2: повтор сделок L_base по ленте с изменённым моментом входа (замер, не подбор) → `entry_replay.parquet`.

Повтор — `tick.rs::sim_tail / Pos` на numba (лонг), та же логика, что `tk617_trace.py` (14 из 14 сделок совпали с движком). Параметры L_base (TK-599): 8 тейков 1…5 NATR по 50 % позиции,
безубыток после 1-го тейка, трейл +2 NATR / откат 2 NATR, `rt` 0,2 % под уровнем 20 с после касания, жёсткий стоп 5 %, задержка выхода 4 мс, проскальзывание 1 бп, `ew = 5 мс`.

Вариант E(k): вход — не сразу на сигнале, а когда цена поднялась на k бп над ценой входа базы (`p_ref`: последняя сделка покупателя ≤ сигнал + 4 мс, как у движка) — рыночная покупка по
первой сделке ленты ≥ p_ref·(1 + k/1e4); приказ живёт 60 мин и снимается; если эта цена ≥ уровня — сделка пропущена (как `Beyond`). k = 0 — база (проверка: P&L каждой сделки = движок).
Портфельные связи (занятость монеты, паузы) не моделируются: единичные сделки, сумма по сделкам.
"""
import argparse
import datetime
import os
from multiprocessing import Pool

import numpy as np
import pandas as pd
from numba import njit

import zipfile

ROOT = "/data/p42-tk599"
WINDOWS = ("wa", "wb", "wc")

def load_day(sym, day):
    fn = "%s-aggTrades-%s.zip" % (sym, day.isoformat())
    for w in WINDOWS:
        pth = "%s/%s/aggtrades/%s/%s" % (ROOT, w, sym, fn)
        if os.path.exists(pth):
            with zipfile.ZipFile(pth) as z:
                with z.open(z.namelist()[0]) as f:
                    df = pd.read_csv(f, usecols=["price", "transact_time", "is_buyer_maker"], dtype={"price": "float64", "transact_time": "int64"})
            return df["transact_time"].values, df["price"].values, (df["is_buyer_maker"].astype(str).str.lower() == "true").values
    return None


KS = (0, 5, 10, 15, 20, 25)
EXPIRY = 3_600_000
CODES = {0: "stop", 1: "be", 2: "trail", 3: "rt", 4: "forced"}


@njit(cache=True)
def sim_long(t, p, bm, T, natr, lp, k_bp, expiry):
    """(код, entry_ms, exit_ms, gross, fees, takes); код < 0 — сделки нет: −1 нет сделки покупателя ≤ T за 5 мс, −2 цена ≥ уровня, −3 приказ не исполнен."""
    n = len(t)
    hi = np.searchsorted(t, T, side="right")
    pref = -1.0
    j = hi - 1
    while j >= 0:
        if T - t[j] > 5:
            break
        if not bm[j]:
            pref = p[j]
            break
        j -= 1
    if pref < 0:
        return -1, 0, 0, 0.0, 0.0, 0
    if pref > lp:
        return -2, 0, 0, 0.0, 0.0, 0
    if k_bp == 0:
        entry_ms, p0, i0 = T, pref, hi
    else:
        trig = pref * (1.0 + k_bp / 1e4)
        i0 = -1
        i = hi
        while i < n:
            if t[i] > T + expiry:
                break
            if p[i] >= trig:
                if p[i] >= lp:
                    return -2, 0, 0, 0.0, 0.0, 0
                i0 = i
                break
            i += 1
        if i0 < 0:
            return -3, 0, 0, 0.0, 0.0, 0
        entry_ms, p0 = t[i0], p[i0]
        i0 += 1
    e = p0 * (1.0 + 1e-4)
    stop_base = e - e * 0.05
    nat = e * natr
    ntp = 8
    tps = np.empty(ntp)
    for q in range(ntp):
        tps[q] = e + nat * (1000 + (4000 * q) // 7) / 1000.0
    lot = 500.0 * 50 / (100.0 * ntp)
    a_dist, r_dist = nat * 2.0, nat * 2.0
    rt_d = lp * 200 / 100000.0
    rem = 500.0
    gross_legs = 0.0
    fee_legs = 0.0
    next_tp = 0
    be_on = False
    armed = False
    trail = -1e30
    maxp = p0
    touched = False
    below = -1
    pend_due = -1
    pend_kind = -1
    kind, xm, xp = 4, entry_ms, p0
    done = False
    i = i0
    while i < n:
        ms, px = t[i], p[i]
        xm, xp = ms, px
        if pend_due >= 0:
            if ms >= pend_due:
                kind = pend_kind
                done = True
                break
            while next_tp < ntp and px > tps[next_tp]:
                gross_legs += lot * (tps[next_tp] - e) / e
                fee_legs += lot * (tps[next_tp] / e) * 1.26e-4
                rem -= lot
                if next_tp == 0:
                    be_on = True
                    stop_base = max(stop_base, e)
                next_tp += 1
            i += 1
            continue
        tl = trail if armed else -1e30
        sp = max(stop_base, tl)
        k = -1
        if px <= sp:
            if tl > stop_base:
                k = 2
            elif be_on:
                k = 1
            else:
                k = 0
        else:
            while next_tp < ntp and px > tps[next_tp]:
                gross_legs += lot * (tps[next_tp] - e) / e
                fee_legs += lot * (tps[next_tp] / e) * 1.26e-4
                rem -= lot
                if next_tp == 0:
                    be_on = True
                    stop_base = max(stop_base, e)
                next_tp += 1
            if px > maxp:
                maxp = px
            if (not armed) and maxp - e >= a_dist:
                armed = True
            if armed:
                trail = max(trail, maxp - r_dist)
            if (not touched) and px >= lp:
                touched = True
            if touched:
                if px <= lp - rt_d:
                    if below < 0:
                        below = ms
                    if ms - below >= 20000:
                        k = 3
                else:
                    below = -1
        if k >= 0:
            pend_due = ms + 4
            pend_kind = k
        i += 1
    if not done:
        kind = 4
    pxf = xp - xp * 1e-4
    gross_legs += rem * (pxf - e) / e
    fee_legs += rem * (pxf / e) * 3.15e-4 + 500.0 * 3.15e-4
    return kind, entry_ms, xm, gross_legs, fee_legs, next_tp


def _sym(args):
    sym, evs = args
    evs = sorted(evs, key=lambda e: e["t_sig"])
    cache, out = {}, []
    for e in evs:
        d0 = datetime.datetime.fromtimestamp((e["t_sig"] - 60_000) / 1000, datetime.timezone.utc).date()
        d1 = datetime.datetime.fromtimestamp(e["t_out"] / 1000, datetime.timezone.utc).date() + datetime.timedelta(days=2)
        ts, ps, bs = [], [], []
        for k in range((d1 - d0).days + 1):
            d = d0 + datetime.timedelta(days=k)
            if d not in cache:
                for kk in [kk for kk in cache if kk < d - datetime.timedelta(days=3)]:
                    del cache[kk]
                cache[d] = load_day(sym, d)
            if cache[d] is not None:
                ts.append(cache[d][0])
                ps.append(cache[d][1])
                bs.append(cache[d][2])
        if not ts:
            out.append((e["i"], None))
            continue
        t = np.concatenate(ts).astype(np.int64)
        p = np.concatenate(ps).astype(np.float64)
        bm = np.concatenate(bs).astype(np.bool_)
        res = {}
        for k in KS:
            res[k] = sim_long(t, p, bm, e["t_sig"] + 4, e["natr"], e["lp"], k, EXPIRY)
        out.append((e["i"], res))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="/data/p42-tk617")
    ap.add_argument("--procs", type=int, default=3)
    ap.add_argument("--set", default="L_base")
    a = ap.parse_args()
    T = pd.read_parquet(os.path.join(a.dir, "trades_tape.parquet"))
    T = T[T["set"] == a.set].reset_index(drop=True)
    by = {}
    for i, r in enumerate(T.itertuples()):
        by.setdefault(r.sym, []).append({"i": i, "t_sig": int(r.t_sig), "t_out": int(r.t_out), "natr": r.natr_e8 / 1e8, "lp": abs(r.level_px_e8) / 1e8})
    jobs = sorted(by.items(), key=lambda kv: -len(kv[1]))
    res = {}
    with Pool(a.procs) as pool:
        for n, lst in enumerate(pool.imap_unordered(_sym, jobs, chunksize=1)):
            for i, r in lst:
                res[i] = r
            if n % 25 == 0:
                print("replay", n, len(jobs), flush=True)
    rows = []
    for i in range(len(T)):
        r = {"i": i}
        for k in KS:
            v = res.get(i)
            if v is None:
                continue
            code, em, xm, g, f, tk = v[k]
            r.update({"code_%d" % k: code, "em_%d" % k: em, "xm_%d" % k: xm, "gross_%d" % k: g, "fees_%d" % k: f, "takes_%d" % k: tk})
        rows.append(r)
    out = pd.concat([T[["set", "win", "sym", "level", "sig_ms", "t_out", "abs_day", "kind", "takes", "gross", "fees", "gf", "dist_e8", "natr_e8"]].reset_index(drop=True), pd.DataFrame(rows)], axis=1)
    out.to_parquet(os.path.join(a.dir, "entry_replay.parquet"))
    print("rows", len(out))


if __name__ == "__main__":
    main()
