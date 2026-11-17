"""П-42 TK-593: кривые для страницы CatBoost (ROC, децили, калибровка, кривая обучения, переобучение, страты) из сохранённых моделей TK-592.

Использование: <venv>/bin/python tk593_compute.py <ep_u0.parquet> <каталог tree TK-592> <каталог кода TK-592 (tk592_lib.py, tk592_tree.py)> <выход curves.json>
Только половины поиска/подтверждения (сутки 14…73): отложенные сутки не читаются (проверка `day.max() <= 73`).
Воспроизведение: CB3 — из .cbm, CB1 — переобучение на tree_auc.extra.iters, логистика — той же функцией; AUC сверяются с tree_auc.csv (поле checks).
"""
from __future__ import annotations

import json
import sys
import time

import numpy as np
import pandas as pd
from catboost import CatBoostClassifier

table, tree_dir, code_dir, out_path = sys.argv[1:5]
sys.path.insert(0, code_dir)
import tk592_tree as T  # noqa: E402
from tk592_lib import FEATS, SEED  # noqa: E402

T.CB["thread_count"] = 6  # как в TK-592: от числа потоков зависит порядок суммирования и AUC в 4-м знаке (CB1 long A->B: 0,63445 при 2 потоках)
CB = T.CB
HOLD_FIRST_DAY = 74
B_STRATA = 1000
MIN_EP = 300
DAY0_DATE = pd.Timestamp("2026-09-15")  # сутки 14


def roc_grid(score: np.ndarray, y: np.ndarray, ng: int = 201):
    o = np.argsort(-score, kind="mergesort")
    s, yy = score[o], y[o].astype(float)
    last = np.r_[np.flatnonzero(np.diff(s)), len(s) - 1]
    tps = np.cumsum(yy)[last]
    fps = 1 + last - tps
    tpr = np.r_[0.0, tps / tps[-1]]
    fpr = np.r_[0.0, fps / fps[-1]]
    g = np.linspace(0, 1, ng)
    return g, np.interp(g, fpr, tpr)


def wilson(k: int, n: int, z: float = 1.96):
    if n == 0:
        return None, None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return float(c - h), float(c + h)


def clean(o):
    if isinstance(o, dict):
        return {k: clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, float) and o != o:
        return None
    return o


def rnd(a, nd=6):
    return [None if v is None or v != v else round(float(v), nd) for v in a]


def strat_stats(ix, s3, sl, y, coins, rng, B=B_STRATA):
    yv, a3, al, cs = y[ix], s3[ix], sl[ix], coins[ix]
    out = {"n": int(len(ix)), "k": int(yv.sum()), "auc_cb3": T.auc(a3, yv), "auc_logit": T.auc(al, yv)}
    out["delta"] = out["auc_cb3"] - out["auc_logit"] if out["auc_cb3"] == out["auc_cb3"] and out["auc_logit"] == out["auc_logit"] else float("nan")
    uc, inv = np.unique(cs, return_inverse=True)
    idx = [np.flatnonzero(inv == i) for i in range(len(uc))]
    A, D = [], []
    for _ in range(B):
        sel = rng.integers(0, len(uc), len(uc))
        j = np.concatenate([idx[i] for i in sel])
        x, z = T.auc(a3[j], yv[j]), T.auc(al[j], yv[j])
        if x == x and z == z:
            A.append(x)
            D.append(x - z)
    if len(A) >= B // 2:
        out["auc_cb3_lo"], out["auc_cb3_hi"] = float(np.quantile(A, 0.025)), float(np.quantile(A, 0.975))
        out["delta_lo"], out["delta_hi"] = float(np.quantile(D, 0.025)), float(np.quantile(D, 0.975))
    else:
        out["auc_cb3_lo"] = out["auc_cb3_hi"] = out["delta_lo"] = out["delta_hi"] = None
    out["small"] = bool(out["n"] < MIN_EP)
    return out


def main():
    t0 = time.time()
    auc_src = pd.read_csv(f"{tree_dir}/tree_auc.csv")
    frozen = json.load(open(f"{tree_dir}/frozen_tree.json"))
    df = T.add_cols(T.prepare(pd.read_parquet(table)))
    assert int(df["day"].max()) < HOLD_FIRST_DAY, "в таблице есть отложенные сутки"
    cols = list(FEATS)
    res = {"meta": {"day_first": int(df["day"].min()), "day_last": int(df["day"].max()), "features": cols, "min_ep": MIN_EP, "B_strata": B_STRATA, "seed": SEED},
           "cells": {}}
    checks = []
    for si, side in enumerate(("long", "short")):
        D = df[df.side == side].reset_index(drop=True)
        halves = {"A": D[D.half == "A"].reset_index(drop=True), "B": D[D.half == "B"].reset_index(drop=True)}
        res["cells"][side] = {}
        for di, (sh, th) in enumerate((("A", "B"), ("B", "A"))):
            S, Tt = halves[sh], halves[th]
            yS, yT = S["y"].to_numpy(), Tt["y"].to_numpy()
            XS, XT = S[cols].to_numpy(), Tt[cols].to_numpy()
            coins = Tt["sym"].to_numpy()
            rows = auc_src[(auc_src.side == side) & (auc_src.dir == f"{sh}->{th}") & (auc_src.set.isin(["f12", "turn"]))]
            src = {r.model: r for r in rows.itertuples()}
            ex = {m: json.loads(src[m].extra) for m in ("CB3", "CB1")}
            # модели
            (s_turn,), _ = T.logit_scores(S, [Tt], ["lturn24"])
            (s_log, s_log_S), _w = T.logit_scores(S, [Tt, S], cols)
            m3 = CatBoostClassifier()
            m3.load_model(f"{tree_dir}/models/cb3_{side}_{sh}_f12.cbm")
            s3, s3S = m3.predict_proba(XT)[:, 1], m3.predict_proba(XS)[:, 1]
            it3, it1 = ex["CB3"]["iters"], ex["CB1"]["iters"]
            m1 = CatBoostClassifier(iterations=it1, depth=1, **CB)
            m1.fit(XS, yS)
            s1, s1S = m1.predict_proba(XT)[:, 1], m1.predict_proba(XS)[:, 1]
            sc = {"CB3": s3, "CB1": s1, "LOGIT": s_log, "TURN": s_turn}
            for mn, v in sc.items():
                checks.append({"side": side, "dir": f"{sh}->{th}", "what": f"auc_{mn}", "src": float(src[mn].auc), "got": T.auc(v, yT)})
            # кривая обучения: те же параметры, больше итераций; точка остановки it3 выбрана на обучающей половине
            nit = 3 * it3
            lc = CatBoostClassifier(iterations=nit, depth=3, leaf_estimation_iterations=1, **CB)  # авто-значение зависит от числа итераций (10 при 3×it3, 1 у модели TK-592)
            lc.fit(XS, yS, eval_set=(XT, yT), use_best_model=False)
            ev = lc.get_evals_result()
            s3_lc = lc.predict_proba(XT, ntree_end=it3)[:, 1]
            checks.append({"side": side, "dir": f"{sh}->{th}", "what": "lc_prefix_vs_saved_max_abs_diff", "src": 0.0, "got": float(np.abs(s3_lc - s3).max())})
            # ранняя остановка: 5 блоков суток обучающей половины, AUC блока на его лучшей итерации (итерация выбрана на этом же блоке)
            folds, cv_its = [], []
            for gk in T.day_groups(S):
                lo, hi = min(gk), max(gk)
                va = S["day"].isin(gk).to_numpy()
                tr = (~va) & ~S["day"].isin([lo - 1, hi + 1]).to_numpy()
                m = CatBoostClassifier(iterations=T.ITER_CAP, depth=3, od_type="Iter", od_wait=50, **CB)
                m.fit(XS[tr], yS[tr], eval_set=(XS[va], yS[va]), use_best_model=True)
                cv_its.append(int(m.get_best_iteration()) + 1)
                pf = m.predict_proba(XS[va])[:, 1]
                folds.append({"days": [int(lo), int(hi)], "n": int(va.sum()), "k": int(yS[va].sum()), "auc": T.auc(pf, yS[va]), "iters": cv_its[-1]})
            checks.append({"side": side, "dir": f"{sh}->{th}", "what": "cv_iters_equal", "src": 1.0, "got": 1.0 if cv_its == ex["CB3"]["cv_iters"] else 0.0})
            # ROC
            roc = {}
            for mn, v in sc.items():
                g, tp = roc_grid(v, yT)
                roc[mn] = {"auc": T.auc(v, yT), "auc_trapz": float(np.trapezoid(tp, g)), "tpr": rnd(tp, 5)}
            roc_fpr = rnd(g, 4)
            # децили оценки CB3 на проверочной половине: доля пробоев, контроль, предсказание
            n = len(s3)
            o = np.argsort(s3, kind="mergesort")
            rank = np.empty(n, int)
            rank[o] = np.arange(n)
            dec = rank * 10 // n
            deciles = []
            for q in range(10):
                m_ = dec == q
                k_, n_ = int(yT[m_].sum()), int(m_.sum())
                lo_, hi_ = wilson(k_, n_)
                deciles.append({"n": n_, "k": k_, "y": k_ / n_, "y_lo": lo_, "y_hi": hi_, "c": float(Tt["c_f"].to_numpy()[m_].mean()), "pred": float(s3[m_].mean())})
            fz = frozen[f"{side}_{sh}_f12"]
            q_lo, q_hi = float((s3 <= fz["cut_lo"]).mean()), float((s3 <= fz["cut_hi"]).mean())
            # U1r (пул включён) внутри проверочной половины
            mu = Tt["in_pool"].to_numpy().astype(bool)
            u1 = {"n": int(mu.sum()), "k": int(yT[mu].sum()), **{mn: T.auc(v[mu], yT[mu]) for mn, v in sc.items()}}
            gate = pd.read_csv(f"{tree_dir}/tree_gate_u1.csv")
            gr = gate[(gate.side == side) & (gate.dir == f"{sh}->{th}")].iloc[0]
            checks.append({"side": side, "dir": f"{sh}->{th}", "what": "u1_auc_CB3", "src": float(gr.auc_cb3_u1), "got": u1["CB3"]})
            checks.append({"side": side, "dir": f"{sh}->{th}", "what": "u1_auc_LOGIT", "src": float(gr.auc_logit_u1), "got": u1["LOGIT"]})
            checks.append({"side": side, "dir": f"{sh}->{th}", "what": "u1_n", "src": float(gr.n_u1), "got": float(u1["n"])})
            # переобучение: в выборке против проверки
            overfit = {"folds": folds, "models": {mn: {"train": T.auc(ts, yS), "val": T.auc(v, yT)} for mn, ts, v in (("CB3", s3S, s3), ("CB1", s1S, s1), ("LOGIT", s_log_S, s_log))}}
            # страты на проверочной половине
            rng_base = [SEED, 593, si, di]
            strata = {"day": [], "lturn24": [], "natr_bp": [], "age_h": []}
            for i, d in enumerate(sorted(Tt["day"].unique())):
                st = strat_stats(np.flatnonzero((Tt["day"] == d).to_numpy()), s3, s_log, yT, coins, np.random.default_rng(rng_base + [1, i]))
                st["day"] = int(d)
                st["date"] = (DAY0_DATE + pd.Timedelta(days=int(d) - 14)).strftime("%Y-%m-%d")
                strata["day"].append(st)
            for fi, f in enumerate(("lturn24", "natr_bp", "age_h")):
                e1, e2 = [float(v) for v in np.quantile(S[f].dropna(), [1 / 3, 2 / 3])]
                x = Tt[f].to_numpy()
                for ti, (lab, m_) in enumerate((("L", x <= e1), ("M", (x > e1) & (x <= e2)), ("H", x > e2))):
                    st = strat_stats(np.flatnonzero(m_ & ~np.isnan(x)), s3, s_log, yT, coins, np.random.default_rng(rng_base + [2, fi, ti]))
                    st.update({"label": lab, "cut_lo": e1, "cut_hi": e2})
                    strata[f].append(st)
            res["cells"][side][f"{sh}->{th}"] = {
                "train_half": sh, "val_half": th, "n_T": int(len(yT)), "k_T": int(yT.sum()), "n_S": int(len(yS)), "k_S": int(yS.sum()),
                "base_y": float(yT.mean()), "base_c": float(Tt["c_f"].mean()),
                "iters": {"CB3": it3, "CB1": it1}, "tertile_cut_q": [q_lo, q_hi], "tertile_cut": [fz["cut_lo"], fz["cut_hi"]],
                "roc": roc, "deciles": deciles, "u1r": u1, "overfit": overfit, "strata": strata,
                "lc": {"stop": it3, "train": rnd(ev["learn"]["Logloss"]), "val": rnd(ev["validation"]["Logloss"])},
            }
            res["roc_fpr"] = roc_fpr
            print(side, f"{sh}->{th}", "готово %.0f с" % (time.time() - t0), flush=True)
    res["checks"] = checks
    res["seconds"] = time.time() - t0
    json.dump(clean(res), open(out_path, "w"), ensure_ascii=False, allow_nan=False)
    bad = [c for c in checks if abs(c["src"] - c["got"]) > 1e-9]
    print("checks", len(checks), "расхождений", len(bad), bad[:5], flush=True)


if __name__ == "__main__":
    main()
