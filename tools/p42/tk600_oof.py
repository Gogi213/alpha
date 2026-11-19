"""П-42 §13.25 п. 5 (TK-600): внефолдовые предсказания руки по СОХРАНЁННЫМ фолдовым моделям — строки U0-F и U1-F (метки не открываются).

  python3 tk600_oof.py --arm X|f12 --side long|short --tab-u0 <tab-….parquet> --tab-u1 <tab-….parquet> --models <каталог models> --out-u0 <oof.parquet> --out-u1 <oof.parquet>
Строка блока CV `k` оценивается фолдовой моделью `k` (`<side>_<sub>_f<k>.cbm`) — как `tk598_fit.fit_side` для U1; для U0 — то же правило. Оценка цели `ŝ`: T5 — `MFE^ − MAE^`, остальные — выход подмодели.
Рука f12: модели TK-598 как есть (не переобучаются), признаки f12; рука X: модели `tk600_fit.py fit`, признаки X (порядок `tk600_feat.X_FEATS`).
Запечатанность (G-S): только строки с сутками 14…73 (таблицы F); столбцы меток читаются лишь ключом сортировки и `day` — в выход идут `ŝ` и ключ.
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk598_lib as L  # noqa: E402
import tk600_feat as F6  # noqa: E402

ORIG_FEATS = list(L.FEATS)       # f12 (без подмены)


def load_model(kind, path):
    from catboost import CatBoostClassifier, CatBoostRanker, CatBoostRegressor
    m = CatBoostRegressor() if kind == "rmse" else CatBoostClassifier() if kind in ("logloss", "wlogloss") else CatBoostRanker()
    m.load_model(path)
    return m


def oof_frame(df, feats, side, models_dir):
    import tk598_fit as FT
    day = df["day"].to_numpy()
    if len(df) and (day.min() < 14 or day.max() > 73):
        raise SystemExit("G-S: строки вне суток 14…73")
    X = df[feats].to_numpy(np.float64)
    blk = L.block_of(day)
    if (blk < 0).any():
        raise SystemExit("строки вне блоков CV")
    out = df[L.KEY + ["day"]].copy()
    for t in L.TARGETS[side]:
        subs = {}
        for sub, kind in FT.SPECS[t]:
            s = np.full(len(df), np.nan)
            for k in range(5):
                m = blk == k
                if m.any():
                    s[m] = FT.predict(load_model(kind, os.path.join(models_dir, f"{side}_{sub}_f{k}.cbm")), kind, X[m])
            subs[sub] = s
        out[f"s_{t}"] = subs["mfe"] - subs["mae"] if t == "T5" else subs[FT.SPECS[t][0][0]]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", required=True, choices=["X", "f12"])
    ap.add_argument("--side", required=True, choices=["long", "short"])
    ap.add_argument("--tab-u0", required=True)
    ap.add_argument("--tab-u1", required=True)
    ap.add_argument("--models", required=True)
    ap.add_argument("--out-u0", required=True)
    ap.add_argument("--out-u1", required=True)
    a = ap.parse_args()
    feats = list(F6.X_FEATS) if a.arm == "X" else list(ORIG_FEATS)
    for tab, out in ((a.tab_u0, a.out_u0), (a.tab_u1, a.out_u1)):
        cols = L.KEY + ["day"] + feats
        df = pd.read_parquet(tab, columns=cols)
        res = oof_frame(df, feats, a.side, a.models)
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        res.to_parquet(out)
        print(f"{a.arm} {a.side} {os.path.basename(tab)}: {len(res)} строк → {out}", flush=True)


if __name__ == "__main__":
    main()
