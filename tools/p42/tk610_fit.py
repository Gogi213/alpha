"""П-42 §13.27 (TK-610): подгонка CatBoost (цель T1, f12) на окне поиска, фолдовые модели, оценка кандидатов, порог `q`, таблицы `score610` для движка.

  python3 tk610_fit.py fit --side L|S --train <train-….parquet …> --out <каталог> [--procs 2] [--dry]
      сетка 12 клеток × 4 блока (Б1…Б4; очистка + зона августа), выбор клетки по средней потере блоков, итерации = округлённое среднее лучших (≥ 10), четыре фолдовые
      модели на фиксированных итерациях → `<out>/models/<S>_f<k>.cbm`, `fit-<S>.json`; важности (SHAP, перестановочная) — описательно. Читает только строки блоков поиска (G6).
      `--dry` (сухой прогон механики): блоки без строк пропускаются, минимум строк не требуется.
  python3 tk610_fit.py score --side L|S --fit-dir <каталог> --cand <cand-….parquet …> --out <каталог> [--cstar <json>] [--union-root /data/p42-tk599/ftab] [--dry]
      `ŝ` каждого кандидата потока R1 (OOF по блоку поиска; август — среднее четырёх моделей), `q` = квантиль 2/3 OOF в зачётных диапазонах окон, слияние с фильтром F C*
      в один столбец `score610`, `<out>/ftab/<окно>/<S>-score610.csv`, `q-<S>.json` (доли и числа по окнам и августу — для G8).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import retitr_ftab as rfx  # noqa: E402
import retitr_full as rf  # noqa: E402
import tk598_lib as L598  # noqa: E402
import tk610_lib as T  # noqa: E402

CB_BASE = dict(learning_rate=0.05, grow_policy="Depthwise", random_seed=T.SEED, thread_count=1, verbose=0, allow_writing_files=False)
G: dict = {}       # данные стороны (до создания пула процессов — fork)
MIN_FOLD_ROWS = 10


def make_model(cell, iterations, early):
    from catboost import CatBoostRegressor
    depth, mdl, l2 = cell
    p = dict(CB_BASE, depth=depth, min_data_in_leaf=mdl, l2_leaf_reg=l2, iterations=iterations)
    if early:
        p.update(od_type="Iter", od_wait=T.OD_WAIT)
    return CatBoostRegressor(loss_function="RMSE", **p)


def xmat(df):
    return df[T.FEATS].to_numpy(np.float64)


def load_train(paths):
    """Строки обучения окон: только блоки поиска (G6), ключ `(sym, level, a_sig)` уникален."""
    df = pd.concat([pd.read_parquet(p) for p in paths], ignore_index=True)
    T.assert_search_days(df["aday"].to_numpy(), "строки обучения")
    if df.duplicated(["sym", "level", "a_sig"]).any():
        raise T.GuardError("строки обучения: ключ (sym, level, a_sig) не уникален")
    return df


def fold_data(U, blk, k):
    """Обучающая и проверочная части фолда `k`: метка `net_nf`, обрезка 1 % / 99 % по ОБУЧАЮЩЕЙ части (потеря — на обрезанной метке проверки, как §13.23)."""
    tr = T.fold_train_idx(U["aday"].to_numpy(), U["a_entry"].to_numpy(), U["a_exit"].to_numpy(), k)
    va = np.nonzero(blk == k)[0]
    y = U["net_nf"].to_numpy(float)
    ok = ~np.isnan(y)
    tr, va = tr[ok[tr]], va[ok[va]]
    if len(tr) < MIN_FOLD_ROWS or len(va) < 1:
        raise T.GuardError("фолд %d: обучающих строк %d, проверочных %d" % (k, len(tr), len(va)))
    lo, hi = L598.clip_bounds(y[tr])
    return tr, va, np.clip(y[tr], lo, hi), np.clip(y[va], lo, hi), (lo, hi)


def cv_task(args):
    cell, k = args
    tr, va, ytr, yva, _ = fold_data(G["U"], G["blk"], k)
    X = G["X"]
    from catboost import Pool
    m = make_model(cell, T.ITER_CAP, True)
    m.fit(Pool(X[tr], label=ytr), eval_set=Pool(X[va], label=yva), use_best_model=True)
    loss = float(np.sqrt(np.mean((m.predict(X[va]) - yva) ** 2)))
    return cell, k, loss, int(m.get_best_iteration()) + 1, int(len(tr)), int(len(va))


def refit_task(args):
    cell, n_it, k = args
    from catboost import Pool
    tr, _, ytr, _, bounds = fold_data(G["U"], G["blk"], k)
    m = make_model(cell, n_it, False)
    m.fit(Pool(G["X"][tr], label=ytr))
    return k, m, bounds, int(len(tr))


def pmap(fn, tasks, procs, chunksize=1):
    if procs <= 1:
        return [fn(t) for t in tasks]
    import multiprocessing
    with ProcessPoolExecutor(procs, mp_context=multiprocessing.get_context("fork")) as ex:
        return list(ex.map(fn, tasks, chunksize=chunksize))


def _jd(o):
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return float(o)
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(type(o))


def fit_side(side, train_paths, out, procs, dry=False):
    os.makedirs(os.path.join(out, "models"), exist_ok=True)
    U = load_train(train_paths)
    blk = T.block_of_abs(U["aday"].to_numpy())
    n_blk = [int((blk == k).sum()) for k in range(len(T.BLOCKS))]
    active = [k for k in range(len(T.BLOCKS)) if n_blk[k] > 0]
    res = {"side": side, "dry": bool(dry), "rows_U0": int(len(U)), "rows_per_block": n_blk, "active_blocks": active, "blocks": T.BLOCKS, "seed": T.SEED}
    need_ok = (len(active) >= 2) if dry else (len(active) == len(T.BLOCKS) and len(U) >= T.MIN_ROWS)
    if not need_ok:
        res["undefined"] = "строк U0* %d (блоки %s), минимум %d в 4 блоках: подбор не определён" % (len(U), n_blk, T.MIN_ROWS)
        with open(os.path.join(out, "fit-%s.json" % side), "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
        print(res["undefined"])
        return res
    import catboost
    res["catboost"] = catboost.__version__
    G["U"], G["X"], G["blk"] = U, xmat(U), blk
    res["train_rows_per_fold"] = {k: int(len(T.fold_train_idx(U["aday"].to_numpy(), U["a_entry"].to_numpy(), U["a_exit"].to_numpy(), k))) for k in active}
    res["zone_rows_excluded"] = int(T.zone_hit(U["a_entry"], U["a_exit"]).sum())
    res["sigma_net_U0"] = float(U["net_nf"].std(ddof=1))
    t_start = time.time()
    tasks = [(cell, k) for cell in T.GRID for k in active]
    cv = {}
    for cell, k, loss, bi, ntr, nva in pmap(cv_task, tasks, procs, chunksize=2):
        cv.setdefault(cell, []).append((k, loss, bi, ntr, nva))
    table = []
    for cell in T.GRID:
        rows = sorted(cv[cell])
        table.append({"cell": list(cell), "loss_mean": float(np.mean([r[1] for r in rows])), "loss_folds": {r[0]: r[1] for r in rows},
                      "best_iter": {r[0]: r[2] for r in rows}, "n_val": {r[0]: r[4] for r in rows}})
    best = min(range(len(table)), key=lambda i: table[i]["loss_mean"])
    cell = tuple(table[best]["cell"])
    n_it = max(T.MIN_ITERS, int(round(float(np.mean(list(table[best]["best_iter"].values()))))))
    res.update({"cv": table, "chosen": list(cell), "iterations": n_it, "cv_seconds": time.time() - t_start})
    print("CV: %d подгонок, клетка %s, итераций %d, %.0f с" % (len(tasks), cell, n_it, time.time() - t_start), flush=True)
    models, folds = {}, []
    for k, m, bounds, ntr in pmap(refit_task, [(cell, n_it, k) for k in active], procs):
        p = os.path.join(out, "models", "%s_f%d.cbm" % (side, k))
        m.save_model(p)
        models[k] = m
        folds.append({"block": k, "file": os.path.basename(p), "sha256": T.sha256_file(p), "clip": list(bounds), "n_train": ntr})
    res["folds"] = folds
    res["importance"] = importances(U, blk, models, active, cell, n_it)
    res["elapsed_s"] = time.time() - t_start
    with open(os.path.join(out, "fit-%s.json" % side), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1, default=_jd)
    return res


def importances(U, blk, models, active, cell, n_it):
    """Описательно: перестановочная важность по проверочным блокам (рост RMSE при перестановке столбца, 3 повтора) и SHAP финальной модели на всех обучающих строках."""
    X = G["X"]
    rng = np.random.default_rng(T.SEED)
    perm = {f: [] for f in T.FEATS}
    for k in active:
        va = np.nonzero(blk == k)[0]
        y = U["net_nf"].to_numpy(float)[va]
        ok = ~np.isnan(y)
        va, y = va[ok], y[ok]
        base = float(np.sqrt(np.mean((models[k].predict(X[va]) - y) ** 2)))
        for j, f in enumerate(T.FEATS):
            d = []
            for _ in range(3):
                Xp = X[va].copy()
                Xp[:, j] = rng.permutation(Xp[:, j])
                d.append(float(np.sqrt(np.mean((models[k].predict(Xp) - y) ** 2))) - base)
            perm[f].append(float(np.mean(d)))
    out = {"perm_rmse_increase": {f: float(np.mean(v)) for f, v in perm.items()}}
    try:
        from catboost import Pool
        keep = np.nonzero(~T.zone_hit(U["a_entry"], U["a_exit"]) & ~np.isnan(U["net_nf"].to_numpy(float)))[0]
        y = U["net_nf"].to_numpy(float)[keep]
        lo, hi = L598.clip_bounds(y)
        fm = make_model(cell, n_it, False)
        fm.fit(Pool(X[keep], label=np.clip(y, lo, hi)))
        sv = fm.get_feature_importance(Pool(X[keep][:3000], label=np.clip(y, lo, hi)[:3000]), type="ShapValues")
        out["shap_mean_abs"] = {f: float(np.abs(sv[:, i]).mean()) for i, f in enumerate(T.FEATS)}
    except Exception as e:   # noqa: BLE001
        out["shap_error"] = str(e)[:200]
    return out


# ───────────────────────────── оценка кандидатов ─────────────────────────────

def load_models(fit_dir, side):
    """Фолдовые модели по `fit-<S>.json` с проверкой sha256: {блок: модель}."""
    from catboost import CatBoostRegressor
    fit = json.load(open(os.path.join(fit_dir, "fit-%s.json" % side), encoding="utf-8"))
    if "undefined" in fit:
        raise T.GuardError("подбор стороны %s не определён: %s" % (side, fit["undefined"]))
    models = {}
    for f in fit["folds"]:
        p = os.path.join(fit_dir, "models", f["file"])
        if T.sha256_file(p) != f["sha256"]:
            raise T.GuardError("sha256 модели не совпал: %s" % f["file"])
        m = CatBoostRegressor()
        m.load_model(p)
        models[f["block"]] = m
    return fit, models


def f_values(union_path, fa, syms, sig_ms):
    """Значения признака F у кандидатов по таблице TK-599: (значения с NaN у «нет ключа» и пустых, число кандидатов без ключа)."""
    s, ms, cols = rfx.read_ftab(union_path)
    if fa not in cols:
        raise T.GuardError("в таблице %s нет столбца %s" % (union_path, fa))
    tab = pd.DataFrame({"sym": s, "sig_ms": ms, "v": cols[fa]})
    if tab.duplicated(["sym", "sig_ms"]).any():
        raise T.GuardError("таблица %s: ключ (sym, sig_ms) не уникален" % union_path)
    q = pd.DataFrame({"sym": list(syms), "sig_ms": np.asarray(sig_ms, np.int64)}).merge(tab, on=["sym", "sig_ms"], how="left", indicator=True, validate="many_to_one")
    return q["v"].to_numpy(float), int((q["_merge"] == "left_only").sum())


def score_side(side, fit_dir, cand_paths, out, cstar=None, union_root="/data/p42-tk599/ftab", dry=False):
    fit, models = load_models(fit_dir, side)
    flt = T.load_cstar(cstar)[side]["filter"] if cstar else None
    parts = []
    for p in cand_paths:
        C = pd.read_parquet(p)
        X = xmat(C)
        preds = {k: m.predict(X) for k, m in models.items()}
        blk = T.block_of_abs(C["aday"].to_numpy())
        C["shat"] = T.pick_scores(blk, preds, strict=not dry)
        C["block"] = blk
        parts.append(C)
    C = pd.concat(parts, ignore_index=True)
    # порог: внефолдовые ŝ кандидатов R1 в зачётных диапазонах окон поиска (кандидаты перекрытий окон не дублируются: диапазоны не пересекаются)
    cr = np.zeros(len(C), bool)
    for w in rf.WINDOWS:
        lo, hi = T.credit_days(w)
        cr |= (C["win"].to_numpy() == w.name) & (C["aday"].to_numpy() >= lo) & (C["aday"].to_numpy() <= hi)
    oof = cr & (C["block"].to_numpy() >= 0)
    q = T.threshold_q(C["shat"].to_numpy()[oof])
    res = {"side": side, "q": q, "n_oof": int(oof.sum()), "filter": flt, "dry": bool(dry), "by_win": {}}
    for w in rf.WINDOWS:
        sub = C[C["win"] == w.name]
        if not len(sub):
            continue
        shat = sub["shat"].to_numpy()
        if flt is not None:
            vals, no_key = f_values(os.path.join(union_root, w.name, "%s-union.csv" % side), flt["fa"], sub["sym"], sub["sig_ms"])
            passed = T.f_pass(vals, flt["fo"], float(flt["fv"]))
        else:
            no_key, passed = 0, np.ones(len(sub), bool)
        col = T.merge_score(shat, passed)
        d = os.path.join(out, "ftab", w.name)
        os.makedirs(d, exist_ok=True)
        rfx.write_ftab(os.path.join(d, "%s-score610.csv" % side), list(sub["sym"]), sub["sig_ms"].to_numpy(np.int64), [T.SCORE_COL], [col])
        aug = T.is_aug(sub["aday"].to_numpy())
        lo, hi = T.credit_days(w)
        inwin = (sub["aday"].to_numpy() >= lo) & (sub["aday"].to_numpy() <= hi) & ~aug
        res["by_win"][w.name] = {"cands": int(len(sub)), "no_key_F": no_key, "F_dropped": int((~passed).sum()), "nan_shat": int(np.isnan(shat).sum()),
                                 "share_ge_q_all": T.selected_share(shat, q), "share_ge_q_passF_in_credit_search": T.selected_share(col[inwin], q) if inwin.any() else None,
                                 "aug_cands": int(aug.sum()), "aug_ge_q": int((aug & (shat >= q)).sum()), "aug_ge_q_passF": int((aug & (col >= q)).sum())}
    res["aug_cands"] = sum(v["aug_cands"] for v in res["by_win"].values())
    res["aug_ge_q"] = sum(v["aug_ge_q"] for v in res["by_win"].values())
    res["aug_ge_q_passF"] = sum(v["aug_ge_q_passF"] for v in res["by_win"].values())
    os.makedirs(out, exist_ok=True)
    pd.DataFrame({"win": C["win"], "sym": C["sym"], "level": C["level"], "sig_ms": C["sig_ms"], "aday": C["aday"], "block": C["block"], "shat": C["shat"]}).to_parquet(
        os.path.join(out, "shat-%s.parquet" % side))
    with open(os.path.join(out, "q-%s.json" % side), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1, default=_jd)
    print(json.dumps({k: res[k] for k in ("side", "q", "n_oof", "aug_cands", "aug_ge_q", "aug_ge_q_passF")}, ensure_ascii=False))
    return res


def main(argv=None):
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    f = sp.add_parser("fit")
    f.add_argument("--side", required=True, choices=list(rf.SIDES))
    f.add_argument("--train", required=True, nargs="+")
    f.add_argument("--out", required=True)
    f.add_argument("--procs", type=int, default=2)
    f.add_argument("--dry", action="store_true")
    s = sp.add_parser("score")
    s.add_argument("--side", required=True, choices=list(rf.SIDES))
    s.add_argument("--fit-dir", required=True)
    s.add_argument("--cand", required=True, nargs="+")
    s.add_argument("--out", required=True)
    s.add_argument("--cstar")
    s.add_argument("--union-root", default="/data/p42-tk599/ftab")
    s.add_argument("--dry", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "fit":
        fit_side(a.side, a.train, a.out, a.procs, a.dry)
    else:
        score_side(a.side, a.fit_dir, a.cand, a.out, a.cstar, a.union_root, a.dry)


if __name__ == "__main__":
    main()
