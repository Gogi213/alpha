#!/usr/bin/env python3
"""TK-433 v10 шаг: r по признакам конца (К1, К3, З1–З3) и сетка старта (DV, natr30, natr1d, консолидация, наклонки).
r = значение события − медиана контролей (лог-отношение для З1–З3); бутстреп по монетам; Холм из 135 (v10 §6).
К2, К4–К8 (сделки/OI) — отдельным шагом. Вход: end-start-th{T}.csv. Выход: <out>-T{T}.csv"""
import sys, re
import numpy as np, pandas as pd

B, SEED, K = 4000, 433, 135
TRD = ["K5_fade", "K6_delta", "K6_int", "K7_retest", "K4_fade", "K8_diff"]  # из tk433-trades.py (необязательно)
GRID = [1 / 8, 1 / 4, 1 / 2, 1, 2, 4, 8]


def coin(s):
    s = re.sub(r"(USDT|USDC|PERP|USD)$", "", s)
    return re.sub(r"^(1000000|100000|10000|1000)", "", s)


def lratio(a, b):
    return np.log(a / b) if a > 0 and b > 0 else np.nan


def feats(x):
    return dict(K1_mono=x.k1_mono, K1_steep=x.k1_last_steepest, K3_saw=x.k3_saw,
                Z1_m=lratio(x.m_aft, x.m_bef), Z2_v=lratio(x.v_aft, x.v_bef), Z3_bas=lratio(x.bas_aft, x.bas_bef),
                CONS=np.log(x.cons_ratio) if x.cons_ratio > 0 else np.nan,
                **{k: getattr(x, k, np.nan) for k in TRD})


def passes(x):
    o = {}
    for nm, v in (("DV", x.dv24 / 350e6), ("natr30", x.natr30 / 1.1), ("natr1d", x.natr1d / 1.1)):
        for c in GRID:
            o[f"{nm}_x{c:g}"] = float(v > c) if v == v else np.nan
    return o


def r_per_event(d):
    ev = d[d["роль"] == "event"].set_index("id")
    rows = []
    for eid, g in d[d["роль"] == "control"].groupby("event_id"):
        e = ev.loc[eid]
        fe = {**feats(e), **passes(e)}
        cs = [{**feats(x), **passes(x)} for x in g.itertuples()]
        row = dict(id=eid, знак=int(e["знак"]), монета=coin(e["инструмент"]))
        for k, v in fe.items():
            c = np.array([q[k] for q in cs], float)
            c = c[~np.isnan(c)]
            if np.isnan(v) or len(c) == 0:
                row[k] = np.nan
            elif k.startswith(("DV", "natr", "K1", "K3")) and not k.startswith("K3"):
                row[k] = v - c.mean()  # доля прошедших / флаг — разность со средней долей контролей
            else:
                row[k] = v - np.median(c)
        rows.append(row)
    return pd.DataFrame(rows)


def boot(v, coins, rng, agg):
    cs = np.unique(coins)
    idx = [np.where(coins == c)[0] for c in cs]
    out = np.empty(B)
    for b in range(B):
        pick = rng.integers(0, len(cs), len(cs))
        out[b] = agg(np.concatenate([v[idx[i]] for i in pick]))
    return out


def main(src, out, T):
    d = pd.read_csv(src)
    if len(sys.argv) > 4:  # + csv признаков сделок (tk433-trades.py)
        d = d.merge(pd.read_csv(sys.argv[4]).drop_duplicates("id"), on="id", how="left")
    R = r_per_event(d)
    rng = np.random.default_rng(SEED)
    cols = [c for c in R.columns if c not in ("id", "знак", "монета")]
    rows = []
    for sg in (1, -1):
        sub = R[R["знак"] == sg]
        for f in cols:
            v = sub[f].to_numpy(float)
            ok = ~np.isnan(v)
            agg = np.mean if f.startswith(("DV", "natr", "K1")) else np.median
            row = dict(T=T, знак="+" if sg == 1 else "-", признак=f, N=int(ok.sum()), монет=int(sub.loc[ok, "монета"].nunique()))
            if ok.sum() < 8 or row["монет"] < 2:
                row.update(r=np.nan, p=np.nan, _b=None)
            else:
                bm = boot(v[ok], sub.loc[ok, "монета"].to_numpy(), rng, agg)
                row.update(r=float(agg(v[ok])), p=float(min(1, 2 * min((bm <= 0).mean(), (bm >= 0).mean()) + 1 / B)), _b=bm)
            rows.append(row)
    T_ = pd.DataFrame(rows)
    order = T_["p"].fillna(2).sort_values().index
    for rank, i in enumerate(order):
        T_.loc[i, "k_шага"] = K - rank
    for i, r in T_.iterrows():
        if r["_b"] is None:
            T_.loc[i, "вердикт"] = "не оценено"
            continue
        a = 0.05 / r["k_шага"]
        lo, hi = np.quantile(r["_b"], [a / 2, 1 - a / 2])
        T_.loc[i, ["lo", "hi"]] = lo, hi
        T_.loc[i, "вердикт"] = "подтверждено" if lo > 0 else ("не различимо (насыщено)" if lo == hi == 0 else ("оспорено" if hi <= 0 else "не различимо"))
    T_ = T_.drop(columns="_b")
    T_.to_csv(f"{out}-T{T}.csv", index=False, float_format="%.4g")
    print(T, "событий с контролем", len(R))
    print(T_[["знак", "признак", "N", "монет", "r", "lo", "hi", "вердикт"]].to_string())


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], int(sys.argv[3]))
