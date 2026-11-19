#!/usr/bin/env python3
"""TK-622: килзоны пробоев П-42 — сигналы и сделки баз по времени сигнала (описательно, окно 01.07–13.11, 136 сут.).

Вход — `trades_feat.parquet` (TK-617, `tk617_build.py`): L_sig / S_sig (сигнальный режим, первые сигналы уровней, вход sig+4 мс,
путь по ленте aggTrades), L_base / S_base (сделки баз). Время корзин — время сигнала `t_sig` (UTC). Набор корзин (65) и правила
зафиксированы здесь ДО счёта; нового сверх набора не добавляется.

Метрики корзины (сторона отдельно):
  первичные (Холм): touch — касание уровня за 60 мин (`t_touch >= 0`); brk — выход за уровень ≥ 1 NATR за 60 мин (`exc_lvl_60 >= natr_bp`);
                    mo60 — сырой маркаут через 60 мин, бп, среднее; gf — P&L сделки базы, $, среднее на сделку (комиссия движка, без фандинга).
  вторичные (без Холма): mo1, mo5, mo15 — маркаут 1/5/15 мин, бп.
Эффект корзины = среднее в корзине − среднее вне корзины (той же стороны, того же ряда). Статистика — круговой блочный бутстреп по суткам
(блок 4, B повторов, общие индексы для всех тестов): интервал D — перцентильный (при доле 0/100 % в корзине оптимистичен). p — тест со случайным
сдвигом часов: у каждых суток все сигналы сдвигаются по кругу на своё случайное смещение 0…24 ч (суточные эффекты и внутрисуточные
кластеры сохраняются, привязка к часам рвётся), P = 40 000 сдвигов, двусторонний около среднего сдвигов; для схемы «день недели» сдвиг
суток не меняет метку, там p — по z = D / SE_бутстрепа. Холм — по всем первичным тестам.
[Правка 19.11 после первого прогона: бутстреп-p вырождался при доле 100 % в корзине (SE → 0) и давал ложные «значимые» корзины по 8–43
сигнала; первый прогон `run1` отброшен, набор корзин и порог n ≥ 8 не менялись.] Устойчивость по месяцам: знак эффекта в месяце совпал со знаком общего эффекта, учитываются месяцы с ≥ 3
наблюдений в корзине и вне её; «стабильно» = совпало в ≥ 4 из ≥ 4 учтённых месяцев. Медиан по P&L нет.
"""
import argparse
import json
import os
from math import erfc, sqrt

import numpy as np
import pandas as pd

NDAYS = 136
BLOCK = 4
DAY_MS = 86_400_000
MONTHS = ["jul", "aug", "sep", "oct", "nov"]
PRIMARY = ("touch", "brk", "mo60", "gf")
SECONDARY = ("mo1", "mo5", "mo15")
D0 = pd.Timestamp("2026-07-01")
SETS = {"L": ("L_sig", "L_base"), "S": ("S_sig", "S_base")}


def buckets(ts):
    """(схема, метка, маска) по времени сигнала ts (мс UTC)."""
    m = (ts % 3_600_000) / 60_000.0
    u = m % 30
    c8 = (ts % (8 * 3_600_000)) / 60_000.0
    f = np.where(c8 < 240, c8, c8 - 480)
    c24 = (ts % DAY_MS) / 60_000.0
    d = np.where(c24 < 720, c24, c24 - 1440)
    hod = (ts % DAY_MS) / 3_600_000.0
    dow = ((ts // DAY_MS) + 3) % 7
    out = []

    def add(scheme, lab, x, lo, hi):
        out.append((scheme, lab, (x >= lo) & (x < hi)))
    for lo, hi, lab in [(40, 50, "−20…−10"), (50, 55, "−10…−5"), (55, 60, "−5…0"), (0, 5, "0…+5"), (5, 10, "+5…+10"),
                        (10, 20, "+10…+20"), (20, 40, "+20…+40")]:
        add("час (мин до/после начала часа)", lab, m, lo, hi)
    for lo, hi, lab in [(20, 25, "−10…−5"), (25, 30, "−5…0"), (30, 35, "0…+5"), (35, 40, "+5…+10")]:
        add("полчаса :30 (мин до/после)", lab, m, lo, hi)
    for lo, hi, lab in [(0, 5, "0…+5"), (5, 10, "+5…+10"), (10, 20, "+10…+20"), (20, 25, "−10…−5"), (25, 30, "−5…0")]:
        add("полчаса, :00 и :30 вместе (мин в полчасе)", lab, u, lo, hi)
    for lo, hi, lab in [(-60, -30, "−60…−30"), (-30, -10, "−30…−10"), (-10, 0, "−10…0"), (0, 10, "0…+10"), (10, 30, "+10…+30"),
                        (30, 60, "+30…+60")]:
        add("фандинг 00/08/16 UTC (мин)", lab, f, lo, hi)
    for lo, hi, lab in [(-120, -60, "−120…−60"), (-60, -30, "−60…−30"), (-30, -10, "−30…−10"), (-10, 0, "−10…0"), (0, 10, "0…+10"),
                        (10, 30, "+10…+30"), (30, 60, "+30…+60"), (60, 120, "+60…+120")]:
        add("смена суток UTC (мин)", lab, d, lo, hi)
    for lo, hi, lab in [(0, 1, "Азия 00:00–01:00"), (7, 8, "Европа 07:00–08:00"), (13.5, 14.5, "США 13:30–14:30")]:
        add("открытие сессии (UTC)", lab, hod, lo, hi)
    for k, nm in enumerate(["пн", "вт", "ср", "чт", "пт", "сб", "вс"]):
        add("день недели (UTC)", nm, dow, k, k + 1)
    add("день недели (UTC)", "выходные (сб+вс)", dow, 5, 7)
    for h in range(24):
        add("час суток (UTC)", "%02d" % h, hod, h, h + 1)
    return out


def blocks_ix(nrep, rng):
    nb = -(-NDAYS // BLOCK)
    st = rng.integers(0, NDAYS, size=(nrep, nb))
    return ((st[:, :, None] + np.arange(BLOCK)[None, None, :]) % NDAYS).reshape(nrep, -1)[:, :NDAYS]


def dsum(sel, day, x=None):
    return np.bincount(day[sel], weights=None if x is None else x[sel], minlength=NDAYS)[:NDAYS].astype(float)


def effect(mask, v, valid, day, mon, ix, dperm=None):
    """Эффект корзины: среднее внутри − вне; интервал, p, месячные эффекты."""
    a, o = mask & valid, (~mask) & valid
    ni, no = int(a.sum()), int(o.sum())
    r = {"n_in": ni, "n_out": no}
    if ni < 8 or no < 8:
        return r
    M = np.stack([dsum(a, day, v), dsum(a, day), dsum(o, day, v), dsum(o, day)], 1)
    G = M[ix].sum(1)
    ok = (G[:, 1] > 0) & (G[:, 3] > 0)
    Ds = G[ok, 0] / G[ok, 1] - G[ok, 2] / G[ok, 3]
    mi, mo = float(v[a].mean()), float(v[o].mean())
    D = mi - mo
    se = float(Ds.std(ddof=1))
    z = D / se if se > 0 else 0.0
    p = erfc(abs(z) / sqrt(2))
    if dperm is not None:
        dp = dperm[~np.isnan(dperm)]
        c = float(dp.mean())
        p = (1 + int((np.abs(dp - c) >= abs(D - c) - 1e-12).sum())) / (1 + len(dp))
    ms = []
    for k in range(5):
        ak, ok_ = a & (mon == k), o & (mon == k)
        ms.append(float(v[ak].mean() - v[ok_].mean()) if ak.sum() >= 3 and ok_.sum() >= 3 else np.nan)
    el = [x for x in ms if x == x]
    agree = sum(1 for x in el if np.sign(x) == np.sign(D))
    r.update(mean_in=mi, mean_out=mo, D=D, lo=float(np.percentile(Ds, 2.5)), hi=float(np.percentile(Ds, 97.5)), se=se, z=z,
             p=p, sum_in=float(v[a].sum()), months=ms, m_elig=len(el), m_agree=agree,
             stable=bool(len(el) >= 4 and agree >= 4))
    return r


def drop2(mask, v, valid, key, D_sign):
    """Эффект после снятия 2 лучших единиц key (сутки или монета) среди наблюдений корзины — жадно, по уменьшению знакового эффекта."""
    a, o = mask & valid, (~mask) & valid
    drop = np.zeros(len(v), bool)
    for _ in range(2):
        best, bk = None, None
        for k in np.unique(key[a & ~drop]):
            dk = drop | (key == k)
            aa, oo = a & ~dk, o & ~dk
            if aa.sum() < 3 or oo.sum() < 3:
                continue
            e = D_sign * (v[aa].mean() - v[oo].mean())
            if best is None or e < best:
                best, bk = e, k
        if bk is None:
            return np.nan
        drop |= key == bk
    aa, oo = a & ~drop, o & ~drop
    return float(v[aa].mean() - v[oo].mean()) if aa.sum() >= 3 and oo.sum() >= 3 else np.nan


def perm_dist(df, vals, nperm, rng, pb=500):
    """Распределение эффекта корзин при случайном сдвиге часов по суткам: {(схема, метка, метрика): массив nperm}."""
    ts = df["t_sig"].values.astype(np.int64)
    day = df["abs_day"].values.astype(int)
    tod = ts % DAY_MS
    dstart = ts - tod
    tot = {m: (float((v * valid).sum()), float(valid.sum())) for m, (v, valid) in vals.items()}
    out = {}
    for b0 in range(0, nperm, pb):
        off = rng.integers(0, DAY_MS, size=(pb, NDAYS))
        tsh = dstart[None, :] + (tod[None, :] + off[:, day]) % DAY_MS
        for scheme, lab, mk in buckets(tsh):
            if scheme.startswith("день недели"):
                continue
            mf = mk.astype(float)
            for met, (v, valid) in vals.items():
                n_in = mf @ valid.astype(float)
                s_in = mf @ (v * valid)
                St, Nt = tot[met]
                with np.errstate(divide="ignore", invalid="ignore"):
                    D = s_in / n_in - (St - s_in) / (Nt - n_in)
                D[(n_in < 1) | (Nt - n_in < 1)] = np.nan
                out.setdefault((scheme, lab, met), np.empty(nperm))[b0:b0 + pb] = D
    return out


def holm(ps):
    ps = np.asarray(ps, float)
    order = np.argsort(ps)
    m = len(ps)
    adj = np.empty(m)
    run = 0.0
    for rank, i in enumerate(order):
        run = max(run, min(1.0, (m - rank) * ps[i]))
        adj[i] = run
    return adj


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--feat", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--reps", type=int, default=20000)
    ap.add_argument("--seed", type=int, default=622)
    ap.add_argument("--perms", type=int, default=40000)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    T = pd.read_parquet(a.feat)
    assert T["abs_day"].between(0, NDAYS - 1).all(), "в данных есть сутки вне 01.07–13.11"
    ix = blocks_ix(a.reps, np.random.default_rng(a.seed))
    rows = []
    zones = {}
    for side, (sg, bs) in SETS.items():
        S = T[T["set"] == sg].reset_index(drop=True)
        Bt = T[T["set"] == bs].reset_index(drop=True)
        zones[side] = {}
        for nm, df in (("sig", S), ("base", Bt)):
            ts = df["t_sig"].values.astype(np.int64)
            day = df["abs_day"].values.astype(int)
            mon = (D0 + pd.to_timedelta(day, unit="D")).month.values - 7
            vals = {}
            if nm == "sig":
                vals["touch"] = ((df["t_touch"].values >= 0).astype(float), np.ones(len(df), bool))
                vals["brk"] = ((df["exc_lvl_60"].values >= df["natr_bp"].values).astype(float), df["exc_lvl_60"].notna().values)
                for c in (1, 5, 15, 60):
                    x = df["mo_%d" % c].values.astype(float)
                    vals["mo%d" % c] = (np.nan_to_num(x), ~np.isnan(x))
            else:
                vals["gf"] = (df["gf"].values.astype(float), np.ones(len(df), bool))
            PD = perm_dist(df, vals, a.perms, np.random.default_rng(a.seed + 1 + 10 * len(zones) + len(zones[side])))
            for scheme, lab, mask in buckets(ts):
                for met, (v, valid) in vals.items():
                    r = effect(mask, v, valid, day, mon, ix, PD.get((scheme, lab, met)))
                    r.update(side=side, scheme=scheme, bucket=lab, metric=met, primary=met in PRIMARY)
                    if "D" in r and r["primary"] and r["stable"] and r["p"] < 0.05:
                        sg_ = np.sign(r["D"])
                        r["D_drop2days"] = drop2(mask, v, valid, day, sg_)
                        r["D_drop2coins"] = drop2(mask, v, valid, df["sym"].values, sg_)
                    rows.append(r)
            zones[side][nm] = len(df)
    R = pd.DataFrame(rows)
    P = R[R["primary"] & R["p"].notna()].copy()
    P["p_holm"] = holm(P["p"].values)
    R = R.merge(P[["side", "scheme", "bucket", "metric", "p_holm"]], on=["side", "scheme", "bucket", "metric"], how="left")
    R["months"] = R["months"].apply(lambda x: json.dumps([None if y != y else round(y, 3) for y in x]) if isinstance(x, list) else None)
    R.to_csv(a.out + "/results.csv", index=False)
    info = {"tests_primary": int(len(P)), "nominal_p05": int((P["p"] < 0.05).sum()), "nominal_p05_stable": int(((P["p"] < 0.05) & P["stable"].astype(bool)).sum()),
            "holm_lt_05": int((P["p_holm"] < 0.05).sum()), "buckets": int(R.groupby(["scheme", "bucket"]).ngroups), "reps": a.reps, "perms": a.perms, "n": zones}
    json.dump(info, open(a.out + "/info.json", "w"), ensure_ascii=False, indent=1)
    print(json.dumps(info, ensure_ascii=False))


if __name__ == "__main__":
    main()
