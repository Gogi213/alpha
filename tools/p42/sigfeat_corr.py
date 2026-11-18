"""П-42 §13.17 (TK-588): корреляции всех монет со всеми (описательно). Запуск на srv:
python3 sigfeat_corr.py <рабочий каталог>  → `<work>/corr/*` (матрицы npz, сводка json, топ-пары csv, кластеры json).
Минутки 566 монет за 01.09–13.11 (все данные до отложенных суток); доходности блоков 5 / 15 мин (пропуск, не 0), 1 мин — справочно;
остатки после беты к BTC; кластеры — иерархия, средняя связь по 1 − ρ.
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sigfeat_lib import *  # noqa: F401,F403

CACHE = "/data/p42-t566/cache"
OWNER = ["STXXUSDT", "STXUSDT", "CLANKERUSDT", "KORUUSDT", "NXPCUSDT"]
MIN_N = {"r5": 2000, "r15": 700, "r1": 10000}
B_PER_DAY = {"r5": 288, "r15": 96}


def residualize(R: np.ndarray, btc: np.ndarray):
    """Остатки после беты к BTC (МНК по общим блокам, β по всему окну); NaN, где доходности монеты нет."""
    ok = ~np.isnan(R) & ~np.isnan(btc)[None, :]
    E = np.full_like(R, np.nan)
    beta = np.full(R.shape[0], np.nan)
    for i in range(R.shape[0]):
        o = ok[i]
        if o.sum() < 100:
            continue
        x, y = btc[o], R[i, o]
        v = float(((x - x.mean()) ** 2).sum())
        beta[i] = float(((x - x.mean()) * (y - y.mean())).sum() / v) if v > 0 else np.nan
        E[i, o] = y - beta[i] * x
    return E, beta


def clusters(r: np.ndarray, syms: list[str], cut: float, min_size: int = 3):
    from scipy.cluster.hierarchy import fcluster, linkage
    from scipy.spatial.distance import squareform
    d = 1.0 - np.where(np.isnan(r), 0.0, r)
    np.fill_diagonal(d, 0.0)
    d = (d + d.T) / 2.0
    z = linkage(squareform(d, checks=False), method="average")
    lab = fcluster(z, t=1.0 - cut, criterion="distance")
    out = []
    for c in np.unique(lab):
        idx = np.flatnonzero(lab == c)
        if len(idx) >= min_size:
            sub = r[np.ix_(idx, idx)]
            iu = np.triu_indices(len(idx), 1)
            vals = sub[iu]
            out.append({"size": int(len(idx)), "mean_r": float(np.nanmean(vals)), "members": [syms[i] for i in idx]})
    out.sort(key=lambda x: -x["size"])
    return out


def main():
    work = sys.argv[1]
    od = f"{work}/corr"
    os.makedirs(od, exist_ok=True)
    syms = sorted(f[:-4] for f in os.listdir(CACHE) if f.endswith(".b42"))
    ns = len(syms)
    ix = {s: i for i, s in enumerate(syms)}
    R1 = np.empty((ns, N_MIN), np.float32)
    R5 = np.empty((ns, N_MIN // 5))
    R15 = np.empty((ns, N_MIN // 15))
    listed = np.zeros(ns, np.int64)
    for i, s in enumerate(syms):
        m = load_b42(f"{CACHE}/{s}.b42")
        R1[i] = minute_returns(m)
        R5[i] = block_returns(m, 5)
        R15[i] = block_returns(m, 15)
        listed[i] = int((m["o"] != 0).sum())
    print(f"загружено {ns} монет", flush=True)
    # 1 мин — на подвыборке времени не режем: матрицы по полному ряду через float32 в блоках монет не нужны, считаем как есть
    mats = {}
    res = {}
    for name, R in (("r5", R5), ("r15", R15)):
        r, n = corr_matrix(R, MIN_N[name])
        mats[name] = (r, n)
        btc = R[ix["BTCUSDT"]]
        E, beta = residualize(R, btc)
        re, ne = corr_matrix(E, MIN_N[name])
        mats[name + "_resid"] = (re, ne)
        res[name + "_beta"] = beta
    r1, n1 = corr_matrix(R1.astype(np.float64), MIN_N["r1"])
    mats["r1"] = (r1, n1)
    np.savez_compressed(f"{od}/matrices.npz", syms=np.array(syms), **{k: v[0].astype(np.float32) for k, v in mats.items()},
                        **{k + "_n": v[1].astype(np.int32) for k, v in mats.items()})
    summ = {"n_coins": ns, "window": "01.09–13.11 (74 сут.)", "min_n": MIN_N}
    iu = np.triu_indices(ns, 1)
    for k, (r, n) in mats.items():
        v = r[iu]
        v = v[~np.isnan(v)]
        summ[k] = {"pairs": int(len(v)), "pairs_possible": int(len(iu[0])), "mean": float(v.mean()), "p10": float(np.percentile(v, 10)),
                   "p90": float(np.percentile(v, 90)), "share_ge_0p5": float((v >= 0.5).mean()), "share_ge_0p3": float((v >= 0.3).mean())}
    # первая главная компонента матрицы r5 (пропуски → 0)
    c = np.where(np.isnan(mats["r5"][0]), 0.0, mats["r5"][0])
    w = np.linalg.eigvalsh(c)
    summ["pc1_share_r5"] = float(w[-1] / w.sum())
    c = np.where(np.isnan(mats["r5_resid"][0]), 0.0, mats["r5_resid"][0])
    w = np.linalg.eigvalsh(c)
    summ["pc1_share_r5_resid"] = float(w[-1] / w.sum())
    # корреляция монет с BTC и ETH
    per_coin = []
    for i, s in enumerate(syms):
        per_coin.append({"sym": s, "r5_btc": float(mats["r5"][0][i, ix["BTCUSDT"]]), "r5_eth": float(mats["r5"][0][i, ix["ETHUSDT"]]),
                         "beta_btc_r5": float(res["r5_beta"][i]), "minutes_with_data": int(listed[i])})
    # топ-пары
    def top_pairs(r, n, k=30):
        pairs = []
        for a, b in zip(*iu):
            if not np.isnan(r[a, b]):
                pairs.append((float(r[a, b]), syms[a], syms[b], int(n[a, b])))
        pairs.sort(reverse=True)
        return pairs[:k]
    tops = {"resid_r5": top_pairs(*mats["r5_resid"]), "raw_r5": top_pairs(*mats["r5"]), "resid_r15": top_pairs(*mats["r15_resid"])}
    # кластеры
    cl = {}
    for cut in (0.2, 0.3, 0.4):
        cl[f"resid_r5_cut{cut}"] = clusters(mats["r5_resid"][0], syms, cut)
    for cut in (0.5, 0.6, 0.7):
        cl[f"raw_r5_cut{cut}"] = clusters(mats["r5"][0], syms, cut)
    # устойчивость по суткам: r5 остатков внутри суток для топ-30 пар и пар владельца
    btc5 = R5[ix["BTCUSDT"]]
    E5, _ = residualize(R5, btc5)
    pairs = [(a, b) for _, a, b, _ in tops["resid_r5"]] + [(a, b) for i, a in enumerate(OWNER) for b in OWNER[i + 1:]]
    pairs += [(a, "BTCUSDT") for a in OWNER] + [(a, "ETHUSDT") for a in OWNER]
    bpd = B_PER_DAY["r5"]
    daily = {}
    for a, b in pairs:
        if a not in ix or b not in ix:
            continue
        rr, rraw = [], []
        for d in range(DAYS_TOTAL):
            sl = slice(d * bpd, (d + 1) * bpd)
            rr.append(pearson_pairs(E5[ix[a], sl], E5[ix[b], sl], 60))
            rraw.append(pearson_pairs(R5[ix[a], sl], R5[ix[b], sl], 60))
        rr, rraw = np.array(rr), np.array(rraw)
        v = rr[~np.isnan(rr)]
        vr = rraw[~np.isnan(rraw)]
        daily[f"{a}|{b}"] = {"days": int(len(v)), "mean_resid": float(v.mean()) if len(v) else None, "share_ge_0p3": float((v >= 0.3).mean()) if len(v) else None,
                             "min_resid": float(v.min()) if len(v) else None, "mean_raw": float(vr.mean()) if len(vr) else None,
                             "share_raw_ge_0p5": float((vr >= 0.5).mean()) if len(vr) else None}
    # структура: Спирмен вектора r5-остатков между 01.09–07.10 (сутки 0–36) и 08.10–13.11 (37–73)
    from scipy.stats import spearmanr
    h = 37 * bpd
    rA, _ = corr_matrix(E5[:, :h], 700)
    rB, _ = corr_matrix(E5[:, h:], 700)
    a_, b_ = rA[iu], rB[iu]
    ok = ~(np.isnan(a_) | np.isnan(b_))
    summ["split_half_spearman_resid_r5"] = float(spearmanr(a_[ok], b_[ok]).statistic)
    summ["split_half_pairs"] = int(ok.sum())
    # пары владельца: сырые, остатки, частная по BTC и ETH (r5, всё окно)
    own = {}
    eth5 = R5[ix["ETHUSDT"]]
    for a, b in [(x, y) for i, x in enumerate(OWNER) for y in OWNER[i + 1:]]:
        if a in ix and b in ix:
            i, j = ix[a], ix[b]
            ok = ~(np.isnan(R5[i]) | np.isnan(R5[j]) | np.isnan(btc5) | np.isnan(eth5))
            Z = np.stack([btc5[ok], eth5[ok], np.ones(ok.sum())], 1)
            ra = R5[i][ok] - Z @ np.linalg.lstsq(Z, R5[i][ok], rcond=None)[0]
            rb = R5[j][ok] - Z @ np.linalg.lstsq(Z, R5[j][ok], rcond=None)[0]
            own[f"{a}|{b}"] = {"raw_r5": float(mats["r5"][0][i, j]), "raw_r15": float(mats["r15"][0][i, j]), "raw_r1": float(mats["r1"][0][i, j]),
                               "resid_btc_r5": float(mats["r5_resid"][0][i, j]), "partial_btc_eth_r5": float(np.corrcoef(ra, rb)[0, 1]),
                               "n_r5": int(mats["r5"][1][i, j])}
    json.dump({"summary": summ, "per_coin": per_coin, "top_pairs": tops, "clusters": cl, "daily": daily, "owner_pairs": own},
              open(f"{od}/summary.json", "w"), ensure_ascii=False, indent=1)
    print("готово", flush=True)


if __name__ == "__main__":
    main()
