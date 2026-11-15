"""Прогноз качелей вне выборки: параметры режима на момент t (только данные ≤ t) → число и время качелей после t.

Запуск: python3 swings_oos.py <swings.pkl> <regime.pkl> <выход oos.pkl> [PAR]
Точки отсчёта t — каждые 30 мин, с 8-го часа окна до t1 − 1 ч. Для каждой пары и N (качели L = 300 мс) предикторы частоты (качелей/ч):
  glob    — (0,1 + k) / (1 ч + экспозиция): все качели с начала данных до t;
  win6    — то же по последним 6 ч (фиксированное окно: боковая линия сравнения);
  reg     — режим интервалов между качелями (BOCPD по интервалам, bocpd.bocpd_exp): качели и экспозиция с начала текущего режима;
  ou_glob — частота качелей по OU (swings_fit.ou_swing_rate), μ, σ, φ — по всем минутам до t;
  ou_reg  — то же по текущему режиму середины (BOCPD с авторегрессией, возраст режима r_t; окно не короче 45 мин);
  ou_surr — как ou_reg, но по фаз-рандомизованному ряду (плацебо: тот же спектр, нет режимов и переходов).
Исход: число качелей в (t, t + H], H = 1 и 6 ч; время до следующей качели (с цензурой на конце данных). Всё — по реальным качелям.
"""
import pickle
import sys
import time
from multiprocessing import Pool
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import bocpd  # noqa: E402
import swings  # noqa: E402
import swings_fit  # noqa: E402
import swings_regime  # noqa: E402

HOUR = swings.HOUR
START_H, STEP_MIN = 8, 30
HS = (1, 6)
PRED = ("glob", "win6", "reg", "ou_glob", "ou_reg", "ou_surr")
A0, B0 = 0.1, 1.0          # сглаживание частоты эмпирических предикторов: (A0 + k) / (B0 + часы)
MINW = 45                  # минимум минут в окне оценки параметров OU
LAMS_SW = (5, 15, 50, 150, 10 ** 9)   # интервалов между сменами режима потока качелей
G = {}


class Cums:
    """Префиксные суммы минутного ряда для статистик любого окна."""

    def __init__(self, x, hm):
        self.x = x
        self.c1 = np.r_[0.0, np.cumsum(x)]
        self.c2 = np.r_[0.0, np.cumsum(x * x)]
        self.cx = np.r_[0.0, 0.0, np.cumsum(x[1:] * x[:-1])]
        self.ch = np.r_[0.0, np.cumsum(hm)]

    def win(self, jj, w):
        """Окно из w точек, заканчивающееся на jj (включительно). -> μ, σ, φ(1 мин), средний полуспред."""
        a = jj - w + 1
        mu = (self.c1[jj + 1] - self.c1[a]) / w
        var = max((self.c2[jj + 1] - self.c2[a]) / w - mu * mu, 1e-12)
        sxy = (self.cx[jj + 1] - self.cx[a + 1]) / max(w - 1, 1)
        phi = min(max((sxy - mu * mu) / var, 0.02), 0.9995)
        return mu, np.sqrt(var), phi, (self.ch[jj + 1] - self.ch[a]) / w


def ou_rates(cu, jj, w, ns):
    mu, sd, phi, hs = cu.win(jj, w)
    th = -np.log(phi) / 60.0
    return [swings_fit.ou_swing_rate(mu, sd, th, N + hs, -N - hs) for N in ns]


def work(key):
    g = G
    v, rg = g["pairs"][key], g["reg"].get(key)
    if rg is None:
        return key, None
    t0, t1 = g["t0"], g["t1"]
    to = t0 + START_H * HOUR + np.arange(0, (t1 - HOUR - t0 - START_H * HOUR) // (STEP_MIN * 60000) + 1) * STEP_MIN * 60000
    no, nN = len(to), len(swings.NS)
    i0, xr = swings_regime.raw_series(v)
    hm = v["hm"].astype(float)[i0:]
    hm = np.where(np.isnan(hm), np.nanmedian(hm), hm)
    cu = Cums(xr, hm)
    xs = rg["xs"].astype(float)
    cus = Cums(xs, hm[:len(xs)])
    j = (to - t0) // 60000 - 1 - i0            # индекс в ряду от i0 (минута-конец = t0 + (j + i0 + 1)·мин)
    start = t0 + max(v["t0e"], 0)
    out = {"to": to, "hist_min": j.astype(np.int32)}
    pr = {p: np.full((nN, no), np.nan, np.float32) for p in PRED}
    real = {h: np.full((nN, no), np.nan, np.float32) for h in HS}
    tn = np.full((nN, no), np.nan, np.float32)
    obs = np.zeros((nN, no), bool)
    hist = np.zeros((nN, no), np.int32)
    ok = j >= 360                                # ≥ 6 ч истории минут
    ou_g, ou_r, ou_s = (np.full((nN, no), np.nan, np.float32) for _ in range(3))
    for o in np.flatnonzero(ok):
        jj = int(j[o])
        rr = int(rg["rl"][jj - 1]) if jj >= 1 and jj - 1 < len(rg["rl"]) else 0
        rs = int(rg["rl_s"][jj - 1]) if jj >= 1 and jj - 1 < len(rg["rl_s"]) else 0
        ou_g[:, o] = ou_rates(cu, jj, jj + 1, swings.NS)
        ou_r[:, o] = ou_rates(cu, jj, max(min(rr + 1, jj + 1), min(MINW, jj + 1)), swings.NS)
        ou_s[:, o] = ou_rates(cus, jj, max(min(rs + 1, jj + 1), min(MINW, jj + 1)), swings.NS)
    pr["ou_glob"], pr["ou_reg"], pr["ou_surr"] = ou_g, ou_r, ou_s
    for iN in range(nN):
        tc = np.sort(t0 + v["sw"][(iN, 300)][0].astype(np.int64))
        A = np.r_[start, tc]
        tau = np.diff(A) / 1000.0
        rl_sw = bocpd.bocpd_exp(tau, 1.0 / g["lam_sw"])[0] if len(tau) else np.zeros(0, np.int32)
        k = np.searchsorted(tc, to, "right")                 # качелей ≤ t
        hist[iN] = k
        expo = (to - start) / HOUR
        pr["glob"][iN] = (A0 + k) / (B0 + expo)
        k6 = k - np.searchsorted(tc, to - 6 * HOUR, "right")
        pr["win6"][iN] = (A0 + k6) / (B0 + np.minimum(6.0, expo))
        r = np.where(k >= 1, rl_sw[np.maximum(k - 1, 0)] if len(rl_sw) else 0, 0)
        ts = A[np.maximum(k - r, 0)]                         # A[k] — последний приход ≤ t (A[0] = начало)
        pr["reg"][iN] = (A0 + r) / (B0 + (to - ts) / HOUR)
        for h in HS:
            e = to + h * HOUR
            real[h][iN] = np.where(e <= t1, np.searchsorted(tc, e, "right") - k, np.nan)
        nx = np.searchsorted(tc, to, "right")
        has = nx < len(tc)
        tn[iN] = np.where(has, (tc[np.minimum(nx, max(len(tc) - 1, 0))] - to) / 1000.0 if len(tc) else 0, (t1 - to) / 1000.0)
        obs[iN] = has
    for p in PRED:
        pr[p][:, ~ok] = np.nan
    out.update({"pred": pr, "real": real, "tn": tn, "obs": obs, "hist": hist, "ok": ok})
    return key, out


def sw_job(args):
    key, iN = args
    v = G["pairs"][key]
    tc = np.sort(v["sw"][(iN, 300)][0].astype(np.int64))
    start = max(v["t0e"], 0)
    tau = np.diff(np.r_[start, tc]) / 1000.0
    return {lam: (bocpd.bocpd_exp(tau, 1.0 / lam)[1] if len(tau) else 0.0) for lam in LAMS_SW}, len(tau)


def main(src, reg, dst, par=6):
    d = pickle.load(open(src, "rb"))
    r = pickle.load(open(reg, "rb"))
    G.update({"pairs": d["pairs"], "reg": r["pairs"], "t0": d["t0"], "t1": d["t1"]})
    keys = sorted(k for k in d["pairs"] if k in r["pairs"])
    t = time.time()
    rng = np.random.default_rng(5)
    cand = [(k, iN) for k in keys for iN in (1, 2, 3) if len(d["pairs"][k]["sw"][(iN, 300)][0]) >= 40]
    pick = [cand[i] for i in rng.choice(len(cand), min(200, len(cand)), replace=False)]
    with Pool(par) as p:
        ev = p.map(sw_job, pick, chunksize=4)
    tot = {lam: sum(e[0][lam] for e in ev) for lam in LAMS_SW}
    G["lam_sw"] = max(tot, key=tot.get)
    print({"lam_sw": G["lam_sw"], "evidence": {k: round(v) for k, v in tot.items()}, "series": len(pick), "s": round(time.time() - t)}, flush=True)
    res = {}
    with Pool(par) as p:
        for k, (key, o) in enumerate(p.imap_unordered(work, keys, chunksize=2)):
            if o is not None:
                res[key] = o
            if k % 200 == 0:
                print(f"{k + 1}/{len(keys)} пар, {time.time() - t:.0f} с", flush=True)
    with open(dst, "wb") as f:
        pickle.dump({"oos": res, "lam_sw": G["lam_sw"], "sw_evidence": tot, "t0": d["t0"], "t1": d["t1"], "lam": r["lam"], "pred": PRED, "hs": HS}, f, protocol=4)
    print({"pairs": len(res), "s": round(time.time() - t)})


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]) if len(sys.argv) > 4 else 6)
