#!/usr/bin/env python3
"""TK-421 L4 v8 (P-TK421 v8, решения Судьи 27.10 23:42, 28.10): «не рынок» по периодам BTC/ETH + доля монет.
Использование: tk421-l4v8.py <корень свечей> <events.csv (b2bv2)> <выход-каталог> [месяц] [art.json] [процессов] [B]
По рынку × знак × T: минуты, где z лидера (BTC или ETH той же биржи) >= z*_T лидера (K1 по распределению z лидера, бутстрап
по суткам) И доля монет рынка с ходом в ту же сторону за T >= θ_T (K1 по доле в минуты с ходом лидера, бутстрап по суткам).
Ход монеты «в ту же сторону» = знак close[t] − close[t−T] (бар точно T мин назад; нет бара — монета в минуте не считается).
Нет излома/хвоста → периодов нет. Период: склейка минут с разрывом <= T. Событие снимается, если [t_start, t_peak] пересекает
период своего знака на своей бирже (любой T). Сверка: L4 v6 (BTC) из csv. K1: argmax |вторая разность log S|, 200 точек
равных шагов log x, шаг 3 (как tk421-b1-thresh.py); x_max — последняя точка, где >= 30 минут выше (одна монета: кластеров
монета-день нет)."""
import csv
import gzip
import importlib.util
import json
import math
import os
import sys
from collections import defaultdict
from multiprocessing import Pool

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sp = importlib.util.spec_from_file_location("mv", os.path.join(HERE, "tk421-moves.py"))
mv = importlib.util.module_from_spec(sp)
sp.loader.exec_module(mv)
TS, MIN, DAY = mv.TS, mv.MIN, mv.DAY
LEAD = {"bybit-linear": ("BTCUSDT", "ETHUSDT"), "bybit-spot": ("BTCUSDT", "ETHUSDT"),
        "gate-futures_usdt": ("BTC_USDT", "ETH_USDT"), "gate-spot": ("BTC_USDT", "ETH_USDT")}
G, STEP, MINC = 200, 3, 30


def k1x(vals, B, rng, day_of, nd, lo_q=0.1):
    """vals: значения по минутам (>0), day_of: сутки каждого значения. K1 по выживанию; бутстрап по суткам."""
    v = np.sort(vals[vals > 0])
    if len(v) < 2 * MINC:
        return None
    xmax = float(v[-MINC])
    xlo = float(v[int(len(v) * (1 - lo_q))])
    if not xmax > xlo * 1.05:
        return {"нет_хвоста": True, "x_lo": xlo, "x_max": xmax}
    xg = np.linspace(math.log(xlo), math.log(xmax), G)

    def one(vs):
        vs = np.sort(vs)
        s = 1 - np.searchsorted(vs, np.exp(xg), side="left") / max(1, len(vs))
        ls = np.log(np.maximum(s, 1e-300))
        d2 = ls[2 * STEP:] - 2 * ls[STEP:-STEP] + ls[:-2 * STEP]
        j = int(np.argmax(np.abs(d2))) + STEP
        return math.exp(xg[j]), float(d2[j - STEP])
    x, d2 = one(v)
    out = {"x_star": x, "d2": d2, "x_lo": xlo, "x_max": xmax, "минут_выше": int((v >= x).sum()), "минут": len(v)}
    msk = vals > 0
    days = [vals[msk & (day_of == d)] for d in range(nd)]
    bs = []
    for _ in range(B):
        pick = rng.integers(0, nd, nd)
        arr = np.concatenate([days[d] for d in pick])
        if len(arr) >= 2 * MINC:
            bs.append(one(arr)[0])
    if bs:
        out["ci95"] = [float(np.percentile(bs, 2.5)), float(np.percentile(bs, 97.5))]
    return out


def coin_counts(arg):
    """доля монет: по инструменту — векторы (up, dn, den) по минутам месяца для каждого T."""
    d, sym, t0, t1 = arg
    t, h, l, c = mv.load(os.path.join(d, sym + ".csv.gz"), t0, t1)
    n = (t1 - t0) // MIN
    if len(t) < 100:
        return None
    t = np.array(t, dtype=np.int64)
    c = np.array(c)
    res = {}
    for T in TS:
        prev = t - T * MIN
        j = np.searchsorted(t, prev)
        ok = (j < len(t)) & (t[np.minimum(j, len(t) - 1)] == prev)
        idx = ((t - t0) // MIN)[ok]
        cp = c[np.minimum(j, len(t) - 1)][ok]
        cc = c[ok]
        up = np.zeros(n, dtype=np.int16); dn = np.zeros(n, dtype=np.int16); den = np.zeros(n, dtype=np.int16)
        up[idx[cc > cp]] = 1; dn[idx[cc < cp]] = 1; den[idx] = 1
        res[T] = (up, dn, den)
    return res


def lead_z(root, sub, sym, t0, t1):
    """z_T(лидер) и a_T по минутам сетки месяца, обе стороны."""
    t, h, l, c = mv.load(os.path.join(root, sub, sym + ".csv.gz"), t0, t1)
    n = (t1 - t0) // MIN
    ix = np.array([(x - t0) // MIN for x in t], dtype=np.int64)
    out = {}
    for T in TS:
        sg = mv.sigma(t, c, T, t0)
        for up, s in ((True, "+"), (False, "-")):
            a = np.array(mv.moves(t, h, l, T, up))
            z = np.full(n, np.nan)
            if sg > 0:
                z[ix] = a / sg
            out[(T, s)] = z
    return out


def periods(mask, T):
    """склейка минут маски с разрывом <= T минут; список (i0, i1) индексов минут."""
    ix = np.flatnonzero(mask)
    out = []
    for i in ix:
        if out and i - out[-1][1] <= T:
            out[-1][1] = int(i)
        else:
            out.append([int(i), int(i)])
    return out


def main():
    root, evf, outd = sys.argv[1:4]
    ym = sys.argv[4] if len(sys.argv) > 4 else "2026-09"
    if len(sys.argv) > 5 and sys.argv[5] != "-":
        mv.load_excl(sys.argv[5])
    nproc = int(sys.argv[6]) if len(sys.argv) > 6 else 15
    B = int(sys.argv[7]) if len(sys.argv) > 7 else 1000
    t0, t1 = mv.month_bounds(ym)
    n = (t1 - t0) // MIN
    nd = (t1 - t0) // DAY
    day_of = np.arange(n) // 1440
    os.makedirs(outd, exist_ok=True)
    rng = np.random.default_rng(4218)
    rows = list(csv.DictReader(open(evf, encoding="utf-8")))
    rep = {"месяц": ym, "B": B, "рынки": {}}
    PER = {}
    for ex, mk, sub in mv.MKT:
        d = os.path.join(root, sub)
        syms = sorted(f[:-7] for f in os.listdir(d) if f.endswith(".csv.gz"))
        agg = {T: [np.zeros(n, dtype=np.int32) for _ in range(3)] for T in TS}
        with Pool(nproc) as pool:
            for r in pool.imap_unordered(coin_counts, [(d, s, t0, t1) for s in syms], chunksize=16):
                if r:
                    for T in TS:
                        for k in range(3):
                            agg[T][k] += r[T][k]
        leaders = [lead_z(root, sub, s, t0, t1) for s in LEAD[sub]]
        R = {}
        for T in TS:
            for s in "+-":
                share = agg[T][0 if s == "+" else 1] / np.maximum(agg[T][2], 1)
                # лидер: любой из двух; порог z* у каждого свой (K1 по его z), пересечение «или»
                hit = np.zeros(n, dtype=bool)
                info = {"z*": []}
                for name, lz in zip(LEAD[sub], leaders):
                    z = lz[(T, s)]
                    ok = ~np.isnan(z)
                    kz = k1x(z[ok], B, rng, day_of[ok], nd)
                    info["z*"].append({"лидер": name, "k1": kz})
                    if kz and "x_star" in kz:
                        hit |= ok & (np.nan_to_num(z) >= kz["x_star"])
                m_ok = hit & (agg[T][2] > 0)
                kt = k1x(share[m_ok], B, rng, day_of[m_ok], nd) if m_ok.sum() else None
                info["θ_доли"] = kt
                info["минут_хода_лидера"] = int(m_ok.sum())
                if kt and "x_star" in kt:
                    mask = m_ok & (share >= kt["x_star"])
                    per = periods(mask, T)
                    info["периодов"] = len(per)
                    PER[(sub, s, T)] = per
                    info["период_список"] = [[t0 + a * MIN, t0 + b * MIN] for a, b in per]
                else:
                    PER[(sub, s, T)] = []
                    info["периодов"] = 0
                R["%d%s" % (T, s)] = info
                print(sub, T, s, info["минут_хода_лидера"], info["периодов"], flush=True)
        rep["рынки"][sub] = R
    # события
    sub_of = {(ex, mk): sub for ex, mk, sub in mv.MKT}
    removed = defaultdict(lambda: defaultdict(int))
    cnt = defaultdict(lambda: [0, 0, 0])  # до, после v8, после v6
    for r in rows:
        sub = sub_of[(r["биржа"], r["рынок"])]
        a, b = int(r["t_start_ms"]), int(r["t_peak_ms"])
        hits = [(T, p) for T in TS for p in PER[(sub, r["знак"], T)] if t0 + p[0] * MIN <= b and t0 + p[1] * MIN >= a]
        r["L4_v8"] = "рынок" if hits else "ok"
        r["L4_v8_T"] = ",".join(sorted({str(T) for T, _ in hits}))
        for T, p in hits:
            removed[(sub, r["знак"], T)][(p[0], p[1])] += 1
        c = cnt[sub]
        c[0] += 1; c[1] += (not hits); c[2] += (r["L4"] == "ok")
    rep["события_до/после_v8/после_v6"] = {k: v for k, v in cnt.items()}
    rep["снято_периодом"] = {"%s|%s|%d|%d-%d" % (k[0], k[1], k[2], a, b): v for k, d_ in removed.items() for (a, b), v in d_.items()}
    json.dump(rep, open(os.path.join(outd, "l4v8-%s.json" % ym), "w"), ensure_ascii=False, indent=1, default=str)
    cols = list(rows[0].keys())
    with open(os.path.join(outd, "pump-events-l4v8-%s.csv" % ym), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    print(json.dumps(rep["события_до/после_v8/после_v6"], ensure_ascii=False))


if __name__ == "__main__":
    main()
