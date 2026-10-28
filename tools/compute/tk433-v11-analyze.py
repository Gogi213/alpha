#!/usr/bin/env python3
"""TK-433 v11 (протокол §3): r = log(мера события / медиана меры контролей), медиана r по событиям, бутстреп по монетам
(общие ресэмплы монет на (D, знак)), p и интервал Холма — нормальное приближение по SE бутстрепа (B=3000; квантиль 1/(2·190) на B=3000 не берётся).
Использование: tk433-v11-analyze.py <каталог vol-D*.csv ±30> <каталог ±7 или -> <выход-префикс>
Семейство Холма: (22×2 − 2 RNG-«внутри») × 5 D = 190, отдельно по знаку. «Внутри» — описательно, в выбор лучшей меры не входит."""
import csv, glob, math, os, re, sys
import numpy as np
from scipy.stats import norm

B, SEED, K = 3000, 433, 190
POSN = ("pre", "in")


def coin(s):
    s = re.sub(r"(USDT|USDC|PERP|USD)$", "", s)
    return re.sub(r"^(1000000|100000|10000|1000)", "", s)


def load(path):
    rd = csv.DictReader(open(path, encoding="utf-8"))
    rows = list(rd)
    cols = rd.fieldnames
    coins = np.array([coin(r["инструмент"]) for r in rows])
    vs = sorted({c[:-4] for c in cols if c.endswith("_pre")})
    R = {}
    for v in vs:
        for p in POSN:
            e = np.array([float(r[f"{v}_{p}"] or "nan") for r in rows])
            c = np.array([float(r[f"{v}_{p}_ctl"] or "nan") for r in rows])
            ok = (e > 0) & (c > 0)
            R[(v, p)] = np.where(ok, np.log(np.where(ok, e, 1) / np.where(ok, c, 1)), np.nan)
    dur = np.array([float(r["длит_мин"]) for r in rows])
    return coins, R, vs, dur


def wmedian(v, w):
    o = np.argsort(v)
    cw = np.cumsum(w[:, o], axis=1)
    half = cw[:, -1:] / 2
    k = (cw < half).sum(axis=1)
    return v[o][np.minimum(k, len(v) - 1)]


def stats(coins, R, rng):
    cs, ci = np.unique(coins, return_inverse=True)
    W = rng.multinomial(len(cs), np.ones(len(cs)) / len(cs), size=B).astype(np.float32)
    out = {}
    for key, r in R.items():
        ok = ~np.isnan(r)
        if ok.sum() < 8 or len(np.unique(coins[ok])) < 5:
            out[key] = (int(ok.sum()), np.nan, np.nan, np.nan)
            continue
        m = float(np.median(r[ok]))
        bm = wmedian(r[ok], W[:, ci[ok]])
        se = float(bm.std(ddof=1))
        p = float(2 * norm.sf(abs(m) / se)) if se > 0 else np.nan
        out[key] = (int(ok.sum()), m, se, p)
    return out


def main():
    d30, d7, outp = sys.argv[1], sys.argv[2], sys.argv[3]
    rng = np.random.default_rng(SEED)
    rows = []
    for D in (90, 180, 360, 720, 1440):
        for sg, n in (("+", "p"), ("-", "m")):
            f = os.path.join(d30, f"vol-D{D}{n}.csv")
            coins, R, vs, dur = load(f)
            S30 = stats(coins, R, rng)
            S7 = None
            if d7 != "-" and os.path.exists(os.path.join(d7, f"vol-D{D}{n}.csv")):
                c7, R7, _, _ = load(os.path.join(d7, f"vol-D{D}{n}.csv"))
                S7 = stats(c7, R7, rng)
            for (v, p), (N, m, se, pv) in S30.items():
                s7 = S7[(v, p)][1] if S7 else np.nan
                rows.append(dict(D=D, знак=sg, мера=v, положение=p, N=N, монет=len(np.unique(coins)), мед_r=m, SE=se, p=pv, мед_r_pm7=s7,
                                 событий_всего=len(coins), мед_длит=float(np.median(dur))))
    fam = [r for r in rows if not (r["мера"].startswith("RNG") and r["положение"] == "in")]
    for sg in ("+", "-"):
        fs = sorted((r for r in fam if r["знак"] == sg), key=lambda r: (np.isnan(r["p"]), r["p"]))
        mx = 0.0
        for i, r in enumerate(fs):
            r["p_Холм"] = min(1.0, max(mx, r["p"] * (K - i))) if not np.isnan(r["p"]) else np.nan
            mx = r["p_Холм"] if not np.isnan(r["p_Холм"]) else mx
    for r in rows:
        r.setdefault("p_Холм", np.nan)
        if np.isnan(r["p"]):
            r["вердикт"] = "нет данных"
        else:
            sign_ok = np.isnan(r["мед_r_pm7"]) or (np.sign(r["мед_r_pm7"]) == np.sign(r["мед_r"]))
            lo = r["мед_r"] - norm.isf(0.05 / K / 2) * r["SE"]
            hi = r["мед_r"] + norm.isf(0.05 / K / 2) * r["SE"]
            if r["мера"].startswith("RNG") and r["положение"] == "in":
                v = "тавтология (вне семейства)"
            elif not np.isnan(r["p_Холм"]) and r["p_Холм"] < 0.05 and sign_ok:
                v = "подтверждено" if r["мед_r"] > 0 else "оспорено (меньше контролей)"
            else:
                v = "не различимо"
            if np.isnan(r["мед_r_pm7"]):
                v += " [±7 не считан]"
            elif not sign_ok:
                v += " [знак ±7 другой]"
            r["вердикт"] = v
    keys = ["D", "знак", "мера", "положение", "N", "монет", "мед_r", "SE", "p", "p_Холм", "мед_r_pm7", "вердикт", "событий_всего", "мед_длит"]
    with open(outp + "-r.csv", "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, keys, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: (f"{r[k]:.4g}" if isinstance(r[k], float) else r[k]) for k in keys})
    n_conf = sum(1 for r in rows if r["вердикт"].startswith("подтверждено"))
    print("строк", len(rows), "в семействе", len(fam), "подтверждено", n_conf)


if __name__ == "__main__":
    main()
