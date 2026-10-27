#!/usr/bin/env python3
"""TK-421 шаг 0 (возврат Судьи 27.10, reviews/tk421-b2b-return): минуты-артефакты данных рынка.
Использование: tk421-art.py <корень свечей> <выход.json> [процессов] [макс. инструментов] [месяц ГГГГ-ММ]
Фитиль инструмента-минуты w = ln(h/l) − |ln(c/o)| (длина теней при неизменном теле). 1) по рынку K1 («излом», как в B1:
равные шаги log x, argmax |вторая разность log S|, шаг 3) на выживании w пулом по инструмент-минутам даёт x*_w;
«фитильный» инструмент-минута: w ≥ x*_w. 2) доля фитильных среди присутствующих инструментов рынка по минутам;
K1 на выживании этой доли по минутам даёт x*_доли. Минута — артефакт, если доля ≥ x*_доли И фитильных ≥ 30
(MIN_CLUST протокола: без этого условия минута с 1–2 торгуемыми контрактами дала бы долю 1, убила бы настоящий памп).
Диапазон сетки: от x(S=0,1) до x_max (последняя точка, где ≥ 30 наблюдений ≥ x). Все 4 рынка — одним правилом."""
import gzip
import importlib.util
import json
import math
import os
import sys
from multiprocessing import Pool
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sp = importlib.util.spec_from_file_location("mv", os.path.join(HERE, "tk421-moves.py"))
mv = importlib.util.module_from_spec(sp)
sp.loader.exec_module(mv)
MIN = mv.MIN
NB, G, STEP, MIN_CLUST = 400, 200, 3, 30
W_LO, W_HI = 1e-4, 12.0
SH_LO, SH_HI = 1e-4, 1.0


def one(arg):
    d, sym, t0, t1 = arg
    idx, w = [], []
    with gzip.open(os.path.join(d, sym + ".csv.gz"), "rt") as f:
        f.readline()
        for line in f:
            p = line.split(",", 5)
            ti = int(p[0])
            if ti < t0 or ti >= t1:
                continue
            o, hi, lo, c = float(p[1]), float(p[2]), float(p[3]), float(p[4])
            if c <= 0 or lo <= 0 or o <= 0 or hi <= 0:
                continue
            idx.append((ti - t0) // MIN)
            w.append(max(0.0, math.log(hi / lo) - abs(math.log(c / o))))
    return np.array(idx, dtype=np.int32), np.array(w, dtype=np.float32)


def edges(lo, hi):
    return np.exp(math.log(lo) + (np.arange(1, NB + 1) - 1) * (math.log(hi) - math.log(lo)) / NB)


def knee(x_obs, lo, hi, x_max=None):
    """x_obs: наблюдения; K1 по выживанию на сетке edges(lo,hi). x_max — верх сетки (по умолчанию 30-е с конца наблюдение).
    Возврат x*, x_lo, x_max."""
    xs = edges(lo, hi)
    n = len(x_obs)
    srt = np.sort(x_obs)
    S = 1.0 - np.searchsorted(srt, xs, side="left") / n  # доля >= x
    pos = srt[srt > 0]
    if len(pos) < MIN_CLUST:
        return None
    xmax = float(pos[-MIN_CLUST]) if x_max is None else float(x_max)
    i01 = int(np.searchsorted(-S, -0.1))
    xlo = float(xs[min(i01, NB - 1)])
    if not xmax > xlo * 1.05:
        return None
    xg = np.linspace(math.log(xlo), math.log(xmax), G)
    ls = np.log(np.maximum(S, 1e-300))
    lsg = np.interp(xg, np.log(xs), ls)
    d2 = lsg[2 * STEP:] - 2 * lsg[STEP:-STEP] + lsg[:-2 * STEP]
    j = int(np.argmax(np.abs(d2))) + STEP
    return float(math.exp(xg[j])), xlo, xmax


def main():
    root, outp = sys.argv[1], sys.argv[2]
    procs = int(sys.argv[3]) if len(sys.argv) > 3 else 15
    lim = int(sys.argv[4]) if len(sys.argv) > 4 else 0
    ym = sys.argv[5] if len(sys.argv) > 5 else "2026-09"
    t0, t1 = mv.month_bounds(ym)
    nm = (t1 - t0) // MIN
    res = {}
    with Pool(procs) as pool:
        for ex, mk, sub in mv.MKT:
            d = os.path.join(root, sub)
            syms = sorted(f[:-7] for f in os.listdir(d) if f.endswith(".csv.gz"))
            if lim:
                syms = syms[:lim]
            data = list(pool.imap_unordered(one, [(d, s, t0, t1) for s in syms], chunksize=4))
            allw = np.concatenate([w for _, w in data]) if data else np.zeros(0)
            # правило Судьи 27.10 (reviews/tk421-art-rule): x_max = наибольшее x, при котором наблюдения >= x лежат на >= 30 РАЗНЫХ минутах
            pm = np.zeros(nm, dtype=np.float32)
            for idx, w in data:
                np.maximum.at(pm, idx, w)
            pmpos = np.sort(pm[pm > 0])
            xmax_min = float(pmpos[-MIN_CLUST]) if len(pmpos) >= MIN_CLUST else None
            kw_old = knee(allw, W_LO, W_HI)
            kw = knee(allw, W_LO, W_HI, xmax_min) if xmax_min is not None else None
            present = np.zeros(nm, dtype=np.int64)
            for idx, _ in data:
                present += np.bincount(idx, minlength=nm)
            if kw is None:
                res[sub] = {"нет_излома_w": True, "инструментов": len(syms)}
                print(sub, "нет излома w", flush=True)
                continue
            xw = kw[0]
            wick = np.zeros(nm, dtype=np.int64)
            for idx, w in data:
                wick += np.bincount(idx[w >= xw], minlength=nm)
            share = np.where(present > 0, wick / np.maximum(present, 1), 0.0)
            obs = share[present > 0]
            ks = knee(obs, SH_LO, SH_HI)
            r = {"инструментов": len(syms), "инструмент_минут": int(allw.size), "x_w": xw, "x_w_lo": kw[1], "x_w_max": kw[2],
                 "минут_с_торгами": int((present > 0).sum()), "доля_фитильных_всего": float((allw >= xw).mean()),
                 "x_w_до_правки": kw_old[0] if kw_old else None, "x_w_max_до_правки": kw_old[2] if kw_old else None}
            top = np.argsort(-share * (wick >= MIN_CLUST))[:10]
            r["топ_минут"] = [{"мин_utc_мс": int(t0 + int(i) * MIN), "доля": float(share[i]), "фитильных": int(wick[i]), "присутствует": int(present[i])} for i in top]
            if ks is None:
                r["нет_излома_доли"] = True
                r["артефакт_минут"] = []
            else:
                xsh = ks[0]
                art = np.where((share >= xsh) & (wick >= MIN_CLUST))[0]
                r.update({"x_доли": xsh, "x_доли_lo": ks[1], "x_доли_max": ks[2], "артефакт_минут_число": int(art.size),
                          "артефакт_минут": [int(t0 + int(i) * MIN) for i in art],
                          "без_условия_30_число": int(((share >= xsh)).sum())})
            res[sub] = r
            print(sub, {k: v for k, v in r.items() if k not in ("артефакт_минут", "топ_минут")}, flush=True)
    json.dump({"месяц": ym, "результат": res}, open(outp, "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
