#!/usr/bin/env python3
"""TK-447 п.6: фандинг Bybit (авг, сен), lsr_taker и ликвидации Gate contract_stats (только сен) — колонки к кубу tk447-features.
extra(sy, lo, ts, q, ym, cut_ms=None) -> dict имя -> [n,len(ts)]. Только строки с t_ms <= t (lsr/ликвидации — <= t−5 мин: t_ms = начало корзины, закрытие неизвестно).
cut_ms — тест заглядывания: строки с t_ms > cut_ms отбрасываются, признаки при t <= cut обязаны совпасть."""
import gzip
import os

import numpy as np

FUND = {"2026-08": "/srv/alpha-sda/tk425/aug-oi/bybit/funding", "2026-09": "/srv/alpha-sda/tk415/out/bybit/funding"}
GATE = {"2026-09": "/srv/alpha-sda/tk415/out/gate/oi"}


def rd(path, cols, cut):
    if not os.path.exists(path):
        return None
    with gzip.open(path, "rt") as f:
        hd = f.readline().strip().split(",")
        ix = [hd.index(c) for c in cols]
        rows = [[float(r.split(",")[i]) if r.split(",")[i] != "" else np.nan for i in ix] for r in f]
    if not rows:
        return None
    a = np.array(rows)
    if cut is not None:
        a = a[a[:, 0] <= cut]
    return a if len(a) else None


def extra(sy, lo, ts, q, ym, cut_ms=None):
    n, m = len(sy), len(ts)
    tms = (lo + ts).astype(np.int64) * 60000
    nan = lambda: np.full((n, m), np.nan)
    F = {k: nan() for k in ("fund_last", "fund_abs", "fund_chg", "lsr_taker", "lsr_taker_chg1h", "liq_usd1h_log", "liq_imb1h", "liq_to_turn1h")}
    cq = np.cumsum(np.asarray(q, np.float64), axis=1)
    for k, s in enumerate(sy):
        if ym in FUND:
            a = rd(f"{FUND[ym]}/{s}.csv.gz", ["t_ms", "funding_rate"], cut_ms)
            if a is not None:
                j = np.searchsorted(a[:, 0], tms, "right") - 1
                ok = j >= 1
                last = np.where(j >= 0, a[np.clip(j, 0, None), 1], np.nan)
                prev = np.where(ok, a[np.clip(j - 1, 0, None), 1], np.nan)
                F["fund_last"][k], F["fund_abs"][k], F["fund_chg"][k] = last, np.abs(last), last - prev
        if ym in GATE:
            g = s[:-4] + "_USDT"
            a = rd(f"{GATE[ym]}/{g}.csv.gz", ["t_ms", "lsr_taker", "long_liq_usd", "short_liq_usd"], cut_ms)
            if a is not None:
                tt = tms - 300000  # корзина с t_ms <= t−5 мин закрыта к t
                j = np.searchsorted(a[:, 0], tt, "right") - 1
                jc = np.clip(j, 0, None)
                v = j >= 0
                lsr = np.where(v, a[jc, 1], np.nan)
                F["lsr_taker"][k] = lsr
                j12 = np.clip(j - 12, 0, None)
                with np.errstate(all="ignore"):
                    F["lsr_taker_chg1h"][k] = np.where(j >= 12, np.log(lsr / a[j12, 1]), np.nan)
                liq = np.nan_to_num(a[:, 2]) + np.nan_to_num(a[:, 3])
                cl = np.concatenate([[0.0], np.cumsum(liq)])
                lo_ = np.clip(j - 11, 0, None)
                s1 = np.where(j >= 11, cl[j + 1] - cl[lo_], np.nan)
                F["liq_usd1h_log"][k] = np.log1p(s1)
                lg = np.concatenate([[0.0], np.cumsum(np.nan_to_num(a[:, 2]))])
                lgs = lg[j + 1] - lg[lo_]
                with np.errstate(all="ignore"):
                    F["liq_imb1h"][k] = np.where((j >= 11) & (s1 > 0), (2 * lgs - s1) / s1, np.nan)
                    tcol = np.clip(ts, 61, None)
                    t1 = cq[k, tcol] - cq[k, tcol - 60]
                    F["liq_to_turn1h"][k] = np.where((j >= 11) & (t1 > 0), s1 / t1, np.nan)
    return F


def test(ym, syms, lo, ts, q):
    t0 = ts[len(ts) // 2]
    cut = int((lo + t0)) * 60000
    F0 = extra(syms, lo, ts, q, ym)
    F1 = extra(syms, lo, ts, q, ym, cut_ms=cut)
    keep = ts <= t0
    bad = 0
    for k in F0:
        same = np.array_equal(F0[k][:, keep], F1[k][:, keep], equal_nan=True)
        diff_after = not np.array_equal(F0[k][:, ~keep], F1[k][:, ~keep], equal_nan=True)
        bad += not same
        print("OK  " if same else "DIFF", ym, k, "finite", round(float(np.isfinite(F0[k][:, keep]).mean()), 3), "порча видна после:", diff_after)
    return bad
