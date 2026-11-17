"""П-42 §13.18 (TK-592): LightGBM — СПРАВОЧНО, не входит в вердикт. Добавлено после предрегистрации по просьбе координатора (отступление, пометить в отчёте).

Те же наборы признаков, половины и сторона, что у `tk592_tree.py`; настройки как у CatBoost: глубина 3 и 1 (`num_leaves` 8 / 2), `min_data_in_leaf` 300,
`learning_rate` 0,05, `lambda_l2` 3, зерно 592, ранняя остановка по 5 группам суток внутри половины обучения (то же `day_groups`, эмбарго 1 сутки).
Использование: <venv>/bin/python tk592_lgbm.py <ep_u0.parquet> <каталог пар (core.json)> <каталог дерева (tree_auc.csv)> <каталог выхода>
Выход: lgbm_auc.csv (AUC вне выборки A→B, B→A) и lgbm_side_by_side.csv (LGBM3/LGBM1 рядом с CB3/CB1/LOGIT/TURN из tree_auc.csv).
"""
from __future__ import annotations

import json
import os
import sys
import time

import lightgbm as lgb
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tk592_lib import FEATS, SEED, prepare  # noqa: E402
from tk592_tree import F48, add_cols, auc, day_groups  # noqa: E402

PARAMS = dict(objective="binary", learning_rate=0.05, lambda_l2=3.0, min_data_in_leaf=300, seed=SEED, deterministic=True, force_row_wise=True,
              num_threads=4, verbosity=-1)
ITER_CAP = 2000
LEAVES = {3: 8, 1: 2}


def fit_lgb(S: pd.DataFrame, cols: list, depth: int):
    p = dict(PARAMS, max_depth=depth, num_leaves=LEAVES[depth])
    its = []
    for gk in day_groups(S):
        lo, hi = min(gk), max(gk)
        va = S["day"].isin(gk).to_numpy()
        tr = (~va) & ~S["day"].isin([lo - 1, hi + 1]).to_numpy()
        dtr = lgb.Dataset(S.loc[tr, cols].to_numpy(), S.loc[tr, "y"].to_numpy())
        dva = lgb.Dataset(S.loc[va, cols].to_numpy(), S.loc[va, "y"].to_numpy(), reference=dtr)
        m = lgb.train(p, dtr, ITER_CAP, valid_sets=[dva], callbacks=[lgb.early_stopping(50, verbose=False)])
        its.append(int(m.best_iteration))
    n_it = max(20, int(round(float(np.mean(its)))))
    m = lgb.train(p, lgb.Dataset(S[cols].to_numpy(), S["y"].to_numpy()), n_it)
    return m, n_it, its


def main():
    table, pairs_dir, tree_dir, out = sys.argv[1:5]
    os.makedirs(out, exist_ok=True)
    core = json.load(open(f"{pairs_dir}/core.json"))["core_used"]
    df = add_cols(prepare(pd.read_parquet(table)))
    sets = {"f12": list(FEATS)}
    if sorted(core) != sorted(FEATS):
        sets = {"core": list(core), **sets}
    sets["f48"] = [c for c in F48 if c in df.columns]
    rows = []
    t0 = time.time()
    for side in ("long", "short"):
        D = df[df.side == side].reset_index(drop=True)
        halves = {"A": D[D.half == "A"].reset_index(drop=True), "B": D[D.half == "B"].reset_index(drop=True)}
        for sh, th in (("A", "B"), ("B", "A")):
            S, T = halves[sh], halves[th]
            yT = T["y"].to_numpy()
            for name, cols in sets.items():
                for depth in (3, 1):
                    m, n_it, its = fit_lgb(S, cols, depth)
                    s = m.predict(T[cols].to_numpy())
                    rows.append({"side": side, "dir": f"{sh}->{th}", "set": name, "model": f"LGB{depth}", "auc": auc(s, yT), "n_T": len(T), "k_T": int(yT.sum()),
                                 "extra": json.dumps({"iters": n_it, "cv_iters": its})})
                    print(side, f"{sh}->{th}", name, f"LGB{depth}", "AUC %.3f it=%d  %.0f с" % (rows[-1]["auc"], n_it, time.time() - t0), flush=True)
    L = pd.DataFrame(rows)
    L.to_csv(f"{out}/lgbm_auc.csv", index=False)
    C = pd.read_csv(f"{tree_dir}/tree_auc.csv")
    A = pd.concat([C, L], ignore_index=True)
    wide = A.pivot_table(index=["side", "dir", "set"], columns="model", values="auc").reset_index()
    wide.to_csv(f"{out}/lgbm_side_by_side.csv", index=False)
    print("готово", time.time() - t0, flush=True)


if __name__ == "__main__":
    main()
