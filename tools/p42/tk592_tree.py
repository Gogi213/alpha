"""П-42 §13.18 (TK-592): CatBoost на ядре — обучение на одной половине, проверка на другой (A→B, B→A), сторона отдельно.

Использование: <venv>/bin/python tk592_tree.py <ep_u0.parquet> <каталог пар (core.json)> <каталог выхода>
Модели: CB3 (глубина 3), CB1 (глубина 1 — гибкая аддитивная), LOGIT (аддитивная логистика), TURN (только оборот) на наборах признаков: core (если ≠ 12), f12, f48 (справочно).
Выход: tree_auc.csv, tree_delta.csv, tree_tertiles.csv, tree_shap.csv, tree_perm.csv, tree_gate_u1.csv, tree_summary.json, models/*.cbm + frozen_tree.json (для отложенных суток).
"""
from __future__ import annotations

import json
import os
import sys
import time

import numpy as np
import pandas as pd
from catboost import CatBoostClassifier, Pool
from scipy.optimize import minimize
from scipy.stats import rankdata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tk592_lib import FEATS, SEED, prepare  # noqa: E402

F48 = ["n_piv", "spread_bp", "spread_tau", "ext_gap_bp", "age_h", "since_born_h", "dist_bp", "pierce_ep", "pierce_max_bp", "touch_ep", "since_touch_h",
       "depth_min_bp", "depth_mean_bp", "approach_dur_min", "since_touch_prior_h", "zv2h", "zv6h", "flat60", "tpm15", "tpm60", "gap60_s", "mv5", "mv15",
       "mv60", "maxmv1", "maxrng15", "cons60", "cons120", "natr_bp", "lturn1", "lturn24", "c5_btc_7d", "c5_btc_24h", "c15_btc_7d", "c15_btc_24h",
       "c1_btc_6h", "btc_mv5", "btc_mv15", "c5_eth_7d", "c5_eth_24h", "c15_eth_7d", "c15_eth_24h", "c1_eth_6h", "eth_mv5", "eth_mv15", "cosig15", "peers7",
       "peer_sig30"]
CB = dict(learning_rate=0.05, l2_leaf_reg=3, grow_policy="Depthwise", min_data_in_leaf=300, random_seed=SEED, loss_function="Logloss",
          thread_count=6, verbose=0)
ITER_CAP = 2000
B_AUC = 1000


def add_cols(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    d["lturn1"] = np.log10(d["turn1h"].clip(lower=1))
    return d


def auc(x: np.ndarray, y: np.ndarray) -> float:
    ok = ~np.isnan(x)
    x, y = x[ok], y[ok].astype(bool)
    n1, n0 = int(y.sum()), int((~y).sum())
    if n1 == 0 or n0 == 0:
        return float("nan")
    r = rankdata(x)
    return float((r[y].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


# ───────────────────────────── аддитивная логистика ─────────────────────────────

def prep(tr: pd.DataFrame, te: pd.DataFrame, cols: list):
    Xtr = tr[cols].astype(float).copy()
    Xte = te[cols].astype(float).copy()
    for c in cols:
        med = Xtr[c].median()
        med = 0.0 if np.isnan(med) else med
        Xtr[c] = Xtr[c].fillna(med)
        Xte[c] = Xte[c].fillna(med)
        lo, hi = Xtr[c].quantile([0.01, 0.99])
        Xtr[c] = Xtr[c].clip(lo, hi)
        Xte[c] = Xte[c].clip(lo, hi)
        mu, sd = Xtr[c].mean(), Xtr[c].std() or 1.0
        Xtr[c] = (Xtr[c] - mu) / sd
        Xte[c] = (Xte[c] - mu) / sd
    return Xtr.to_numpy(), Xte.to_numpy()


def logit_fit(X: np.ndarray, y: np.ndarray, lam: float) -> np.ndarray:
    X1 = np.c_[np.ones(len(X)), X]

    def f(w):
        z = X1 @ w
        loss = np.logaddexp(0, z) - y * z
        p = 1 / (1 + np.exp(-z))
        return loss.sum() + lam * (w[1:] ** 2).sum() / 2, X1.T @ (p - y) + lam * np.r_[0, w[1:]]

    return minimize(f, np.zeros(X1.shape[1]), jac=True, method="L-BFGS-B").x


def logit_scores(tr, te_list, cols, y_col="y"):
    Xtr, _ = prep(tr, te_list[0], cols)
    w = logit_fit(Xtr, tr[y_col].to_numpy().astype(float), max(1.0, 2.0 * len(cols)))
    out = []
    for te in te_list:
        _, Xte = prep(tr, te, cols)
        out.append(np.c_[np.ones(len(Xte)), Xte] @ w)
    return out, w


# ───────────────────────────── CatBoost ─────────────────────────────

def day_groups(S: pd.DataFrame, k: int = 5):
    days = np.sort(S["day"].unique())
    return [set(g.tolist()) for g in np.array_split(days, k)]


def best_iters(S: pd.DataFrame, cols: list, depth: int) -> tuple[int, list]:
    """Ранняя остановка по 5 группам суток внутри половины обучения, эмбарго 1 сутки между обучающей и проверочной группой."""
    groups = day_groups(S)
    its = []
    for gk in groups:
        lo, hi = min(gk), max(gk)
        va = S["day"].isin(gk).to_numpy()
        tr = (~va) & ~S["day"].isin([lo - 1, hi + 1]).to_numpy()
        m = CatBoostClassifier(iterations=ITER_CAP, depth=depth, od_type="Iter", od_wait=50, **CB)
        m.fit(S.loc[tr, cols].to_numpy(), S.loc[tr, "y"].to_numpy(), eval_set=(S.loc[va, cols].to_numpy(), S.loc[va, "y"].to_numpy()), use_best_model=True)
        its.append(int(m.get_best_iteration()) + 1)
    return max(20, int(round(float(np.mean(its))))), its


def fit_cb(S: pd.DataFrame, cols: list, depth: int):
    n_it, its = best_iters(S, cols, depth)
    m = CatBoostClassifier(iterations=n_it, depth=depth, **CB)
    m.fit(S[cols].to_numpy(), S["y"].to_numpy())
    return m, n_it, its


def coin_resamples(coins: np.ndarray, B: int, rng: np.random.Generator):
    uc, inv = np.unique(coins, return_inverse=True)
    idx = [np.flatnonzero(inv == i) for i in range(len(uc))]
    for _ in range(B):
        sel = rng.integers(0, len(uc), len(uc))
        yield np.concatenate([idx[i] for i in sel])


def delta_ci(sa, sb, y, coins, rng, B=B_AUC):
    vals = []
    for ix in coin_resamples(coins, B, rng):
        a, b = auc(sa[ix], y[ix]), auc(sb[ix], y[ix])
        if a == a and b == b:
            vals.append(a - b)
    v = np.array(vals)
    return float(np.quantile(v, 0.025)), float(np.quantile(v, 0.975))


def tertile_table(T: pd.DataFrame, score_T: np.ndarray, cut: tuple, rng, B=B_AUC):
    """Терцили оценки (границы — с обучающей половины): n, k, y, c, E1 с интервалом по монетам; и на U1r."""
    g = np.where(score_T <= cut[0], 0, np.where(score_T <= cut[1], 1, 2))
    d = T["d"].to_numpy()
    rows = []
    coins = T["sym"].to_numpy()
    uc, inv = np.unique(coins, return_inverse=True)
    for q in range(3):
        m = g == q
        n = int(m.sum())
        row = {"tertile": ["low", "mid", "high"][q], "n": n, "k": int(T.loc[m, "y"].sum()), "y_share": float(T.loc[m, "y"].mean()) if n else np.nan,
               "c_share": float(T.loc[m, "c_f"].mean()) if n else np.nan, "E1": float(d[m].mean()) if n else np.nan}
        if n > 1:
            nc = np.bincount(inv[m], minlength=len(uc)).astype(float)
            sc = np.bincount(inv[m], weights=d[m], minlength=len(uc))
            W = rng.multinomial(len(uc), np.full(len(uc), 1 / len(uc)), size=B).astype(float)
            with np.errstate(invalid="ignore", divide="ignore"):
                v = (W @ sc) / (W @ nc)
            row["E1_lo"], row["E1_hi"] = float(np.nanquantile(v, 0.025)), float(np.nanquantile(v, 0.975))
        mu = m & T["in_pool"].to_numpy().astype(bool)
        row["u1_n"], row["u1_k"] = int(mu.sum()), int(T.loc[mu, "y"].sum())
        row["u1_E1"] = float(d[mu].mean()) if mu.sum() else np.nan
        rows.append(row)
    return rows


def main():
    table, pairs_dir, out = sys.argv[1], sys.argv[2], sys.argv[3]
    os.makedirs(f"{out}/models", exist_ok=True)
    core = json.load(open(f"{pairs_dir}/core.json"))["core_used"]
    df = add_cols(prepare(pd.read_parquet(table)))
    sets = {"f12": list(FEATS)}
    if sorted(core) != sorted(FEATS):
        sets = {"core": list(core), **sets}
    sets["f48"] = [c for c in F48 if c in df.columns]
    auc_rows, delta_rows, tert_rows, shap_rows, perm_rows, gate_rows, frozen = [], [], [], [], [], [], {}
    summary = {"sets": {k: v for k, v in sets.items()}, "n_f48": len(sets["f48"])}
    t0 = time.time()
    for si, side in enumerate(("long", "short")):
        D = df[df.side == side].reset_index(drop=True)
        halves = {"A": D[D.half == "A"].reset_index(drop=True), "B": D[D.half == "B"].reset_index(drop=True)}
        for di, (sh, th) in enumerate((("A", "B"), ("B", "A"))):
            S, T = halves[sh], halves[th]
            yT = T["y"].to_numpy()
            coinsT = T["sym"].to_numpy()
            rng = np.random.default_rng([SEED, si, di, 7])
            tag = f"{side} {sh}->{th}"
            # эталон: только оборот
            (s_turn,), _ = logit_scores(S, [T], ["lturn24"])
            auc_rows.append({"side": side, "dir": f"{sh}->{th}", "set": "turn", "model": "TURN", "auc": auc(s_turn, yT), "n_T": len(T), "k_T": int(yT.sum())})
            for name, cols in sets.items():
                (s_log,), _w = logit_scores(S, [T], cols)
                m3, it3, its3 = fit_cb(S, cols, 3)
                m1, it1, its1 = fit_cb(S, cols, 1)
                s3, s1 = m3.predict_proba(T[cols].to_numpy())[:, 1], m1.predict_proba(T[cols].to_numpy())[:, 1]
                for mn, sc, extra in (("LOGIT", s_log, {}), ("CB3", s3, {"iters": it3, "cv_iters": its3}), ("CB1", s1, {"iters": it1, "cv_iters": its1})):
                    auc_rows.append({"side": side, "dir": f"{sh}->{th}", "set": name, "model": mn, "auc": auc(sc, yT), "n_T": len(T), "k_T": int(yT.sum()),
                                     "extra": json.dumps(extra)})
                for a, b, lab in ((s3, s_log, "CB3-LOGIT"), (s3, s1, "CB3-CB1"), (s1, s_log, "CB1-LOGIT"), (s3, s_turn, "CB3-TURN"), (s_log, s_turn, "LOGIT-TURN")):
                    lo, hi = delta_ci(a, b, yT, coinsT, rng)
                    delta_rows.append({"side": side, "dir": f"{sh}->{th}", "set": name, "pair": lab, "delta": auc(a, yT) - auc(b, yT), "lo": lo, "hi": hi})
                # тертили оценки (границы — оценка на обучающей половине)
                for mn, model_scores in (("CB3", (m3.predict_proba(S[cols].to_numpy())[:, 1], s3)),):
                    cut = (float(np.quantile(model_scores[0], 1 / 3)), float(np.quantile(model_scores[0], 2 / 3)))
                    for r in tertile_table(T, model_scores[1], cut, rng):
                        tert_rows.append({"side": side, "dir": f"{sh}->{th}", "set": name, "model": mn, "cut_lo": cut[0], "cut_hi": cut[1], **r})
                    frozen[f"{side}_{sh}_{name}"] = {"cut_lo": cut[0], "cut_hi": cut[1], "iters": it3, "cols": cols}
                    m3.save_model(f"{out}/models/cb3_{side}_{sh}_{name}.cbm")
                # логистика: те же терцили
                Xtr, Xte = prep(S, T, cols)
                w = logit_fit(Xtr, S["y"].to_numpy().astype(float), max(1.0, 2.0 * len(cols)))
                s_tr = np.c_[np.ones(len(Xtr)), Xtr] @ w
                cut = (float(np.quantile(s_tr, 1 / 3)), float(np.quantile(s_tr, 2 / 3)))
                for r in tertile_table(T, s_log, cut, rng):
                    tert_rows.append({"side": side, "dir": f"{sh}->{th}", "set": name, "model": "LOGIT", "cut_lo": cut[0], "cut_hi": cut[1], **r})
                # SHAP-взаимодействия и перестановки (не для f48)
                if name != "f48":
                    phi = m3.get_feature_importance(Pool(T[cols].to_numpy(), yT), type="ShapInteractionValues")
                    F = len(cols)
                    for i in range(F):
                        for j in range(i + 1, F):
                            shap_rows.append({"side": side, "dir": f"{sh}->{th}", "set": name, "f1": cols[i], "f2": cols[j],
                                              "mean_abs": float(2 * np.abs(phi[:, i, j]).mean())})
                    base = auc(s3, yT)
                    for ci_, c in enumerate(cols):
                        drops = []
                        for rep in range(5):
                            Xp = T[cols].copy()
                            Xp[c] = np.random.default_rng([SEED, rep, ci_]).permutation(Xp[c].to_numpy())
                            drops.append(base - auc(m3.predict_proba(Xp.to_numpy())[:, 1], yT))
                        perm_rows.append({"side": side, "dir": f"{sh}->{th}", "set": name, "feature": c, "auc_drop": float(np.mean(drops))})
                print(tag, name, "AUC CB3 %.3f CB1 %.3f LOGIT %.3f TURN %.3f it3=%d it1=%d  %.0f с" % (auc(s3, yT), auc(s1, yT), auc(s_log, yT), auc(s_turn, yT), it3, it1, time.time() - t0), flush=True)
                # ворота U1r: дерево против «оборот + возраст + NATR» на U1r половины проверки
                if name == ("core" if "core" in sets else "f12"):
                    mu = T["in_pool"].to_numpy().astype(bool)
                    (s_ref,), _ = logit_scores(S, [T], ["lturn24", "age_h", "natr_bp"])
                    if mu.sum() > 30:
                        gate_rows.append({"side": side, "dir": f"{sh}->{th}", "set": name, "n_u1": int(mu.sum()), "k_u1": int(yT[mu].sum()),
                                          "auc_cb3_u1": auc(s3[mu], yT[mu]), "auc_ref_u1": auc(s_ref[mu], yT[mu]), "auc_logit_u1": auc(s_log[mu], yT[mu])})
    pd.DataFrame(auc_rows).to_csv(f"{out}/tree_auc.csv", index=False)
    pd.DataFrame(delta_rows).to_csv(f"{out}/tree_delta.csv", index=False)
    pd.DataFrame(tert_rows).to_csv(f"{out}/tree_tertiles.csv", index=False)
    pd.DataFrame(shap_rows).to_csv(f"{out}/tree_shap.csv", index=False)
    pd.DataFrame(perm_rows).to_csv(f"{out}/tree_perm.csv", index=False)
    pd.DataFrame(gate_rows).to_csv(f"{out}/tree_gate_u1.csv", index=False)
    json.dump(frozen, open(f"{out}/frozen_tree.json", "w"), ensure_ascii=False, indent=1)
    summary["seconds"] = time.time() - t0
    json.dump(summary, open(f"{out}/tree_summary.json", "w"), ensure_ascii=False, indent=1)
    print("готово", summary["seconds"], flush=True)


if __name__ == "__main__":
    main()
