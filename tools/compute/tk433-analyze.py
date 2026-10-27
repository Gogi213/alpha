#!/usr/bin/env python3
"""TK-433 шаг 3: r = признак тренда против медианы его контролей, бутстреп по монетам, Холм из 34 (v9b + правка Судьи 28.10).
Вход: features-2026-09.csv (tk433-features.py merge). Выход: r-таблица csv + события csv. Главный θ=60; θ=240 — чувствительность."""
import sys, re, json
import numpy as np, pandas as pd

LOG = ["v_in", "v_pre", "m_in", "m_pre"]
FEATS = ["v_in", "v_pre", "m_in", "m_pre", "buy_in", "buy_pre", "sf_diff", "sf_corr", "oi_in", "fund_in", "liq_in", "btc_in",
         "basis_pre", "basis_in", "basis_chg", "lead_in", "fsr_in"]
B, SEED, K = 4000, 433, 34


def coin(s):
    s = re.sub(r"(USDT|USDC|PERP|USD)$", "", s)
    return re.sub(r"^(1000000|100000|10000|1000)", "", s)


def rval(f, ev, ct):
    c = ct[~np.isnan(ct)]
    if np.isnan(ev) or len(c) == 0:
        return np.nan
    m = np.median(c)
    if f in LOG:
        return np.log(ev / m) if ev > 0 and m > 0 else np.nan
    return ev - m


def build(d, theta):
    d = d[d["θ"] == theta]
    ev = d[d["роль"] == "event"].set_index("id")
    ct = d[d["роль"] == "control"]
    rows, null = [], []
    for eid, g in ct.groupby("event_id"):
        e = ev.loc[eid]
        row = dict(id=eid, знак=int(e["знак"]), монета=coin(e["инструмент"]), n_ctrl=len(g))
        for f in FEATS:
            row[f] = rval(f, e[f], g[f].to_numpy(float))
        rows.append(row)
        if len(g) >= 2:
            for i in range(len(g)):
                x = g.iloc[i]
                rest = g.drop(g.index[i])
                null.append(dict(знак=int(e["знак"]), **{f: rval(f, x[f], rest[f].to_numpy(float)) for f in FEATS}))
    return pd.DataFrame(rows), pd.DataFrame(null)


def boot_medians(r, coins, rng):
    cs = np.unique(coins)
    idx = [np.where(coins == c)[0] for c in cs]
    out = np.empty(B)
    for b in range(B):
        pick = rng.integers(0, len(cs), len(cs))
        out[b] = np.median(np.concatenate([r[idx[i]] for i in pick]))
    return out


def table(R, N0):
    rng = np.random.default_rng(SEED)
    rows = []
    for sg in (1, -1):
        for f in FEATS:
            sub = R[R["знак"] == sg]
            v = sub[f].to_numpy(float)
            ok = ~np.isnan(v)
            row = dict(знак="+" if sg == 1 else "-", признак=f, N=int(ok.sum()), монет=int(sub.loc[ok, "монета"].nunique()))
            if ok.sum() < 8 or sub.loc[ok, "монета"].nunique() < 5:
                row.update(медиана_r=np.nan, p=np.nan, boot=None)
            else:
                bm = boot_medians(v[ok], sub.loc[ok, "монета"].to_numpy(), rng)
                row.update(медиана_r=float(np.median(v[ok])), p=float(min(1, 2 * min((bm <= 0).mean(), (bm >= 0).mean()) + 1 / B)), boot=bm)
                n0 = N0[(N0["знак"] == sg)][f].dropna().to_numpy(float)
                row["N_нуль"] = len(n0)
                if len(n0) >= 20:
                    q = np.quantile(n0, 0.95)
                    row["доля_выше_q95_нуля"] = float((v[ok] > q).mean())
                    row["нуль_медиана"] = float(np.median(n0))
            rows.append(row)
    T = pd.DataFrame(rows)
    # Холм из K=34: непосчитанные (нет данных) остаются в семействе
    order = T["p"].fillna(2).sort_values().index
    for rank, i in enumerate(order):
        T.loc[i, "k_шага"] = K - rank
    for i, r in T.iterrows():
        if r["boot"] is None:
            T.loc[i, "вердикт"] = "нет данных"
            continue
        a = 0.05 / r["k_шага"]
        lo, hi = np.quantile(r["boot"], [a / 2, 1 - a / 2])
        T.loc[i, "lo"], T.loc[i, "hi"] = lo, hi
        T.loc[i, "вердикт"] = "подтверждено" if lo > 0 else ("оспорено" if hi <= 0 else "не различимо")
    return T.drop(columns="boot")


if __name__ == "__main__":
    src, out = sys.argv[1], sys.argv[2]
    d = pd.read_csv(src)
    for th in (60, 240):
        R, N0 = build(d, th)
        T = table(R, N0)
        T.to_csv(f"{out}-r-th{th}.csv", index=False, float_format="%.4g")
        R.to_csv(f"{out}-events-r-th{th}.csv", index=False, float_format="%.4g")
        print(th, "событий с контролем", len(R), "нулей", len(N0))
        print(T[["знак", "признак", "N", "монет", "медиана_r", "lo", "hi", "вердикт", "доля_выше_q95_нуля"]].to_string())
