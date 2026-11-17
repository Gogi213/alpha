"""П-42 §13.20 (TK-594): сравнение наборов признаков S0 = f12, S1 = f12 + поток, S2 = f12 + производные, S3 = f12 + поток + производные на эпизодах TK-592.

Использование (venv с catboost): python tk594_cmp.py <ep_u0.parquet> <feat_flow.parquet> <feat_deriv.parquet> <каталог выхода>
Модели: CB3 (§13.18 п. 10) и LOGIT (то же, но пропуски — медиана обучающей половины + индикатор пропуска). Обучение A → проверка B и B → A, сторона отдельно.
Первичный контраст на сторону: Δ = AUC[CB3(S3)] − AUC[CB3(S0)], парный бутстреп по монетам (2 000, зерно 594), интервал 97,5 %; прошёл — нижняя граница > 0 в обоих направлениях и ΔE1_top > 0 в обоих.
Описательно: остальные 10 контрастов (95 %), U1r, внутримонетный AUC, групповая перестановка CB3(S3), терцильные таблицы, покрытие.
"""
from __future__ import annotations

import json
import os
import sys
import time

import numpy as np
import pandas as pd
from scipy.stats import rankdata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tk592_lib import FEATS, prepare  # noqa: E402
from tk592_tree import fit_cb, tertile_table  # noqa: E402
from tk594_feat import DERIV, FLOW  # noqa: E402

SEED = 594
B_BOOT = 2000
SETS = {"S0": list(FEATS), "S1": list(FEATS) + FLOW, "S2": list(FEATS) + DERIV, "S3": list(FEATS) + FLOW + DERIV}
TK592_AUC = {("long", "A->B"): 0.642, ("long", "B->A"): 0.616, ("short", "A->B"): 0.618, ("short", "B->A"): 0.639}


def auc(x: np.ndarray, y: np.ndarray) -> float:
    n1 = int(y.sum())
    n0 = len(y) - n1
    if n1 == 0 or n0 == 0:
        return float("nan")
    r = rankdata(x)
    return float((r[y.astype(bool)].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


# ───────────────────────────── логистика с индикаторами пропусков ─────────────────────────────

def logit_fit(X: np.ndarray, y: np.ndarray, lam: float) -> np.ndarray:
    from scipy.optimize import minimize
    X1 = np.c_[np.ones(len(X)), X]

    def f(w):
        z = X1 @ w
        loss = np.logaddexp(0, z) - y * z
        p = 1 / (1 + np.exp(-z))
        return loss.sum() + lam * (w[1:] ** 2).sum() / 2, X1.T @ (p - y) + lam * np.r_[0, w[1:]]

    return minimize(f, np.zeros(X1.shape[1]), jac=True, method="L-BFGS-B").x


class LogitModel:
    """§13.20 п. 3: аддитивная логистика §13.18 (L2, обрезка 1–99 %, масштаб по обучению, λ = max(1; 2·число признаков)); пропуски — медиана обучающей половины
    плюс индикатор пропуска для каждого признака, у которого в обучающей половине есть пропуски. Параметры сериализуются (заморозка)."""

    def fit(self, S: pd.DataFrame, cols: list):
        self.cols = list(cols)
        self.par = {}
        X = []
        for c in self.cols:
            x = S[c].astype(float)
            med = x.median()
            med = 0.0 if np.isnan(med) else float(med)
            xf = x.fillna(med)
            lo, hi = (float(v) for v in xf.quantile([0.01, 0.99]))
            xc = xf.clip(lo, hi)
            mu, sd = float(xc.mean()), float(xc.std() or 1.0)
            p = {"med": med, "lo": lo, "hi": hi, "mu": mu, "sd": sd, "ind": None}
            X.append(((xc - mu) / sd).to_numpy())
            if bool(x.isna().any()):
                m = x.isna().astype(float)
                p["ind"] = [float(m.mean()), float(m.std() or 1.0)]
                X.append(((m - p["ind"][0]) / p["ind"][1]).to_numpy())
            self.par[c] = p
        self.w = logit_fit(np.column_stack(X), S["y"].to_numpy().astype(float), max(1.0, 2.0 * len(self.cols)))
        return self

    def design(self, T: pd.DataFrame) -> np.ndarray:
        X = []
        for c in self.cols:
            p = self.par[c]
            x = T[c].astype(float)
            z = x.fillna(p["med"]).clip(p["lo"], p["hi"])
            X.append(((z - p["mu"]) / p["sd"]).to_numpy())
            if p["ind"] is not None:
                X.append(((x.isna().astype(float) - p["ind"][0]) / p["ind"][1]).to_numpy())
        return np.column_stack(X)

    def score(self, T: pd.DataFrame) -> np.ndarray:
        return np.c_[np.ones(len(T)), self.design(T)] @ self.w

    def to_json(self) -> dict:
        return {"cols": self.cols, "par": self.par, "w": [float(v) for v in self.w]}

    @classmethod
    def from_json(cls, d: dict):
        m = cls()
        m.cols, m.par, m.w = d["cols"], d["par"], np.array(d["w"])
        return m


# ───────────────────────────── бутстреп по монетам (парный) ─────────────────────────────

def coin_index(coins: np.ndarray):
    uc, inv = np.unique(coins, return_inverse=True)
    return uc, inv, [np.flatnonzero(inv == i) for i in range(len(uc))]


def boot(coins, y, d, scores: dict, tops: dict, B: int, rng):
    """Парные пересэмплирования монет: AUC каждой оценки, E1 в верхнем терциле каждой оценки, внутримонетный AUC (веса n1·n0). Возвращает словари массивов [B]."""
    uc, inv, idx = coin_index(coins)
    nc = len(uc)
    # внутримонетный AUC по каждой оценке
    wc = np.zeros(nc)
    ac = {k: np.full(nc, np.nan) for k in scores}
    for i, ix in enumerate(idx):
        n1 = int(y[ix].sum())
        n0 = len(ix) - n1
        if n1 >= 5 and n0 >= 5:
            wc[i] = n1 * n0
            for k, s in scores.items():
                ac[k][i] = auc(s[ix], y[ix])
    out_auc = {k: np.empty(B) for k in scores}
    out_e1 = {k: np.empty(B) for k in tops}
    out_wc = {k: np.empty(B) for k in scores}
    nT = {k: np.bincount(inv[m], minlength=nc).astype(float) for k, m in tops.items()}
    sT = {k: np.bincount(inv[m], weights=d[m], minlength=nc) for k, m in tops.items()}
    for b in range(B):
        sel = rng.integers(0, nc, nc)
        ix = np.concatenate([idx[i] for i in sel])
        W = np.bincount(sel, minlength=nc).astype(float)
        yb = y[ix]
        for k, s in scores.items():
            out_auc[k][b] = auc(s[ix], yb)
            ok = wc > 0
            out_wc[k][b] = (W[ok] * wc[ok] * ac[k][ok]).sum() / (W[ok] * wc[ok]).sum()
        for k in tops:
            den = W @ nT[k]
            out_e1[k][b] = (W @ sT[k]) / den if den > 0 else np.nan
    point_wc = {k: float(np.nansum(wc * ac[k]) / wc.sum()) for k in scores}
    return out_auc, out_e1, out_wc, point_wc


def ci(v: np.ndarray, level: float):
    a = (1 - level) / 2
    return float(np.nanquantile(v, a)), float(np.nanquantile(v, 1 - a))


# ───────────────────────────── основной проход ─────────────────────────────

def main():
    ep_path, flow_path, deriv_path, out = sys.argv[1:5]
    os.makedirs(out, exist_ok=True)
    t0 = time.time()
    raw = pd.read_parquet(ep_path)
    fl = pd.read_parquet(flow_path)
    dv = pd.read_parquet(deriv_path)
    assert len(fl) == len(raw) == len(dv) and fl["ep_id"].is_unique and dv["ep_id"].is_unique, "G7: 1:1 по ep_id"
    raw = raw.merge(fl, on="ep_id", how="left", validate="one_to_one").merge(dv, on="ep_id", how="left", validate="one_to_one")
    df = prepare(raw)
    counts = {s: int((df.side == s).sum()) for s in ("long", "short")}
    print("после prepare:", counts, flush=True)
    g7 = {"rows_joined": int(len(raw)), "analysis": counts, "expected": {"long": 21301, "short": 23762}, "ok": counts == {"long": 21301, "short": 23762}}
    json.dump(g7, open(f"{out}/g7.json", "w"))
    cov_rows, auc_rows, delta_rows, e1_rows, wc_rows, u1_rows, tert_rows, perm_rows, iters = [], [], [], [], [], [], [], [], {}
    primary = {}
    for si, side in enumerate(("long", "short")):
        D = df[df.side == side].reset_index(drop=True)
        halves = {"A": D[D.half == "A"].reset_index(drop=True), "B": D[D.half == "B"].reset_index(drop=True)}
        for h, H in halves.items():
            for c in FLOW + DERIV:
                cov_rows.append({"side": side, "half": h, "feature": c, "n": len(H), "coverage": float(H[c].notna().mean())})
        for di, (sh, th) in enumerate((("A", "B"), ("B", "A"))):
            S, T = halves[sh], halves[th]
            tag = f"{side} {sh}->{th}"
            yT, dT, coins, inpool = T["y"].to_numpy(), T["d"].to_numpy(), T["sym"].to_numpy(), T["in_pool"].to_numpy().astype(bool)
            scores, tops, cuts, models = {}, {}, {}, {}
            for name, cols in SETS.items():
                m3, n_it, its = fit_cb(S, cols, 3)
                s_tr = m3.predict_proba(S[cols].to_numpy())[:, 1]
                s_te = m3.predict_proba(T[cols].to_numpy())[:, 1]
                lm = LogitModel().fit(S, cols)
                lg_tr, lg_te = lm.score(S), lm.score(T)
                for mn, sc_tr, sc_te in (("CB3", s_tr, s_te), ("LOGIT", lg_tr, lg_te)):
                    scores[(name, mn)] = sc_te
                    cut = (float(np.quantile(sc_tr, 1 / 3)), float(np.quantile(sc_tr, 2 / 3)))  # границы терцилей — оценка на обучающей половине
                    cuts[(name, mn)] = cut
                    tops[(name, mn)] = sc_te > cut[1]
                iters[f"{side}|{sh}->{th}|{name}"] = {"iters": int(n_it), "cv_iters": its}
                models[name] = (m3, cols)
                print(tag, name, "CB3 AUC %.4f LOGIT %.4f it=%d  %.0f с" % (auc(s_te, yT), auc(lg_te, yT), n_it, time.time() - t0), flush=True)
            rng = np.random.default_rng([SEED, si, di])
            b_auc, b_e1, b_wc, p_wc = boot(coins, yT, dT, scores, tops, B_BOOT, rng)
            for (name, mn), s in scores.items():
                a = auc(s, yT)
                lo, hi = ci(b_auc[(name, mn)], 0.95)
                auc_rows.append({"side": side, "dir": f"{sh}->{th}", "set": name, "model": mn, "auc": a, "lo95": lo, "hi95": hi, "n_T": len(T), "k_T": int(yT.sum())})
                e1 = float(dT[tops[(name, mn)]].mean())
                e1_rows.append({"side": side, "dir": f"{sh}->{th}", "set": name, "model": mn, "n_top": int(tops[(name, mn)].sum()), "E1_top": e1,
                                "y_top": float(yT[tops[(name, mn)]].mean()), "c_top": float(T["c_f"].to_numpy()[tops[(name, mn)]].mean())})
                wc_rows.append({"side": side, "dir": f"{sh}->{th}", "set": name, "model": mn, "within_coin_auc": p_wc[(name, mn)]})
                mu = tops[(name, mn)] & inpool
                u1_rows.append({"side": side, "dir": f"{sh}->{th}", "set": name, "model": mn, "n_u1": int(inpool.sum()), "k_u1": int(yT[inpool].sum()),
                                "auc_u1": auc(s[inpool], yT[inpool]) if yT[inpool].sum() > 0 else float("nan"), "n_u1_top": int(mu.sum()),
                                "E1_u1_top": float(dT[mu].mean()) if mu.sum() else float("nan")})
            for mn in ("CB3", "LOGIT"):
                for name in ("S1", "S2", "S3"):
                    k1, k0 = (name, mn), ("S0", mn)
                    dv_ = b_auc[k1] - b_auc[k0]
                    dd = auc(scores[k1], yT) - auc(scores[k0], yT)
                    lo95, hi95 = ci(dv_, 0.95)
                    lo975, hi975 = ci(dv_, 0.975)
                    de1 = float(dT[tops[k1]].mean() - dT[tops[k0]].mean())
                    de1_lo, de1_hi = ci(b_e1[k1] - b_e1[k0], 0.95)
                    dwc = p_wc[k1] - p_wc[k0]
                    dwc_lo, dwc_hi = ci(b_wc[k1] - b_wc[k0], 0.95)
                    # ΔAUC на U1r (точка)
                    d_u1 = (auc(scores[k1][inpool], yT[inpool]) - auc(scores[k0][inpool], yT[inpool])) if yT[inpool].sum() > 0 else float("nan")
                    delta_rows.append({"side": side, "dir": f"{sh}->{th}", "set": name, "model": mn, "dAUC": dd, "lo95": lo95, "hi95": hi95, "lo975": lo975, "hi975": hi975,
                                       "dE1_top": de1, "dE1_lo95": de1_lo, "dE1_hi95": de1_hi, "d_within_coin": dwc, "dwc_lo95": dwc_lo, "dwc_hi95": dwc_hi,
                                       "dAUC_u1r": d_u1, "primary": bool(name == "S3" and mn == "CB3")})
            # терцильные таблицы (описательно)
            for name in SETS:
                for mn in ("CB3", "LOGIT"):
                    cut = cuts[(name, mn)]
                    for r in tertile_table(T, scores[(name, mn)], cut, np.random.default_rng([SEED, si, di, 3])):
                        tert_rows.append({"side": side, "dir": f"{sh}->{th}", "set": name, "model": mn, "cut_lo": cut[0], "cut_hi": cut[1], **r})
            # групповая перестановка CB3(S3)
            m3, cols = models["S3"]
            base = auc(scores[("S3", "CB3")], yT)
            groups = {"f12": SETS["S0"], "flow": FLOW, "deriv": DERIV}
            for gname, gcols in groups.items():
                drops = []
                for rep in range(5):
                    Xp = T[cols].copy()
                    perm = np.random.default_rng([SEED, rep, 11]).permutation(len(Xp))
                    Xp[gcols] = Xp[gcols].to_numpy()[perm]
                    drops.append(base - auc(m3.predict_proba(Xp.to_numpy())[:, 1], yT))
                perm_rows.append({"side": side, "dir": f"{sh}->{th}", "group": gname, "auc_drop_mean": float(np.mean(drops)), "auc_drop_sd": float(np.std(drops))})
    A = pd.DataFrame(auc_rows)
    Dl = pd.DataFrame(delta_rows)
    # G5: CB3(S0) против TK-592
    g5 = []
    for _, r in A[(A.set == "S0") & (A.model == "CB3")].iterrows():
        g5.append({"side": r["side"], "dir": r["dir"], "auc": r["auc"], "tk592": TK592_AUC[(r["side"], r["dir"])], "ok": round(r["auc"], 3) == TK592_AUC[(r["side"], r["dir"])]})
    json.dump({"rows": g5, "ok": all(x["ok"] for x in g5)}, open(f"{out}/g5.json", "w"), indent=1)
    # первичный критерий
    for side in ("long", "short"):
        p = Dl[(Dl.side == side) & Dl.primary]
        ab, ba = p[p["dir"] == "A->B"].iloc[0], p[p["dir"] == "B->A"].iloc[0]
        ok = bool(ab.lo975 > 0 and ba.lo975 > 0 and ab.dE1_top > 0 and ba.dE1_top > 0)
        auc_only = bool(ab.lo975 > 0 and ba.lo975 > 0)
        primary[side] = {"passed": ok, "auc_only": auc_only and not ok,
                         "A->B": {"dAUC": ab.dAUC, "lo975": ab.lo975, "hi975": ab.hi975, "dE1_top": ab.dE1_top, "dE1_ci95": [ab.dE1_lo95, ab.dE1_hi95]},
                         "B->A": {"dAUC": ba.dAUC, "lo975": ba.lo975, "hi975": ba.hi975, "dE1_top": ba.dE1_top, "dE1_ci95": [ba.dE1_lo95, ba.dE1_hi95]}}
    json.dump(primary, open(f"{out}/primary.json", "w"), ensure_ascii=False, indent=1)
    A.to_csv(f"{out}/auc.csv", index=False)
    Dl.to_csv(f"{out}/delta.csv", index=False)
    pd.DataFrame(e1_rows).to_csv(f"{out}/e1_top.csv", index=False)
    pd.DataFrame(wc_rows).to_csv(f"{out}/within_coin.csv", index=False)
    pd.DataFrame(u1_rows).to_csv(f"{out}/u1r.csv", index=False)
    pd.DataFrame(tert_rows).to_csv(f"{out}/tertiles.csv", index=False)
    pd.DataFrame(perm_rows).to_csv(f"{out}/perm_groups.csv", index=False)
    pd.DataFrame(cov_rows).to_csv(f"{out}/coverage.csv", index=False)
    json.dump(iters, open(f"{out}/iters.json", "w"), indent=1)
    print(json.dumps(primary, ensure_ascii=False), flush=True)
    print("G5", json.dumps(g5), "готово %.0f с" % (time.time() - t0), flush=True)


if __name__ == "__main__":
    main()
