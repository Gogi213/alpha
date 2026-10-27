#!/usr/bin/env python3
"""TK-421 шаг B1 (протокол P-TK421 v3/v5): пороги a*_T (V0) по K1 «излом» из гистограмм шага A.
Использование: tk421-b1-thresh.py <каталог шага A> <выход.json> [месяц ГГГГ-ММ] [B]
По (биржа-рынок, T, знак) и по трём срезам (месяц, 1-я половина, 2-я половина):
S(x) — выживание пулом по ходам-минутам (гистограмма ha, бины по log a, A_LO..A_HI, NB=400); сетка — 200 точек
РАВНЫХ ШАГОВ log x от x(S=0,1) до x_max (x_max — последняя точка, где ≥ 30 монето-суток имеют суточный максимум ≥ x:
кластеры монета-день, правило (3)); K1 = argmax |вторая разность log S| с шагом 3 точки сетки (без сглаживания);
интервал — бутстреп по инструментам (с возвращением, B, процентильный 95 %).
Сетка равна по log x, а не по log S (в протоколе «равные шаги log S»): при равных шагах log S вторая разность log S
тождественно 0 — выбор записан в отчёт. Только V0 (V1 в L0 не входит, v5 п.1). K2 (GPD) — отдельным шагом."""
import json
import math
import sys
import numpy as np

NB = 400
A_LO, A_HI = 0.001, 4.0
G = 200
STEP = 3
MIN_CLUST = 30
KEYS = [f"{T}{s}" for T in (5, 15, 60, 240, 1440) for s in "+-"]
LA, SA = math.log(A_LO), (math.log(A_HI) - math.log(A_LO)) / NB


def edges():
    # нижняя граница бина k (k>=1): exp(LA + (k-1)*SA); бин 0 — x<=0 или ниже A_LO
    return np.exp(LA + (np.arange(1, NB + 1) - 1) * SA)


def surv_from_hist(h, lo_edges):
    """h: (N, NB+1) счётчики -> S на нижних границах бинов 1..NB (доля минут с бином >= k)."""
    tot = h.sum(axis=-1, keepdims=True).astype(float)
    cum = np.cumsum(h[..., ::-1], axis=-1)[..., ::-1]  # число минут в бинах >= k
    return cum[..., 1:] / np.maximum(tot, 1.0), tot


def k1(s_edges, xs, xgrid_log):
    """s_edges: S на lo_edges; интерполяция log S по log x на сетку; argmax |d2|."""
    lx = np.log(xs)
    ls = np.log(np.maximum(s_edges, 1e-300))
    lsg = np.interp(xgrid_log, lx, ls)
    d2 = lsg[2 * STEP:] - 2 * lsg[STEP:-STEP] + lsg[:-2 * STEP]
    j = int(np.argmax(np.abs(d2))) + STEP
    return j, float(d2[j - STEP])


def run_slice(H, dmax, sl, B, rng):
    """H: (N, 2, NB+1) для T/знака; sl: 'м'|'1'|'2'; dmax: (N, ndays)."""
    h = H.sum(axis=1) if sl == "м" else H[:, 0 if sl == "1" else 1]
    xs = edges()
    S, _ = surv_from_hist(h.sum(axis=0, keepdims=True), xs)
    S = S[0]
    ok = S > 0
    if ok.sum() < 20:
        return None
    # x(S=0,1) и x_max по кластерам монета-день
    flat = dmax.ravel()
    flat = flat[flat > 0]
    if len(flat) < MIN_CLUST:
        return None
    xmax = float(np.sort(flat)[-MIN_CLUST])
    i01 = np.searchsorted(-S, -0.1)  # первая позиция, где S <= 0,1
    xlo = float(xs[min(i01, NB - 1)])
    if not xmax > xlo * 1.05:
        return {"нет_хвоста": True, "x_lo": xlo, "x_max": xmax}
    xg = np.linspace(math.log(xlo), math.log(xmax), G)
    j, d2 = k1(S, xs, xg)
    out = {"x_star": float(math.exp(xg[j])), "d2": d2, "x_lo": xlo, "x_max": xmax, "кластеров_хвост": int((flat >= math.exp(xg[j])).sum()),
           "минут": int(h.sum())}
    n = h.shape[0]
    bs = []
    for _ in range(B):
        w = np.bincount(rng.integers(0, n, n), minlength=n).astype(np.int64)
        hb = (w @ h)[None, :]
        Sb, _ = surv_from_hist(hb, xs)
        jb, _ = k1(Sb[0], xs, xg)
        bs.append(math.exp(xg[jb]))
    out["ci95"] = [float(np.percentile(bs, 2.5)), float(np.percentile(bs, 97.5))]
    return out


def main():
    d, outp = sys.argv[1], sys.argv[2]
    ym = sys.argv[3] if len(sys.argv) > 3 else "2026-09"
    B = int(sys.argv[4]) if len(sys.argv) > 4 else 1000
    rng = np.random.default_rng(421)
    res = {}
    for ex, mk in (("bybit", "linear"), ("bybit", "spot"), ("gate", "futures_usdt"), ("gate", "spot")):
        rows = []
        with open(f"{d}/moves-{ym}-{ex}-{mk}.jsonl") as f:
            for line in f:
                r = json.loads(line)
                if "ha" in r:
                    rows.append(r)
        n = len(rows)
        print(ex, mk, n, "инструментов", flush=True)
        for k in KEYS:
            H = np.array([r["ha"][k] for r in rows], dtype=np.int64)  # (N,2,NB+1)
            dm = np.array([r["dmax_a"][k] for r in rows], dtype=float)
            for sl in ("м", "1", "2"):
                res[f"{ex}-{mk}|{k}|{sl}"] = run_slice(H, dm, sl, B, rng)
            print(" ", k, res[f"{ex}-{mk}|{k}|м"] and res[f"{ex}-{mk}|{k}|м"].get("x_star"), flush=True)
    with open(outp, "w") as f:
        json.dump({"месяц": ym, "B": B, "сетка": G, "шаг": STEP, "min_кластеров": MIN_CLUST, "результат": res}, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
