"""TK-609: сравнитель таблиц признаков «эталон Python ↔ Rust» (правила R1/R2: NaN-маска и биты float64 — без допуска).

  python3 rustfeat_gate.py compare <py.parquet> <rust.csv|rust.parquet> --cols flow|deriv|all [--out gate.json] [--eps eps.parquet]
Ключ — `ep_id`. Для каждого столбца: число равных, число различий (по битам `view(int64)`), различия масок NaN, max ULP, первые 20 ключей.
`--eps` — по эпизодам считаются классы краёв (монеты без `metrics`, монеты с первым днём сделок внутри окна) и различия по ним.
Код возврата 0 — только при полном равенстве (строки, ключи, все выбранные столбцы).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

import numpy as np
import pandas as pd

from rustfeat_run import DERIV, FLOW, read_rust_csv

DAY_MS = 86_400_000
BASE_DEFAULT = "2026-09-01"
EPOCH = dt.date(1970, 1, 1).toordinal()


def ordered(bits: int) -> int:
    """int64-представление float → монотонный порядок (для расстояния в ULP)."""
    return bits if bits >= 0 else -(2 ** 63) - bits


def compare(ref: pd.DataFrame, new: pd.DataFrame, cols: list[str]) -> dict:
    rep = {"rows_ref": int(len(ref)), "rows_new": int(len(new)), "cols": {}, "total_diffs": 0}
    if not ref["ep_id"].is_unique or not new["ep_id"].is_unique:
        rep["error"] = "ep_id не уникален"
        rep["total_diffs"] = -1
        return rep
    only_ref = sorted(set(ref["ep_id"]) - set(new["ep_id"]))
    only_new = sorted(set(new["ep_id"]) - set(ref["ep_id"]))
    rep["keys_only_ref"], rep["keys_only_new"] = len(only_ref), len(only_new)
    if only_ref or only_new:
        rep["first_only_ref"], rep["first_only_new"] = only_ref[:20], only_new[:20]
        rep["total_diffs"] = -1
        return rep
    ref = ref.set_index("ep_id")
    new = new.set_index("ep_id").reindex(ref.index)
    bad_rows = np.zeros(len(ref), dtype=bool)
    for c in cols:
        a = ref[c].to_numpy(np.float64)
        b = new[c].to_numpy(np.float64)
        na, nb = np.isnan(a), np.isnan(b)
        mask_diff = na != nb
        both = ~na & ~nb
        ba, bb = a.view(np.int64), b.view(np.int64)
        bits_diff = both & (ba != bb)
        diff = mask_diff | bits_diff
        ulps = [abs(ordered(int(x)) - ordered(int(y))) for x, y in zip(ba[bits_diff], bb[bits_diff])]
        rep["cols"][c] = {
            "equal": int((~diff).sum()), "diff": int(diff.sum()), "nan_mask_diff": int(mask_diff.sum()), "nan_both": int((na & nb).sum()),
            "max_ulp": max(ulps) if ulps else 0, "zero_sign_diff": int((bits_diff & (a == b)).sum()),
            "first_keys": [str(k) for k in ref.index[diff][:20]],
        }
        rep["total_diffs"] += int(diff.sum())
        bad_rows |= diff
    rep["rows_with_diff"] = int(bad_rows.sum())
    rep["_bad_rows"] = bad_rows
    rep["_index"] = ref.index
    return rep


def classes(eps: pd.DataFrame, bad_rows: np.ndarray, index: pd.Index, base_date: str) -> dict:
    """Классы краёв по монетам: без `metrics`, первый день сделок внутри окна `[dmin − 8, …]`; строки и строки с различиями в них."""
    trades = os.environ.get("TK594_TRADES", "/data/aggtrades/binance").split(":")
    metrics = os.environ.get("TK594_METRICS", "/data/p42-metrics")
    base_day = dt.date.fromisoformat(base_date).toordinal() - EPOCH
    e = eps.set_index("ep_id").reindex(index)
    e["abs_day"] = base_day + e["sig_ms"] // DAY_MS
    pos = {k: i for i, k in enumerate(index)}
    out = {"coins": int(e["sym"].nunique()),
           "no_metrics": {"coins": 0, "rows": 0, "diff_rows": 0},
           "first_day_in_window": {"coins": 0, "rows": 0, "diff_rows": 0}}
    for sym, g in e.groupby("sym"):
        nd = int(bad_rows[[pos[k] for k in g.index]].sum())
        if not os.path.isdir(f"{metrics}/{sym}"):
            c = out["no_metrics"]
            c["coins"] += 1
            c["rows"] += len(g)
            c["diff_rows"] += nd
        first = None
        for root in trades:
            d = f"{root}/{sym}"
            if os.path.isdir(d):
                ds = [f[len(sym) + 11:-4] for f in os.listdir(d) if f.startswith(sym + "-aggTrades-") and f.endswith(".zip")]
                if ds:
                    first = min(dt.date.fromisoformat(x).toordinal() - EPOCH for x in ds)
                    break
        if first is not None and first >= int(g["abs_day"].min()) - 8:
            c = out["first_day_in_window"]
            c["coins"] += 1
            c["rows"] += len(g)
            c["diff_rows"] += nd
    return out


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("compare")
    c.add_argument("ref")
    c.add_argument("new")
    c.add_argument("--cols", default="all", choices=["flow", "deriv", "all"])
    c.add_argument("--out", default="")
    c.add_argument("--eps", default="")
    c.add_argument("--base-date", default=BASE_DEFAULT)
    a = ap.parse_args()
    cols = {"flow": FLOW, "deriv": DERIV, "all": FLOW + DERIV}[a.cols]
    ref = pd.read_parquet(a.ref)
    new = pd.read_parquet(a.new) if a.new.endswith(".parquet") else read_rust_csv(a.new)
    rep = compare(ref, new, cols)
    bad, idx = rep.pop("_bad_rows", None), rep.pop("_index", None)
    if a.eps and bad is not None:
        rep["classes"] = classes(pd.read_parquet(a.eps, columns=["ep_id", "sym", "sig_ms"]), bad, idx, a.base_date)
    rep["ref"], rep["new"], rep["which"] = a.ref, a.new, a.cols
    if a.out:
        json.dump(rep, open(a.out, "w"), ensure_ascii=False, indent=1)
    worst = max((v["max_ulp"] for v in rep["cols"].values()), default=0)
    print(f"rows ref={rep['rows_ref']} new={rep['rows_new']} total_diffs={rep['total_diffs']} rows_with_diff={rep.get('rows_with_diff')} max_ulp={worst}")
    for k, v in rep["cols"].items():
        if v["diff"]:
            print(f"  {k}: diff={v['diff']} nan_mask={v['nan_mask_diff']} max_ulp={v['max_ulp']} first={v['first_keys'][:3]}")
    sys.exit(0 if rep["total_diffs"] == 0 and rep["rows_ref"] == rep["rows_new"] else 1)


if __name__ == "__main__":
    main()
