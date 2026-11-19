"""П-42 §13.25 п. 6 (TK-600): заморозка X-руки до чтения июля / августа — модели обеих рук, границы `q` из U1-F OOF, список целей этапа 2, sha256 моделей / признаков окон / кода.

  python3 tk600_freeze.py --stage1 <stage1.json> --models-x <fit-X/models> --models-f12 <p42-tk598/fit/models> --oof-dir <oof> --feat-dir <feat> --out <каталог заморозки>
      Цели этапа 2 — механически из `stage1.json` (`admit_stage2`). Модели обеих рук копируются в `<out>/models-X`, `<out>/models-f12` (sha256 каждого фолдового файла); границы `q` каждой руки —
      `oof_threshold` её внефолдовых предсказаний U1-F (`oof-<рука>-<сторона>-u1.parquet`, T4 — `q_{1/3}`); sha256 файлов признаков июля и августа (`feat-X-<прогон>-<L|S>_u1.parquet`, без меток),
      `stage1.json` и кода (`tk600_*.py`, `tk598_*.py`, `tk594_feat.py`). Выход — `<out>/frozen600.json`. Если ни одна цель не допущена — файл не создаётся (этапы 2–3 не запускаются).
Меток июля / августа не открывает.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk598_lib as L  # noqa: E402
import tk600_feat as F6  # noqa: E402
from tk598_fit import SPECS, sha256  # noqa: E402

FEAT_FILES = [f"feat-X-{run}-{v}_u1.parquet" for run in ("jul-1", "aug-1") for v in ("L", "S")]


def admitted(stage1):
    """{side: [цели]} — только `admit_stage2` из этапа 1, порядок как в `stage1.json`."""
    out = {}
    for side, sd in stage1["sides"].items():
        ts = [r["target"] for r in sd["targets"] if r.get("admit_stage2")]
        if ts:
            out[side] = ts
    return out


def arm_record(arm, side, t, models_src, models_dst, oof_dir):
    s = pd.read_parquet(os.path.join(oof_dir, f"oof-{arm}-{side}-u1.parquet"))[f"s_{t}"].to_numpy(float)
    lower = t == "T4"
    subs = {}
    for sub, kind in SPECS[t]:
        files = []
        for k in range(5):
            fn = f"{side}_{sub}_f{k}.cbm"
            dst = os.path.join(models_dst, fn)
            if not os.path.exists(dst):
                shutil.copyfile(os.path.join(models_src, fn), dst)
            files.append({"file": fn, "sha256": sha256(dst)})
        subs[sub] = {"kind": kind, "folds": files}
    return {"q": L.oof_threshold(s, lower), "lower": lower, "n_oof_u1": int(len(s)), "subs": subs}


def freeze(a):
    with open(a.stage1, encoding="utf-8") as f:
        st = json.load(f)
    adm = admitted(st)
    if not adm:
        print("ни одна цель не допущена на этап 2 — заморозка не создаётся")
        return None
    dirs = {"X": os.path.join(a.out, "models-X"), "f12": os.path.join(a.out, "models-f12")}
    src = {"X": a.models_x, "f12": a.models_f12}
    for d in dirs.values():
        os.makedirs(d, exist_ok=True)
    fr = {"protocol": "docs/research/P-42-level-breakout.md §13.25", "seed": 600, "B": 20_000, "block_days": 4, "pool_days": 62, "min_sel": L.MIN_SEL,
          "frozen_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "x_feats": list(F6.X_FEATS), "stage1_sha256": sha256(a.stage1), "sides": {}}
    for side, ts in adm.items():
        fr["sides"][side] = {"targets": ts, "m": len(ts), "arms": {arm: {t: arm_record(arm, side, t, src[arm], dirs[arm], a.oof_dir) for t in ts} for arm in ("X", "f12")}}
    fr["inputs_sha256"] = {fn: sha256(os.path.join(a.feat_dir, fn)) for fn in FEAT_FILES if os.path.exists(os.path.join(a.feat_dir, fn))}
    here = os.path.dirname(os.path.abspath(__file__))
    fr["code_sha256"] = {fn: sha256(os.path.join(here, fn)) for fn in sorted(os.listdir(here))
                         if (fn.startswith(("tk600_", "tk598_")) and fn.endswith(".py")) or fn == "tk594_feat.py"}
    p = os.path.join(a.out, "frozen600.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(fr, f, ensure_ascii=False, indent=1)
    for side, fs in fr["sides"].items():
        print(side, "m =", fs["m"], fs["targets"], {arm: {t: round(fs["arms"][arm][t]["q"], 6) for t in fs["targets"]} for arm in ("X", "f12")})
    print("inputs:", sorted(fr["inputs_sha256"]), "frozen600.json sha256", sha256(p))
    return fr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage1", required=True)
    ap.add_argument("--models-x", required=True)
    ap.add_argument("--models-f12", required=True)
    ap.add_argument("--oof-dir", required=True)
    ap.add_argument("--feat-dir", required=True)
    ap.add_argument("--out", required=True)
    freeze(ap.parse_args())


if __name__ == "__main__":
    main()
