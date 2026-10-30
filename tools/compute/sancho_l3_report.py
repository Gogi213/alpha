#!/usr/bin/env python3
"""TK-455 / P-TK455 §5–§7: агрегация слоя 3 (якорные строки сделок -> таблица K=60, Холм, вердикты D/P, срезы).
Вход — <run>/<SYM>-<tf>.csv.gz (строки сделок якоря: real/C1/C2/C3). Кластер = день UTC, B=20000, seed 455.
python sancho_l3_report.py <run_dir> <out_prefix> [NX]
Выход: <pre>-rows.csv (K=60), <pre>-years.csv, <pre>-syms.csv, <pre>-funnel.csv"""
import glob
import os
import sys
from multiprocessing import Pool
import numpy as np
import pandas as pd

B, CH, SEED = 20000, 4000, 455
DAY = 86400000
G = None


def load(run):
    fs = sorted(f for f in glob.glob(os.path.join(run, "*.csv.gz")) if not f.endswith("-agg.csv.gz") and ".tmp" not in f)
    D = pd.concat((pd.read_csv(f) for f in fs), ignore_index=True)
    D["day"] = D.t0 // DAY
    D["year"] = pd.to_datetime(D.t0, unit="ms").dt.year
    ok = D.gross.notna()
    D["net_mix"] = D.gross - D.fee_mix - D.fund
    D["net_mk"] = D.gross - D.fee_mk - D.fund
    D["net_tk"] = D.gross - D.fee_tk - D.fund
    return D, ok


def boot(key):
    """key = (ev, form, tf, dir); на день: n и суммы net_{mix,mk,tk} real / C1 / C2."""
    days, M = G[key]  # M: dict роль -> (n, s_mix, s_mk, s_tk) по дням
    D = len(days)
    n_r, sm, sk, st = M["real"]
    N = n_r.sum()
    if N < 8 or D < 5:
        return key, None
    rng = np.random.default_rng(SEED)
    out = {k: [] for k in ("mix", "mk", "tk", "d1", "d2")}
    for _ in range(B // CH):
        cnt = rng.multinomial(D, np.full(D, 1 / D), CH).astype(np.float64)
        nr = cnt @ n_r
        nr = np.where(nr > 0, nr, np.nan)
        mix, mk, tk = (cnt @ sm) / nr, (cnt @ sk) / nr, (cnt @ st) / nr
        out["mix"].append(mix); out["mk"].append(mk); out["tk"].append(tk)
        for c, nm in (("C1", "d1"), ("C2", "d2")):
            if c in M:
                nc = cnt @ M[c][0]
                out[nm].append(mix - (cnt @ M[c][1]) / np.where(nc > 0, nc, np.nan))
    o = {k: np.concatenate(v) for k, v in out.items() if v}
    pt = lambda x, f: float(np.nanpercentile(x, f))
    res = dict(N=int(N), days=D, mean_mix=sm.sum() / N, mean_mk=sk.sum() / N, mean_tk=st.sum() / N,
               mix_lo=pt(o["mix"], 2.5), mix_hi=pt(o["mix"], 97.5),
               mk_hi=pt(o["mk"], 97.5), tk_lo=pt(o["tk"], 2.5),
               p_tk_pos=(np.nanmean(o["tk"] <= 0) + 1 / B), p_mk_neg=(np.nanmean(o["mk"] >= 0) + 1 / B))
    for c, nm in (("C1", "d1"), ("C2", "d2")):
        if nm in o:
            nc = M[c][0].sum()
            d0 = res["mean_mix"] - M[c][1].sum() / nc
            dd = o[nm]
            res.update({f"delta_{c}": d0, f"lo_{c}": pt(dd, 2.5), f"hi_{c}": pt(dd, 97.5), f"ctl_mean_{c}": M[c][1].sum() / nc,
                        f"p_{c}": min(1.0, 2 * min(np.nanmean(dd <= 0), np.nanmean(dd >= 0)) + 1 / B)})
    return key, res


def holm(p):
    p = np.asarray(p, float)
    o = np.argsort(p, kind="stable")
    adj = np.maximum.accumulate((len(p) - np.arange(len(p))) * p[o]).clip(max=1)
    r = np.empty_like(adj)
    r[o] = adj
    return r


def day_arrays(g):
    d = g.groupby("day").agg(n=("net_mix", "size"), s_mix=("net_mix", "sum"), s_mk=("net_mk", "sum"), s_tk=("net_tk", "sum"))
    return d


def slice_table(R, C, keys):
    """срез (год / монета): N, средние net, Δ против C1, C2 (описательно, без бутстрепа)."""
    rows = []
    for k, g in R.groupby(keys):
        r = dict(zip(keys, k if isinstance(k, tuple) else (k,)))
        r.update(N=len(g), coins=g.sym.nunique(), mean_gross=g.gross.mean(), mean_mix=g.net_mix.mean(), mean_mk=g.net_mk.mean(),
                 mean_tk=g.net_tk.mean(), win_mix=(g.net_mix > 0).mean())
        rows.append(r)
    return pd.DataFrame(rows)


if __name__ == "__main__":
    run, pre = sys.argv[1], sys.argv[2]
    nx = int(sys.argv[3]) if len(sys.argv) > 3 else 15
    D, ok = load(run)
    # воронка (причины по роли real)
    fun = D[D.role == "real"].assign(trade=ok).groupby(["ev", "form", "tf", "dir"]).apply(
        lambda g: pd.Series(dict(signals=len(g), trades=int(g.trade.sum()), **g[~g.trade].reason.value_counts().to_dict())),
        include_groups=False).reset_index()
    fun.to_csv(f"{pre}-funnel.csv", index=False)
    T = D[ok].copy()
    # монета «с историей с 2020»: есть строки C2 (бары без условия) в 2020 по этому ТФ
    first = D.groupby("sym").t0.min()
    hist = set(first[pd.to_datetime(first, unit="ms").dt.year <= 2020].index)
    T["hist2020"] = T.sym.isin(hist)
    G = {}
    for key, g in T.groupby(["ev", "form", "tf", "dir"]):
        days = np.sort(g.day.unique())
        M = {}
        for role in ("real", "C1", "C2"):
            gr = g[g.role == role]
            if len(gr) == 0:
                continue
            a = day_arrays(gr).reindex(days, fill_value=0.0)
            M[role] = tuple(a[c].values.astype(np.float64) for c in ("n", "s_mix", "s_mk", "s_tk"))
        if "real" in M:
            G[key] = (days, M)
    with Pool(nx) as p:
        res = p.map(boot, list(G), chunksize=1)
    rows = [dict(zip(("ev", "form", "tf", "dir"), k), **r) for k, r in res if r]
    R = pd.DataFrame(rows)
    # семейство K=60: A: A1,A4..A8; B: A5..A8; tf 60/240/1440; сторона H/L
    fam = [(e, "A", tf, d) for e in ("A1", "A4", "A5", "A6", "A7", "A8") for tf in (60, 240, 1440) for d in ("H", "L")] + \
          [(e, "B", tf, d) for e in ("A5", "A6", "A7", "A8") for tf in (60, 240, 1440) for d in ("H", "L")]
    assert len(fam) == 60
    F = pd.DataFrame(fam, columns=["ev", "form", "tf", "dir"]).merge(R, how="left", on=["ev", "form", "tf", "dir"])
    nodata = F.N.isna()  # нет данных -> остаётся в семействе, p=1
    F["p_D"] = np.where(nodata, 1.0, F[["p_C1", "p_C2"]].max(axis=1))
    F["p_D_holm"] = holm(F.p_D.fillna(1.0))
    F["p_P_pos_holm"] = holm(F.p_tk_pos.fillna(1.0))
    F["p_P_neg_holm"] = holm(F.p_mk_neg.fillna(1.0))
    # года: знак Δ по годам (N >= 8 и >= 5 монет)
    Y = T.groupby(["ev", "form", "tf", "dir", "year"]).apply(
        lambda g: pd.Series(dict(N=int((g.role == "real").sum()), coins=g[g.role == "real"].sym.nunique(),
                                 mean_mix=g[g.role == "real"].net_mix.mean(), mean_mk=g[g.role == "real"].net_mk.mean(),
                                 mean_tk=g[g.role == "real"].net_tk.mean(),
                                 d1=g[g.role == "real"].net_mix.mean() - g[g.role == "C1"].net_mix.mean(),
                                 d2=g[g.role == "real"].net_mix.mean() - g[g.role == "C2"].net_mix.mean())), include_groups=False).reset_index()
    Y["enough"] = (Y.N >= 8) & (Y.coins >= 5)
    Y.to_csv(f"{pre}-years.csv", index=False)
    ys = Y[Y.enough].groupby(["ev", "form", "tf", "dir"]).apply(
        lambda g: pd.Series(dict(yrs=len(g), yrs_pos=int(((g.d1 > 0) & (g.d2 > 0)).sum()), yrs_neg=int(((g.d1 < 0) & (g.d2 < 0)).sum()),
                                 yrs_mixed=int(len(g) - ((g.d1 > 0) & (g.d2 > 0)).sum() - ((g.d1 < 0) & (g.d2 < 0)).sum()))),
        include_groups=False).reset_index()
    F = F.merge(ys, how="left", on=["ev", "form", "tf", "dir"])
    both_pos = (F.delta_C1 > 0) & (F.delta_C2 > 0)
    both_neg = (F.delta_C1 < 0) & (F.delta_C2 < 0)
    yok_pos = (F.yrs_mixed == 0) & (F.yrs_neg == 0) & (F.yrs >= 1)
    yok_neg = (F.yrs_mixed == 0) & (F.yrs_pos == 0) & (F.yrs >= 1)
    F["D"] = np.where((F.p_D_holm < .05) & both_pos & yok_pos & (F.lo_C1 > 0) & (F.lo_C2 > 0), "подтверждено",
              np.where((F.p_D_holm < .05) & both_neg & yok_neg & (F.hi_C1 < 0) & (F.hi_C2 < 0), "оспорено", "не различимо"))
    F["P"] = np.where(F.p_P_pos_holm < .05, "положительно", np.where(F.p_P_neg_holm < .05, "отрицательно", "не различимо"))
    F["verdict"] = np.where((F.D == "подтверждено") & (F.P == "положительно"), "держится",
                    np.where((F.D == "оспорено") | (F.P == "отрицательно"), "оспорено", "не различимо"))
    F["breakeven_slip_bps_leg"] = F.mean_mix / np.where(F.form == "A", 2.0, 2.0)  # 2 ноги; >0 -> запас на ногу
    # доля выигрышей, средний gross, C3 — по сделкам
    X = T[T.role == "real"].groupby(["ev", "form", "tf", "dir"]).agg(
        win_mix=("net_mix", lambda s: (s > 0).mean()), mean_gross=("gross", "mean"), mean_fund=("fund", "mean"),
        coins=("sym", "nunique")).reset_index()
    X3 = T[T.role == "C3"].groupby(["ev", "form", "tf", "dir"]).agg(c3_mean_mix=("net_mix", "mean"), c3_n=("net_mix", "size")).reset_index()
    F = F.merge(X, how="left", on=["ev", "form", "tf", "dir"]).merge(X3, how="left", on=["ev", "form", "tf", "dir"])
    # срезы: BTC/ETH, с историей 2020, по монетам
    sp = T[T.role == "real"].assign(grp=lambda d: np.where(d.sym.isin(["BTCUSDT", "ETHUSDT"]), "BTC/ETH", "прочие"))
    sl = slice_table(sp, None, ["ev", "form", "tf", "dir", "grp"])
    h = slice_table(sp, None, ["ev", "form", "tf", "dir", "hist2020"])
    F = F.merge(sl.pivot_table(index=["ev", "form", "tf", "dir"], columns="grp", values="mean_mix").add_prefix("mix_").reset_index(),
                how="left", on=["ev", "form", "tf", "dir"])
    F = F.merge(h.pivot_table(index=["ev", "form", "tf", "dir"], columns="hist2020", values="mean_mix").add_prefix("mix_hist2020_").reset_index(),
                how="left", on=["ev", "form", "tf", "dir"])
    F.to_csv(f"{pre}-rows.csv", index=False)
    slice_table(sp, None, ["sym", "ev", "form", "tf", "dir"]).to_csv(f"{pre}-syms.csv", index=False)
    print(len(F), "строк без данных:", int(nodata.sum()), F.verdict.value_counts().to_dict(),
          "D:", F.D.value_counts().to_dict(), "P:", F.P.value_counts().to_dict())
