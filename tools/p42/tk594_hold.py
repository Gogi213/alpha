"""П-42 §13.20 п. 7 (TK-594): отложенная проверка на августе 2026 — заморозка моделей и единственное чтение.

  <venv>/bin/python tk594_hold.py freeze <ep_u0.parquet> <feat_flow> <feat_deriv> <iters.json (сравнения)> <каталог заморозки>
      — обучение на ВСЕЙ A ∪ B (сутки 14…73 без 44) для каждого набора (S0…S3), модели (CB3 / LOGIT) и стороны; число деревьев CB3 = среднее лучших итераций двух направлений
        сравнения; границы терцилей оценки — на A ∪ B; пишет `models/*.cbm`, `logit_*.json`, `frozen.json` (наборы, итерации, границы, SHA файлов), без чтения августа.
  <venv>/bin/python tk594_hold.py read <aug_u0.parquet> <aug_feat_flow> <aug_feat_deriv> <каталог заморозки> <каталог выхода>
      — ЕДИНСТВЕННОЕ чтение меток августа: AUC, ΔAUC (S1…S3 − S0, обе модели), ΔE1_top (границы терцилей — замороженные), первичный контраст, внутримонетный AUC, U1r, покрытие.
        Повторный запуск отказывает (маркер `read.done`).
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from catboost import CatBoostClassifier  # noqa: E402
from tk592_lib import prepare  # noqa: E402
from tk592_tree import CB  # noqa: E402
from tk594_cmp import SETS, B_BOOT, LogitModel, auc, boot, ci  # noqa: E402
from tk594_feat import DERIV, FLOW, join_feats  # noqa: E402

SEED = 594


def sha(path: str) -> str:
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def freeze(ep_path, flow_path, deriv_path, iters_path, out):
    os.makedirs(f"{out}/models", exist_ok=True)
    raw = join_feats(pd.read_parquet(ep_path), pd.read_parquet(flow_path), pd.read_parquet(deriv_path))
    df = prepare(raw)
    iters = json.load(open(iters_path))
    frozen = {"sets": SETS, "rows": {}, "files": {}}
    for side in ("long", "short"):
        S = df[df.side == side].reset_index(drop=True)  # A ∪ B, сутки 44 уже исключены prepare()
        frozen["rows"][side] = int(len(S))
        for name, cols in SETS.items():
            n_it = int(round(np.mean([iters[f"{side}|A->B|{name}"]["iters"], iters[f"{side}|B->A|{name}"]["iters"]])))
            m = CatBoostClassifier(iterations=n_it, depth=3, **CB)
            m.fit(S[cols].to_numpy(), S["y"].to_numpy())
            tr = m.predict_proba(S[cols].to_numpy())[:, 1]
            path = f"{out}/models/cb3_{side}_{name}.cbm"
            m.save_model(path)
            lm = LogitModel().fit(S, cols)
            ls = lm.score(S)
            json.dump(lm.to_json(), open(f"{out}/models/logit_{side}_{name}.json", "w"))
            frozen["files"][f"cb3_{side}_{name}"] = {"iters": n_it, "cut_hi": float(np.quantile(tr, 2 / 3)), "cut_lo": float(np.quantile(tr, 1 / 3)), "sha256": sha(path)}
            frozen["files"][f"logit_{side}_{name}"] = {"cut_hi": float(np.quantile(ls, 2 / 3)), "cut_lo": float(np.quantile(ls, 1 / 3)),
                                                       "sha256": sha(f"{out}/models/logit_{side}_{name}.json")}
            print(side, name, "iters", n_it, flush=True)
    json.dump(frozen, open(f"{out}/frozen.json", "w"), ensure_ascii=False, indent=1)


def table_sha(aug_u0: str, flow: str, deriv: str) -> str:
    """SHA таблицы августа БЕЗ столбцов меток и контроля: ключи + признаки (читаются только эти столбцы)."""
    keys = ["ep_id", "sym", "side", "sig_ms", "day", "in_pool", "turn24h"]
    a = pd.read_parquet(aug_u0, columns=keys).sort_values("ep_id")
    h = hashlib.sha256()
    h.update(pd.util.hash_pandas_object(a, index=False).to_numpy().tobytes())
    for p in (flow, deriv):
        b = pd.read_parquet(p).sort_values("ep_id")
        h.update(pd.util.hash_pandas_object(b, index=False).to_numpy().tobytes())
    return h.hexdigest()


def read(aug_u0, flow_path, deriv_path, fdir, out):
    os.makedirs(out, exist_ok=True)
    if os.path.exists(f"{out}/read.done"):
        sys.exit("август уже читался (read.done): повторное чтение запрещено (§13.20 п. 7)")
    frozen = json.load(open(f"{fdir}/frozen.json"))
    for k, v in frozen["files"].items():
        ext = "cbm" if k.startswith("cb3") else "json"
        assert sha(f"{fdir}/models/{k}.{ext}") == v["sha256"], f"модель {k} изменена после заморозки"
    open(f"{out}/read.done", "w").write(time.strftime("%F %T"))
    t0 = time.time()
    raw = join_feats(pd.read_parquet(aug_u0), pd.read_parquet(flow_path), pd.read_parquet(deriv_path))
    d = raw.copy()
    d["lturn24"] = np.log10(d["turn24h"].clip(lower=1))
    n_all = len(d)
    d = d[(d["c_n"] >= 5) & d["c_f"].notna()].reset_index(drop=True)  # как §13.18 `prepare`, без эмбарго суток (август целиком)
    d["d"] = d["y"].astype(float) - d["c_f"].astype(float)
    info = {"episodes_all": int(n_all), "analysis": {s: int((d.side == s).sum()) for s in ("long", "short")}}
    cov = []
    auc_rows, delta_rows, e1_rows, wc_rows, u1_rows = [], [], [], [], []
    primary = {}
    for si, side in enumerate(("long", "short")):
        T = d[d.side == side].reset_index(drop=True)
        yT, dT, coins, inpool = T["y"].to_numpy(), T["d"].to_numpy(), T["sym"].to_numpy(), T["in_pool"].to_numpy().astype(bool)
        for c in FLOW + DERIV:
            cov.append({"side": side, "feature": c, "n": len(T), "coverage": float(T[c].notna().mean())})
        scores, tops = {}, {}
        for name, cols in SETS.items():
            cb = CatBoostClassifier()
            cb.load_model(f"{fdir}/models/cb3_{side}_{name}.cbm")
            lm = LogitModel.from_json(json.load(open(f"{fdir}/models/logit_{side}_{name}.json")))
            for mn, sc in (("CB3", cb.predict_proba(T[cols].to_numpy())[:, 1]), ("LOGIT", lm.score(T))):
                scores[(name, mn)] = sc
                tops[(name, mn)] = sc > frozen["files"][f"{'cb3' if mn == 'CB3' else 'logit'}_{side}_{name}"]["cut_hi"]
        rng = np.random.default_rng([SEED, si, 99])
        b_auc, b_e1, b_wc, p_wc = boot(coins, yT, dT, scores, tops, B_BOOT, rng)
        for (name, mn), sc in scores.items():
            lo, hi = ci(b_auc[(name, mn)], 0.95)
            auc_rows.append({"side": side, "set": name, "model": mn, "auc": auc(sc, yT), "lo95": lo, "hi95": hi, "n": len(T), "k": int(yT.sum())})
            e1_rows.append({"side": side, "set": name, "model": mn, "n_top": int(tops[(name, mn)].sum()), "E1_top": float(dT[tops[(name, mn)]].mean()) if tops[(name, mn)].any() else float("nan"),
                            "y_top": float(yT[tops[(name, mn)]].mean()) if tops[(name, mn)].any() else float("nan")})
            wc_rows.append({"side": side, "set": name, "model": mn, "within_coin_auc": p_wc[(name, mn)]})
            u1_rows.append({"side": side, "set": name, "model": mn, "n_u1": int(inpool.sum()), "k_u1": int(yT[inpool].sum()),
                            "auc_u1": auc(sc[inpool], yT[inpool]) if yT[inpool].sum() > 0 else float("nan")})
        for mn in ("CB3", "LOGIT"):
            for name in ("S1", "S2", "S3"):
                k1, k0 = (name, mn), ("S0", mn)
                dv = b_auc[k1] - b_auc[k0]
                lo95, hi95 = ci(dv, 0.95)
                lo975, hi975 = ci(dv, 0.975)
                e1d = b_e1[k1] - b_e1[k0]
                delta_rows.append({"side": side, "set": name, "model": mn, "dAUC": auc(scores[k1], yT) - auc(scores[k0], yT), "lo95": lo95, "hi95": hi95, "lo975": lo975, "hi975": hi975,
                                   "dE1_top": float(dT[tops[k1]].mean() - dT[tops[k0]].mean()), "dE1_lo95": ci(e1d, 0.95)[0], "dE1_hi95": ci(e1d, 0.95)[1],
                                   "d_within_coin": p_wc[k1] - p_wc[k0], "dwc_lo95": ci(b_wc[k1] - b_wc[k0], 0.95)[0], "dwc_hi95": ci(b_wc[k1] - b_wc[k0], 0.95)[1],
                                   "primary": bool(name == "S3" and mn == "CB3")})
    A, Dl = pd.DataFrame(auc_rows), pd.DataFrame(delta_rows)
    for side in ("long", "short"):
        p = Dl[(Dl.side == side) & Dl.primary].iloc[0]
        primary[side] = {"passed": bool(p.lo975 > 0 and p.dE1_top > 0), "dAUC": p.dAUC, "lo975": p.lo975, "hi975": p.hi975, "dE1_top": p.dE1_top, "dE1_ci95": [p.dE1_lo95, p.dE1_hi95]}
    A.to_csv(f"{out}/auc.csv", index=False)
    Dl.to_csv(f"{out}/delta.csv", index=False)
    pd.DataFrame(e1_rows).to_csv(f"{out}/e1_top.csv", index=False)
    pd.DataFrame(wc_rows).to_csv(f"{out}/within_coin.csv", index=False)
    pd.DataFrame(u1_rows).to_csv(f"{out}/u1r.csv", index=False)
    pd.DataFrame(cov).to_csv(f"{out}/coverage.csv", index=False)
    json.dump({"info": info, "primary": primary, "seconds": time.time() - t0}, open(f"{out}/hold_result.json", "w"), ensure_ascii=False, indent=1)
    print(json.dumps({"info": info, "primary": primary}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "freeze":
        freeze(*sys.argv[2:7])
    elif mode == "sha":
        print(table_sha(*sys.argv[2:5]))
    elif mode == "read":
        read(*sys.argv[2:7])
    else:
        sys.exit("режим: freeze | sha | read")
