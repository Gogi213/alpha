"""П-42 §13.23 (TK-598): таблица строк окна = сделки варианта движка + метки + признаки f12 + путь MAE / MFE.

  python3 tk598_table.py build --run <out/NAME> --variant L_u1 --win F|jul|aug|hold --feat <feat-….parquet> [--path <path-….parquet>] --out <tab.parquet>

Строка = сделка (ключ `(sym, level, sig_ms)`, уникален). Метки (п. 1 §13.23): `net_nf = gross − fees` (без фандинга), `bps = net_nf / номинал · 1e4`, `y_win`, `y_stop = kind == "stop"`, `mfe_natr`, `mae_natr`
(из ленты, `tk598_path.py`). Окно — по суткам сигнала `sig_ms // DAY` в [lo, hi] (`tk598_lib.WINDOWS`); строки вне окна отбрасываются, их число печатается.
Для июля, августа и 14–16.11 таблица со метками строится ТОЛЬКО скриптом чтения `tk598_read.py` после заморозки.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk598_lib as L  # noqa: E402


def build_table(run: str, variant: str, win: str, feat_path: str, path_path: str | None = None):
    side = "long" if variant[0] == "L" else "short"
    d = os.path.join(run, "eng", variant)
    tr = L.add_labels(L.trade_frame(os.path.join(d, f"{side}-trades.csv"), os.path.join(d, f"{side}-detail.jsonl")))
    n0 = len(tr)
    tr = tr[L.window_mask(tr["day"], win)].reset_index(drop=True)
    dropped = n0 - len(tr)
    ft = pd.read_parquet(feat_path)
    ft["level"] = ft["level"].astype(np.int64)
    ft["sig_ms"] = ft["sig_ms"].astype(np.int64)
    tab = tr.merge(ft[L.KEY + ["tick_ok"] + L.FEATS], on=L.KEY, how="left", validate="one_to_one")
    n_nofeat = int(tab["tick_ok"].isna().sum())
    tab["tick_ok"] = tab["tick_ok"].fillna(False).astype(bool)
    if path_path:
        pa = pd.read_parquet(path_path)
        pa["level"] = pa["level"].astype(np.int64)
        pa["sig_ms"] = pa["sig_ms"].astype(np.int64)
        tab = tab.merge(pa[L.KEY + ["mfe_pct", "mae_pct", "mfe_natr", "mae_natr", "n_ticks"]], on=L.KEY, how="left", validate="one_to_one")
    tab["entry_day_abs"] = tab["entry_ms"] // L.DAY
    info = {"variant": variant, "win": win, "rows": int(len(tab)), "dropped_outside_window": int(dropped), "rows_without_feature_row": n_nofeat,
            "distinct_sig_days": int(tab["day"].nunique()), "tick_ok": int(tab["tick_ok"].sum()),
            "nan_share": {c: float(tab[c].isna().mean()) for c in L.FEATS}}
    return tab, info


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build"])
    ap.add_argument("--run", required=True)
    ap.add_argument("--variant", required=True)
    ap.add_argument("--win", required=True, choices=list(L.WINDOWS))
    ap.add_argument("--feat", required=True)
    ap.add_argument("--path")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    if a.win != "F":
        raise SystemExit("таблица с метками окон июль / август / 14–16.11 строится только скриптом чтения после заморозки (§13.23 п. 3, G-S)")
    tab, info = build_table(a.run, a.variant, a.win, a.feat, a.path)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    tab.to_parquet(a.out)
    with open(a.out + ".info.json", "w", encoding="utf-8") as _f:
        json.dump(info, _f, ensure_ascii=False, indent=1)
    print(json.dumps(info, ensure_ascii=False))


if __name__ == "__main__":
    main()
