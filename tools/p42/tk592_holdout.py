"""П-42 §13.18 п. 12 (TK-592): ЕДИНСТВЕННОЕ чтение отложенных суток (14.11 и позже) по замороженным правилам — ничего не подбирается.

Использование (venv с catboost): tk592_holdout.py <hold_u0.parquet> <ep_u0.parquet (обучение)> <каталог пар> <каталог дерева> <выход> <SHA коммита заморозки>
Читает: подтверждённые клетки (cells_confirm.csv, frozen_<сторона>_<S>to<T>.pkl) и модели дерева (models/cb3_*.cbm, frozen_tree.json).
Пишет: holdout.json, holdout_cells.csv, holdout_tree.csv, holdout.md. Запускать один раз, после коммита заморозки: повторный запуск (есть holdout.json или
holdout.started — в том числе после оборванного прогона) отказывает; SHA заморозки пишется в holdout.started и holdout.json.
"""
from __future__ import annotations

import json
import os
import pickle
import re
import sys

import numpy as np
import pandas as pd
from catboost import CatBoostClassifier

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tk592_lib import SEED, prepare  # noqa: E402
from tk592_tree import add_cols, auc, logit_scores, tertile_table  # noqa: E402


def coin_ci(values, coins, B=2000, seed=0):
    """95 % интервал среднего `values` бутстрепом монет целиком."""
    rng = np.random.default_rng([SEED, seed])
    uc, inv = np.unique(coins, return_inverse=True)
    n = np.bincount(inv, minlength=len(uc)).astype(float)
    s = np.bincount(inv, weights=values, minlength=len(uc))
    W = rng.multinomial(len(uc), np.full(len(uc), 1 / len(uc)), size=B).astype(float)
    with np.errstate(invalid="ignore", divide="ignore"):
        v = (W @ s) / (W @ n)
    return float(np.nanquantile(v, 0.025)), float(np.nanquantile(v, 0.975))


def main():
    hold_t, ep_t, pairs_dir, tree_dir, out, freeze_sha = sys.argv[1:7]
    if not re.fullmatch(r"[0-9a-f]{7,40}", freeze_sha):
        sys.exit("нужен SHA коммита заморозки (7–40 hex)")
    os.makedirs(out, exist_ok=True)
    if os.path.exists(f"{out}/holdout.json"):
        sys.exit(f"{out}/holdout.json уже есть: отложенные сутки читаются один раз (§13.18 п. 12)")
    try:
        with open(f"{out}/holdout.started", "x") as f:  # эксклюзивное создание: даже оборванный прогон второй раз не пускает
            f.write(freeze_sha + "\n")
    except FileExistsError:
        sys.exit(f"{out}/holdout.started уже есть: чтение уже начиналось, отложенные сутки читаются один раз (§13.18 п. 12)")
    H = add_cols(prepare(pd.read_parquet(hold_t)))
    TR = add_cols(prepare(pd.read_parquet(ep_t)))
    days = sorted(H["day"].unique().tolist())
    info = {"days": days, "episodes": {s: int((H.side == s).sum()) for s in ("long", "short")}, "breakouts": {s: int(H[H.side == s].y.sum()) for s in ("long", "short")},
            "u1r_episodes": {s: int(((H.side == s) & (H.in_pool == 1)).sum()) for s in ("long", "short")}}
    res = {"freeze_sha": freeze_sha, "info": info, "cells": [], "tree": []}
    # (i) подтверждённые клетки
    try:
        cf = pd.read_csv(f"{pairs_dir}/cells_confirm.csv")
    except pd.errors.EmptyDataError:  # подтверждённых клеток нет — файл пуст (§13.18: шорт-листы пусты)
        cf = pd.DataFrame({"cf_confirmed": pd.Series(dtype=bool)})
    conf = cf[cf.cf_confirmed]
    cell_rows = []
    for r in conf.itertuples():
        sh = r.direction.split("->")[0]
        fz = pickle.load(open(f"{pairs_dir}/frozen_{r.side}_{sh}to{'B' if sh == 'A' else 'A'}.pkl", "rb"))
        D = H[H.side == r.side].reset_index(drop=True)
        if len(D) == 0:
            continue
        add, bk = fz["add"], fz["bk"]
        resid = D["d"].to_numpy() - add.predict(D)
        delta = float(resid.mean())
        c1 = bk[(r.f1, r.scheme)].assign(D[r.f1].to_numpy())
        c2 = bk[(r.f2, r.scheme)].assign(D[r.f2].to_numpy())
        cell = next(c for c in fz["shortlist"] if c["id"] == r.id)
        m = (c1 == cell["b1"]) & (c2 == cell["b2"])
        n = int(m.sum())
        row = {"id": r.id, "direction": r.direction, "side": r.side, "n_H": n, "k_H": int(D["y"].to_numpy()[m].sum()), "sign_M_search": int(np.sign(cell["M"]))}
        if n > 1:
            row.update({"y_share_H": float(D["y"][m].mean()), "c_share_H": float(D["c_f"][m].mean()), "E1_H": float(D["d"][m].mean()), "M_H": float(resid[m].mean() - delta)})
            row["E1_H_ci"] = coin_ci(D["d"].to_numpy()[m], D["sym"].to_numpy()[m], seed=1)
            row["same_sign_E1"] = bool(np.sign(row["E1_H"]) == row["sign_M_search"])
            row["same_sign_M"] = bool(np.sign(row["M_H"]) == row["sign_M_search"])
            mu = m & (D["in_pool"].to_numpy() == 1)
            row["u1_n"], row["u1_k"] = int(mu.sum()), int(D["y"].to_numpy()[mu].sum())
            row["u1_E1"] = float(D["d"].to_numpy()[mu].mean()) if mu.sum() else None
        cell_rows.append(row)
    pd.DataFrame(cell_rows).to_csv(f"{out}/holdout_cells.csv", index=False)
    res["cells"] = cell_rows
    # (ii) деревья
    frozen = json.load(open(f"{tree_dir}/frozen_tree.json"))
    tree_rows = []
    for key, fz in sorted(frozen.items()):
        side, sh, name = key.split("_", 2)
        D = H[H.side == side].reset_index(drop=True)
        if len(D) == 0:
            continue
        cols = fz["cols"]
        m = CatBoostClassifier()
        m.load_model(f"{tree_dir}/models/cb3_{side}_{sh}_{name}.cbm")
        s = m.predict_proba(D[cols].to_numpy())[:, 1]
        yH = D["y"].to_numpy()
        rng = np.random.default_rng([SEED, sum(map(ord, key)) % 1000])
        tt = tertile_table(D, s, (fz["cut_lo"], fz["cut_hi"]), rng)
        # логистика (та же половина обучения) и «только оборот»
        S = TR[(TR.side == side) & (TR.half == sh)].reset_index(drop=True)
        (s_log,), _ = logit_scores(S, [D], cols)
        (s_turn,), _ = logit_scores(S, [D], ["lturn24"])
        tree_rows.append({"side": side, "train_half": sh, "set": name, "model": "CB3", "auc": auc(s, yH), "auc_logit": auc(s_log, yH), "auc_turn": auc(s_turn, yH),
                          "n": len(D), "k": int(yH.sum()), "tertiles": tt})
        for t in tt:
            print(side, sh, name, t["tertile"], t["n"], t["k"], "y %.3f c %.3f E1 %+.3f" % (t["y_share"], t["c_share"], t["E1"]), flush=True)
    res["tree"] = tree_rows
    json.dump(res, open(f"{out}/holdout.json", "w"), ensure_ascii=False, indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o))
    rows = []
    for t in tree_rows:
        for q in t["tertiles"]:
            rows.append({"side": t["side"], "train_half": t["train_half"], "set": t["set"], "tertile": q["tertile"], **{k: q.get(k) for k in ("n", "k", "y_share", "c_share", "E1", "E1_lo", "E1_hi", "u1_n", "u1_k", "u1_E1")},
                         "auc": t["auc"], "auc_logit": t["auc_logit"], "auc_turn": t["auc_turn"]})
    pd.DataFrame(rows).to_csv(f"{out}/holdout_tree.csv", index=False)
    print(json.dumps(info, ensure_ascii=False))


if __name__ == "__main__":
    main()
