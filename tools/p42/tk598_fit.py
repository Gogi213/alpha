"""П-42 §13.23 (TK-598), шаги 1–2: подгонка CatBoost на окне F (сутки 14…73), блочная OOF-проверка, границы «третей», ворота OOF, заморозка.

  python3 tk598_fit.py fit  --side long|short --u0 <tab-F-U0.parquet> --u1 <tab-F-U1.parquet> --out <каталог> [--procs 6]
      Подбор по сетке (12 клеток: depth {1,2,3} × min_data_in_leaf {50,200} × l2_leaf_reg {3,10}) блочной CV по суткам (5 блоков по 12 суток, очистка purge, п. 4):
      критерий — средняя по блокам потеря на строках U0 проверочного блока (RMSE / Logloss с весами / −NDCG для T6; T5 — две модели, клетка выбирается для каждой отдельно);
      iterations = округлённое среднее лучших итераций (≥ 10); пять фолдовых моделей выбранной клетки на фиксированных iterations (без ранней остановки) → OOF-предсказания U1-F
      → граница `q`; ворота OOF (S1_OOF > 0, выбранных ≥ L.MIN_SEL = 30 по §13.23.3); финальная модель на всём U0-F (справочно); важности (SHAP, перестановочная).
      Метки валидации обрезаются теми же границами 1 % / 99 %, что и обучение (потеря модели считается на обрезанной метке; стратегия S1 / S2 — на необрезанной `net_nf`).
  python3 tk598_fit.py freeze --fit-dir <каталог> --out <каталог заморозки>
      собирает `frozen.json`: цели, прошедшие ворота OOF, `m` по сторонам, sha256 моделей, границ и кода; печатает сводку.

Запечатанность (G-S): загружаются только строки U0 / U1 с сутками сигнала 14…73 (иначе — остановка); файлов окон июль / август / 14–16.11 скрипт не открывает.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk598_lib as L  # noqa: E402

CB_BASE = dict(learning_rate=0.05, grow_policy="Depthwise", random_seed=L.SEED, thread_count=1, verbose=0, allow_writing_files=False)
ITER_CAP = 1000
FINAL_CUTOFF_DAYS = 74   # конец окна F (сутки 14…73): сделки, закрытые позже, в обучение не идут (ни в фолдах, ни в финальной модели)
OD_WAIT = 50
MIN_ITERS = 10
# цель → подмодели: (имя, вид); вид: rmse | wlogloss | logloss | rank
SPECS = {"T1": [("net", "rmse")], "T2": [("bps", "rmse")], "T3": [("win", "wlogloss")], "T4": [("stop", "logloss")],
         "T5": [("mfe", "rmse"), ("mae", "rmse")], "T6": [("rank", "rank")]}
LABEL_COL = {"net": "net_nf", "bps": "bps", "win": "y_win", "stop": "y_stop", "mfe": "mfe_natr", "mae": "mae_natr", "rank": "net_nf"}
G: dict = {}    # данные стороны (заполняются до создания пула процессов — fork)


# ───────────────────────────── данные ─────────────────────────────

def load_side(u0_path, u1_path):
    u0, u1 = pd.read_parquet(u0_path), pd.read_parquet(u1_path)
    for name, d in (("U0", u0), ("U1", u1)):
        if len(d) and (d["day"].min() < 14 or d["day"].max() > 73):
            raise SystemExit(f"G-S: {name} содержит сутки вне 14…73 ({d['day'].min()}…{d['day'].max()})")
    return u0.reset_index(drop=True), u1.reset_index(drop=True)


def xmat(df):
    return df[L.FEATS].to_numpy(np.float64)


def prep(U0, sub, tr_idx, va_idx):
    """Обучающая и проверочная части подмодели: метки с обрезкой / весами / релевантностью по ОБУЧАЮЩЕЙ части; строки без метки отбрасываются."""
    kind = dict(sum(SPECS.values(), []))[sub]
    col = LABEL_COL[sub]
    y = U0[col].to_numpy(float)
    ok = ~np.isnan(y)
    tr, va = tr_idx[ok[tr_idx]], va_idx[ok[va_idx]]
    meta = {}
    net = U0["net_nf"].to_numpy(float)
    if kind == "rmse":
        lo, hi = L.clip_bounds(y[tr])
        meta["clip"] = [lo, hi]
        ytr, yva = np.clip(y[tr], lo, hi), np.clip(y[va], lo, hi)
        wtr = wva = None
    elif kind == "wlogloss":
        a = np.abs(net)
        m = float(a[tr].mean())
        meta["w_mean"] = m
        w = a / m if m > 0 else np.ones_like(a)
        ytr, yva, wtr, wva = y[tr], y[va], w[tr], w[va]
    elif kind == "logloss":
        ytr, yva, wtr, wva = y[tr], y[va], None, None
    else:   # rank: релевантность — квинтиль net_nf по обучающей части; группа — сутки входа; группы из одной сделки отбрасываются
        bounds = L.quintile_bounds(net[tr])
        meta["quintile_bounds"] = bounds
        rel = L.quintile_rel(net, bounds).astype(float)
        g = U0["entry_day_abs"].to_numpy(np.int64)
        tr = tr[np.isin(g[tr], _multi(g[tr]))]
        va = va[np.isin(g[va], _multi(g[va]))]
        tr, va = tr[np.argsort(g[tr], kind="stable")], va[np.argsort(g[va], kind="stable")]
        ytr, yva, wtr, wva = rel[tr], rel[va], None, None
    return dict(kind=kind, tr=tr, va=va, ytr=ytr, yva=yva, wtr=wtr, wva=wva, meta=meta,
                gtr=U0["entry_day_abs"].to_numpy(np.int64)[tr] if kind == "rank" else None,
                gva=U0["entry_day_abs"].to_numpy(np.int64)[va] if kind == "rank" else None)


def _multi(g):
    v, c = np.unique(g, return_counts=True)
    return v[c >= 2]


# ───────────────────────────── модель и потеря ─────────────────────────────

def make_model(kind, cell, iterations, early):
    from catboost import CatBoostClassifier, CatBoostRanker, CatBoostRegressor
    depth, mdl, l2 = cell
    p = dict(CB_BASE, depth=depth, min_data_in_leaf=mdl, l2_leaf_reg=l2, iterations=iterations)
    if early:
        p.update(od_type="Iter", od_wait=OD_WAIT)
    if kind == "rmse":
        return CatBoostRegressor(loss_function="RMSE", **p)
    if kind in ("logloss", "wlogloss"):
        return CatBoostClassifier(loss_function="Logloss", **p)
    return CatBoostRanker(loss_function="YetiRank", eval_metric="NDCG", **p)


def make_pool(X, y, w=None, g=None):
    from catboost import Pool
    return Pool(X, label=y, weight=w, group_id=g)


def predict(model, kind, X):
    if kind in ("logloss", "wlogloss"):
        return model.predict_proba(X)[:, 1]
    return model.predict(X)


def val_loss(kind, pred, y, w=None, g=None):
    """Потеря на проверке: RMSE; Logloss (с весами для T3); −NDCG для T6 (меньше — лучше)."""
    if kind == "rmse":
        return float(np.sqrt(np.mean((pred - y) ** 2)))
    if kind in ("logloss", "wlogloss"):
        p = np.clip(pred, 1e-15, 1 - 1e-15)
        ll = -(y * np.log(p) + (1 - y) * np.log(1 - p))
        return float(np.average(ll, weights=w)) if w is not None else float(ll.mean())
    from catboost.utils import eval_metric
    return -float(eval_metric(y, pred, "NDCG", group_id=g)[0])


def train_idx(U0, k):
    """Обучающие строки фолда `k`: purge по блоку + только сделки, закрытые до конца окна F (`exit_ms` < 74 сут., как у финальной модели); возвращает (индексы, число отброшенных этим вторым условием)."""
    tr = L.purge_train(U0["day"].to_numpy(), U0["entry_ms"].to_numpy(), U0["exit_ms"].to_numpy(), L.F_BLOCKS[k])
    ok = U0["exit_ms"].to_numpy()[tr] < FINAL_CUTOFF_DAYS * L.DAY
    return tr[ok], int((~ok).sum())


def cv_task(args):
    """Одна подгонка (подмодель, клетка, блок): потеря на проверке при лучшей итерации и лучшая итерация."""
    sub, cell, k = args
    U0, blk = G["U0"], G["blk"]
    va_idx = np.nonzero(blk == k)[0]
    tr_idx = train_idx(U0, k)[0]
    D = prep(U0, sub, tr_idx, va_idx)
    X = G["X"]
    m = make_model(D["kind"], cell, ITER_CAP, True)
    m.fit(make_pool(X[D["tr"]], D["ytr"], D["wtr"], D["gtr"]), eval_set=make_pool(X[D["va"]], D["yva"], D["wva"], D["gva"]), use_best_model=True)
    pred = predict(m, D["kind"], X[D["va"]])
    return sub, cell, k, val_loss(D["kind"], pred, D["yva"], D["wva"], D["gva"]), int(m.get_best_iteration()) + 1, int(len(D["tr"])), int(len(D["va"]))


def refit_task(args):
    """Фолдовая модель выбранной клетки на фиксированных iterations; возвращает модель в виде байтов .cbm и метаданные."""
    sub, cell, n_it, k = args
    U0, blk = G["U0"], G["blk"]
    va_idx = np.nonzero(blk == k)[0]
    tr_idx = train_idx(U0, k)[0]
    D = prep(U0, sub, tr_idx, va_idx)
    m = make_model(D["kind"], cell, n_it, False)
    m.fit(make_pool(G["X"][D["tr"]], D["ytr"], D["wtr"], D["gtr"]))
    return sub, k, m, D["meta"], int(len(D["tr"]))


# ───────────────────────────── подгонка ─────────────────────────────

def fit_side(side, u0_path, u1_path, out, procs):
    os.makedirs(out, exist_ok=True)
    mdir = os.path.join(out, "models")
    os.makedirs(mdir, exist_ok=True)
    U0, U1 = load_side(u0_path, u1_path)
    res = {"side": side, "rows_U0": int(len(U0)), "rows_U1": int(len(U1)), "targets": {}}
    if len(U0) < L.MIN_ROWS_U0:
        res["undefined"] = f"строк U0-F {len(U0)} < {L.MIN_ROWS_U0}: подбор не определён"
        with open(os.path.join(out, f"fit-{side}.json"), "w", encoding="utf-8") as _f:
            json.dump(res, _f, ensure_ascii=False, indent=1)
        print(res["undefined"])
        return res
    G["U0"], G["X"], G["blk"] = U0, xmat(U0), L.block_of(U0["day"].to_numpy())
    X1 = xmat(U1)
    blk1 = L.block_of(U1["day"].to_numpy())
    if (G["blk"] < 0).any() or (blk1 < 0).any():
        raise SystemExit("строки вне блоков CV")
    import catboost
    res["catboost"] = catboost.__version__
    res["blocks"] = L.F_BLOCKS
    res["n_per_block_U0"] = [int((G["blk"] == k).sum()) for k in range(5)]
    res["train_rows_per_fold"] = [int(len(train_idx(U0, k)[0])) for k in range(5)]
    res["train_dropped_exit_ge74_per_fold"] = [train_idx(U0, k)[1] for k in range(5)]
    print(f"обучение по фолдам: строк {res['train_rows_per_fold']}, отброшено exit ≥ {FINAL_CUTOFF_DAYS} сут.: {res['train_dropped_exit_ge74_per_fold']}", flush=True)
    res["n_per_block_U1"] = [int((blk1 == k).sum()) for k in range(5)]
    stops = int(U0["y_stop"].sum())
    res["stops_U0"] = stops
    res["sigma_net_U1"] = float(U1["net_nf"].std(ddof=1))
    res["trades_per_day_U1"] = float(len(U1) / 60)
    tnames = L.TARGETS[side]
    subs = [(t, sub, kind) for t in tnames for sub, kind in SPECS[t]]
    mp = __import__("multiprocessing").get_context("fork")
    t_start = time.time()
    # --- 1. CV по сетке ---
    tasks = [(sub, cell, k) for _t, sub, _kind in subs for cell in L.GRID for k in range(5)]
    cv = {}
    with ProcessPoolExecutor(procs, mp_context=mp) as ex:
        for sub, cell, k, loss, bi, ntr, nva in ex.map(cv_task, tasks, chunksize=4):
            cv.setdefault((sub, cell), []).append((k, loss, bi, ntr, nva))
    print(f"CV: {len(tasks)} подгонок, {time.time() - t_start:.0f} с", flush=True)
    chosen = {}
    for t, sub, kind in subs:
        table = []
        for cell in L.GRID:
            rows = sorted(cv[(sub, cell)])
            table.append({"cell": list(cell), "loss_mean": float(np.mean([r[1] for r in rows])), "loss_folds": [r[1] for r in rows],
                          "best_iter": [r[2] for r in rows], "n_val": [r[4] for r in rows]})
        best = min(range(len(table)), key=lambda i: table[i]["loss_mean"])
        n_it = max(MIN_ITERS, int(round(float(np.mean(table[best]["best_iter"])))))
        chosen[sub] = {"cell": tuple(table[best]["cell"]), "n_it": n_it}
        res["targets"].setdefault(t, {"subs": {}})["subs"][sub] = {"kind": kind, "cv": table, "chosen": list(chosen[sub]["cell"]), "iterations": n_it}
    # --- 2. фолдовые модели выбранной клетки ---
    models = {}
    meta = {}
    tasks = [(sub, chosen[sub]["cell"], chosen[sub]["n_it"], k) for _t, sub, _kind in subs for k in range(5)]
    with ProcessPoolExecutor(procs, mp_context=mp) as ex:
        for sub, k, m, mt, ntr in ex.map(refit_task, tasks, chunksize=1):
            models[(sub, k)] = m
            meta[(sub, k)] = dict(mt, n_train=ntr)
    # --- 3. OOF-предсказания U1-F, границы, ворота ---
    kinds = {sub: kind for _t, sub, kind in subs}
    oof = {}
    for _t, sub, kind in subs:
        s = np.full(len(U1), np.nan)
        for k in range(5):
            m = blk1 == k
            if m.any():
                s[m] = predict(models[(sub, k)], kind, X1[m])
        oof[sub] = s
    summary = []
    oof_out = U1[L.KEY].copy()
    for t in tnames:
        s = oof["mfe"] - oof["mae"] if t == "T5" else oof[SPECS[t][0][0]]
        lower = t == "T4"
        evid = {}
        if t == "T4" and stops < L.MIN_EVENTS:
            evid["undefined"] = f"стопов в U0-F {stops} < {L.MIN_EVENTS}"
        q = L.oof_threshold(s, lower)
        d = L.describe(U1, s, t, q, lower, 60)
        evid.update({"q": q, "lower": lower, "oof": d,
                     "gate_oof": bool(d["S1"] > 0 and d["n_sel"] >= L.MIN_SEL and "undefined" not in evid)})
        res["targets"][t].update(evid)
        oof_out[f"s_{t}"] = s
        summary.append((t, d["n_sel"], d["S1"], d["S2"], evid["gate_oof"]))
    oof_out.to_parquet(os.path.join(out, f"oof-{side}.parquet"))
    # --- 4. сохранение моделей ---
    for (sub, k), m in models.items():
        p = os.path.join(mdir, f"{side}_{sub}_f{k}.cbm")
        m.save_model(p)
    for t in tnames:
        for sub, _kind in SPECS[t]:
            res["targets"][t]["subs"][sub]["fold_meta"] = [meta[(sub, k)] for k in range(5)]
    # --- 5. финальная модель на всём U0-F (справочно), важности ---
    cutoff = FINAL_CUTOFF_DAYS * L.DAY
    keep = (U0["exit_ms"].to_numpy() < cutoff)
    res["final_excluded_rows"] = int((~keep).sum())
    all_idx = np.nonzero(keep)[0]
    for _t, sub, kind in subs:
        D = prep(U0, sub, all_idx, all_idx[:0])
        m = make_model(kind, chosen[sub]["cell"], chosen[sub]["n_it"], False)
        m.fit(make_pool(G["X"][D["tr"]], D["ytr"], D["wtr"], D["gtr"]))
        m.save_model(os.path.join(mdir, f"{side}_{sub}_final.cbm"))
        info = {"clip_or_bounds": D["meta"], "n_train": int(len(D["tr"]))}
        try:
            from catboost import Pool
            sv = m.get_feature_importance(Pool(G["X"][D["tr"]][:3000], label=D["ytr"][:3000], weight=None if D["wtr"] is None else D["wtr"][:3000],
                                               group_id=None if D["gtr"] is None else D["gtr"][:3000]), type="ShapValues")
            info["shap_mean_abs"] = {f: float(np.abs(sv[:, i]).mean()) for i, f in enumerate(L.FEATS)}
        except Exception as e:   # noqa: BLE001
            info["shap_error"] = str(e)[:200]
        info["pvc"] = {f: float(v) for f, v in zip(L.FEATS, m.get_feature_importance(type="PredictionValuesChange"))}
        res["targets"][_t]["subs"][sub]["final"] = info
    # --- 6. перестановочная важность по фолдовым моделям (потеря на U0-проверке) ---
    rng = np.random.default_rng(L.SEED)
    for _t, sub, kind in subs:
        imp = {f: [] for f in L.FEATS}
        for k in range(5):
            va_idx = np.nonzero(G["blk"] == k)[0]
            tr_idx = train_idx(U0, k)[0]
            D = prep(U0, sub, tr_idx, va_idx)
            Xv = G["X"][D["va"]]
            base = val_loss(kind, predict(models[(sub, k)], kind, Xv), D["yva"], D["wva"], D["gva"])
            for j, f in enumerate(L.FEATS):
                ds = []
                for _ in range(3):
                    Xp = Xv.copy()
                    Xp[:, j] = rng.permutation(Xp[:, j])
                    ds.append(val_loss(kind, predict(models[(sub, k)], kind, Xp), D["yva"], D["wva"], D["gva"]) - base)
                imp[f].append(float(np.mean(ds)))
        res["targets"][_t]["subs"][sub]["perm_importance_loss_increase"] = {f: float(np.mean(v)) for f, v in imp.items()}
    # --- 7. контроль без модели: natr_bp ---
    rho = L.spearman(U0["natr_bp"].to_numpy(float), U0["net_nf"].to_numpy(float))
    nb = U1["natr_bp"].to_numpy(float)
    lower = bool(rho < 0)
    qn = float(np.nanquantile(nb, 1 / 3 if lower else 2 / 3))
    res["control_natr_bp"] = {"spearman_U0F": rho, "lower": lower, "q": qn, "oof": L.describe(U1, nb, "CTRL", qn, lower, 60)}
    res["elapsed_s"] = time.time() - t_start
    with open(os.path.join(out, f"fit-{side}.json"), "w", encoding="utf-8") as _f:
        json.dump(res, _f, ensure_ascii=False, indent=1, default=_jd)
    for row in summary:
        print("%s n_sel=%d S1_OOF=%.4f S2_OOF=%.4f gate=%s" % row, flush=True)
    return res


def _jd(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(type(o))


# ───────────────────────────── заморозка ─────────────────────────────

def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def freeze(fit_dir, out):
    os.makedirs(os.path.join(out, "models"), exist_ok=True)
    fr = {"protocol": "docs/research/P-42-level-breakout.md §13.23, §13.23.1, §13.23.2", "feats": L.FEATS, "seed": L.SEED, "boot": {"B": L.B_BOOT, "block_days": L.BLOCK_DAYS},
          "min_sel": L.MIN_SEL, "windows": {k: list(v) for k, v in L.WINDOWS.items()}, "grid": [list(c) for c in L.GRID], "sides": {}}
    for side in ("long", "short"):
        p = os.path.join(fit_dir, f"fit-{side}.json")
        if not os.path.exists(p):
            continue
        r = json.load(open(p))
        fs = {"catboost": r.get("catboost"), "targets": {}, "pass": [], "fail": []}
        if "undefined" in r:
            fs["undefined"] = r["undefined"]
            fr["sides"][side] = fs
            continue
        for t, td in r["targets"].items():
            subs = {}
            for sub, sd in td["subs"].items():
                files = []
                for k in range(5):
                    fn = f"{side}_{sub}_f{k}.cbm"
                    src = os.path.join(fit_dir, "models", fn)
                    dst = os.path.join(out, "models", fn)
                    shutil.copyfile(src, dst)
                    files.append({"file": fn, "sha256": sha256(dst)})
                fn = f"{side}_{sub}_final.cbm"
                shutil.copyfile(os.path.join(fit_dir, "models", fn), os.path.join(out, "models", fn))
                subs[sub] = {"kind": sd["kind"], "cell": sd["chosen"], "iterations": sd["iterations"], "folds": files,
                             "final": {"file": fn, "sha256": sha256(os.path.join(out, "models", fn))}}
            ok = bool(td.get("gate_oof")) and "undefined" not in td
            fs["targets"][t] = {"q": td["q"], "lower": td["lower"], "gate_oof": ok, "n_sel_oof": td["oof"]["n_sel"], "S1_oof": td["oof"]["S1"],
                                "S2_oof": td["oof"]["S2"], "subs": subs}
            (fs["pass"] if ok else fs["fail"]).append(t)
        fs["m"] = len(fs["pass"])
        fs["control_natr_bp"] = {k: r["control_natr_bp"][k] for k in ("spearman_U0F", "lower", "q")}
        fs["sigma_net_U1"], fs["trades_per_day_U1"], fs["rows_U1"], fs["rows_U0"] = r["sigma_net_U1"], r["trades_per_day_U1"], r["rows_U1"], r["rows_U0"]
        fr["sides"][side] = fs
    here = os.path.dirname(os.path.abspath(__file__))
    fr["code_sha256"] = {fn: sha256(os.path.join(here, fn)) for fn in sorted(os.listdir(here)) if fn.startswith("tk598_") and fn.endswith(".py")}
    with open(os.path.join(out, "frozen.json"), "w", encoding="utf-8") as _f:
        json.dump(fr, _f, ensure_ascii=False, indent=1, default=_jd)
    for side, fs in fr["sides"].items():
        print(side, "m =", fs.get("m"), "pass", fs.get("pass"), "fail", fs.get("fail"), fs.get("undefined", ""))
    return fr


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    f = sp.add_parser("fit")
    f.add_argument("--side", required=True, choices=["long", "short"])
    f.add_argument("--u0", required=True)
    f.add_argument("--u1", required=True)
    f.add_argument("--out", required=True)
    f.add_argument("--procs", type=int, default=6)
    z = sp.add_parser("freeze")
    z.add_argument("--fit-dir", required=True)
    z.add_argument("--out", required=True)
    a = ap.parse_args()
    if a.cmd == "fit":
        fit_side(a.side, a.u0, a.u1, a.out, a.procs)
    else:
        freeze(a.fit_dir, a.out)


if __name__ == "__main__":
    main()
