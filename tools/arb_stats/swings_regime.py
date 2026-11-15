"""Режимы середины исполнимых цен пары: BOCPD с авторегрессией (bocpd.py) по минутной сетке; вероятность смены режима выбирается по правдоподобию данных.

Запуск: python3 swings_regime.py <swings.pkl> <выход regime.pkl> [PAR]
Нормировка ряда — по первым 120 минутам (медиана, MAD; пол — медианный полуспред и 0,001 %): причинная (дальше ничего не подглядывает).
Выбор λ = 1/H (минут между сменами режима): 150 случайных пар (по 75 из ok и bx), суммарное логарифмическое правдоподобие, λ ∈ LAMS; λ = ∞ — режим один.
Суррогат (плацебо): фаз-рандомизация минутного ряда (тот же спектр и размах, без режимов и переходов) через тот же BOCPD — rl_s.
"""
import pickle
import zlib
import sys
import time
from multiprocessing import Pool
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import bocpd  # noqa: E402

LAMS = (60, 180, 540, 1620, 4860, 10 ** 9)   # минут
NORM_MIN = 120
G = {}


def raw_series(v):
    """-> (i0, минутный ряд m без NaN (одиночные пропуски — последнее значение)) или None. i0 — первая минута с данными."""
    x = v["mm"].astype(float)
    ok = np.flatnonzero(~np.isnan(x))
    if len(ok) < 600:
        return None
    i0 = int(ok[0])
    x = x[i0:]
    bad = np.isnan(x)
    if bad.any():
        idx = np.where(~bad, np.arange(len(x)), 0)
        np.maximum.accumulate(idx, out=idx)
        x = x[idx]
    return i0, x


def prep(v):
    """-> (i0, нормированный ряд, медиана, масштаб) или None."""
    r = raw_series(v)
    if r is None:
        return None
    i0, x = r
    h = v["hm"].astype(float)[i0:i0 + NORM_MIN]
    first = x[:NORM_MIN]
    med = float(np.median(first))
    sc = max(1.4826 * float(np.median(np.abs(first - med))), float(np.nanmedian(h)), 1e-3)
    return i0, (x - med) / sc, med, sc


def phase_randomise(x, rng):
    n = len(x)
    f = np.fft.rfft(x - x.mean())
    ph = rng.uniform(0, 2 * np.pi, len(f))
    ph[0] = 0.0
    if n % 2 == 0:
        ph[-1] = 0.0
    return np.fft.irfft(np.abs(f) * np.exp(1j * ph), n) + x.mean()


def hazard_job(args):
    key, lam = args
    p = prep(G["pairs"][key])
    return key, lam, bocpd.bocpd_ar1(p[1], 1.0 / lam)[1]


def regime_job(key):
    v = G["pairs"][key]
    p = prep(v)
    if p is None:
        return key, None
    i0, x, med, sc = p
    rng = np.random.default_rng(zlib.crc32(repr(key).encode()))
    rl, ev, pcp = bocpd.bocpd_ar1(x, 1.0 / G["lam"])
    xs = phase_randomise(x, rng)
    rls, evs, _ = bocpd.bocpd_ar1(xs, 1.0 / G["lam"])
    return key, {"i0": i0, "rl": rl, "rl_s": rls, "ev": ev, "ev_s": evs, "pcp": pcp.astype(np.float32), "med": med, "sc": sc, "xs": (xs * sc + med).astype(np.float32)}


def main(src, dst, par=6):
    d = pickle.load(open(src, "rb"))
    G["pairs"] = d["pairs"]
    keys = sorted(k for k in d["pairs"])
    rng = np.random.default_rng(11)
    ok = [k for k in keys if "bingx" not in k[1:]]
    bx = [k for k in keys if "bingx" in k[1:]]
    sample = [ok[i] for i in rng.choice(len(ok), min(75, len(ok)), replace=False)] + [bx[i] for i in rng.choice(len(bx), min(75, len(bx)), replace=False)]
    t = time.time()
    ev = {}
    with Pool(par) as p:
        for key, lam, e in p.imap_unordered(hazard_job, [(k, lam) for k in sample for lam in LAMS], chunksize=4):
            ev.setdefault(lam, {})[key] = e
    tot = {lam: sum(v.values()) for lam, v in ev.items()}
    totg = {lam: (sum(v for k, v in ev[lam].items() if "bingx" not in k[1:]), sum(v for k, v in ev[lam].items() if "bingx" in k[1:])) for lam in ev}
    lam = max(tot, key=tot.get)
    print({"hazard_evidence": {k: round(v) for k, v in tot.items()}, "by_group_ok_bx": {k: [round(x) for x in v] for k, v in totg.items()}, "lam": lam, "s": round(time.time() - t)}, flush=True)
    G["lam"] = lam
    out = {}
    with Pool(par) as p:
        for k, (key, r) in enumerate(p.imap_unordered(regime_job, keys, chunksize=2)):
            if r is not None:
                out[key] = r
            if k % 100 == 0:
                print(f"{k + 1}/{len(keys)} пар, {time.time() - t:.0f} с", flush=True)
    with open(dst, "wb") as f:
        pickle.dump({"lam": lam, "hazard_evidence": tot, "hazard_by_group": totg, "sample": sample, "pairs": out, "t0": d["t0"], "t1": d["t1"]}, f, protocol=4)
    print({"pairs": len(out), "s": round(time.time() - t)})


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 6)
