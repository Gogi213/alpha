"""TK-611: ворота f12 — таблица признаков Rust (`alpha-feat f12`) против эталона Python (`eps-*.parquet` TK-599 / `ep_u0.parquet` TK-594), побитово.

  python3 rustfeat_f12_gate.py compare <ref.parquet> <rust.csv> [--key ep_id | --key sym,level,sig_ms] [--side long|short] [--out gate.json]
Float-столбцы — биты `view(int64)` (маска NaN, max ULP, различие знака нуля), целые/булевы/строки — равенство. Эталон может содержать только часть
строк (`--key` другой, чем `ep_id` — для `ep_u0`, где `ep_no` — номер эпизода, а не кандидата): сравниваются общие ключи, но строки Rust обязаны
покрывать эталон целиком. Код возврата 0 — только при полном равенстве.
"""
from __future__ import annotations

import argparse
import json
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")
from rustfeat_gate import ordered  # noqa: E402

META_STR = ["univ", "side", "sym", "half"]
META_NUM = ["level", "ep_no", "sig_ms", "day", "n_sig_ep", "last_sig_ms", "lp_e8", "natr_e8", "dist_e8", "emp_coin", "hour4", "wday"]
FEATS = ["n_piv", "spread_bp", "spread_tau", "ext_gap_bp", "age_h", "since_born_h", "dist_bp", "pierce_ep", "pierce_max_bp", "touch_ep", "since_touch_h",
         "depth_min_bp", "depth_mean_bp", "approach_dur_min", "since_touch_prior_h", "touch_prior_none", "emp2h", "emp6h", "flat60", "zv2h", "zv6h",
         "tpm15", "tpm60", "gap60_s", "mv5", "mv15", "mv60", "maxmv1", "maxrng15", "cons60", "cons120", "natr_bp", "turn1h", "turn24h",
         "c5_btc_7d", "c5_btc_24h", "c15_btc_7d", "c15_btc_24h", "c1_btc_6h", "btc_mv5", "btc_mv15",
         "c5_eth_7d", "c5_eth_24h", "c15_eth_7d", "c15_eth_24h", "c1_eth_6h", "eth_mv5", "eth_mv15"]
NUM = META_NUM + FEATS + ["lturn24"]


def read_rust_csv(path: str) -> pd.DataFrame:
    """Выход `alpha-feat f12`: float — круговая запись (`round_trip`); числовые столбцы читаются как float64, `tick_ok` — True/False."""
    dtype = {c: "float64" for c in NUM}
    dtype.update({c: "object" for c in ["ep_id"] + META_STR})
    df = pd.read_csv(path, dtype=dtype, float_precision="round_trip", keep_default_na=True, na_values=[""])
    df["tick_ok"] = df["tick_ok"].map({"True": True, "False": False}) if df["tick_ok"].dtype == object else df["tick_ok"].astype(bool)
    return df


def ref_frame(path: str, side: str | None) -> pd.DataFrame:
    ref = pd.read_parquet(path)
    if side and "side" in ref:
        ref = ref[ref["side"] == side]
    ref = ref.copy()
    if "tick_ok" not in ref:
        ref["tick_ok"] = True
    ref["tick_ok"] = ref["tick_ok"].fillna(False).astype(bool)
    ref["lturn24"] = np.log10(ref["turn24h"].astype(float).clip(lower=1))
    return ref


def compare(ref: pd.DataFrame, new: pd.DataFrame, key: list[str], exclude: tuple[str, ...] = ()) -> dict:
    rep = {"rows_ref": int(len(ref)), "rows_new": int(len(new)), "key": key, "cols": {}, "total_diffs": 0, "excluded": list(exclude)}
    if ref.duplicated(key).any() or new.duplicated(key).any():
        rep["error"] = "ключ не уникален"
        rep["total_diffs"] = -1
        return rep
    j = ref.merge(new, on=key, how="outer", suffixes=("", "__rs"), indicator=True, validate="one_to_one")
    only_ref = j[j["_merge"] == "left_only"]
    rep["keys_only_ref"] = int(len(only_ref))
    rep["keys_only_new"] = int((j["_merge"] == "right_only").sum())
    if len(only_ref):
        rep["first_only_ref"] = [str(x) for x in only_ref[key].astype(str).agg("|".join, axis=1)[:20]]
        rep["total_diffs"] = -1
        return rep
    j = j[j["_merge"] == "both"].reset_index(drop=True)
    names = [c for c in j.columns if not c.endswith("__rs") and c not in ("_merge",)]
    bad_rows = np.zeros(len(j), dtype=bool)
    for c in NUM + ["tick_ok"] + META_STR:
        if c in key or c in exclude or c not in ref.columns or c + "__rs" not in j.columns:
            continue
        a_raw, b_raw = j[c], j[c + "__rs"]
        if c in META_STR:
            diff = (a_raw.astype(object) != b_raw.astype(object)).to_numpy() & ~(a_raw.isna() & b_raw.isna()).to_numpy()
            rep["cols"][c] = {"equal": int((~diff).sum()), "diff": int(diff.sum()), "first_keys": [str(k) for k in j.loc[diff, key[0]][:10]]}
        elif c == "tick_ok":
            diff = (a_raw.astype(bool) != b_raw.astype(bool)).to_numpy()
            rep["cols"][c] = {"equal": int((~diff).sum()), "diff": int(diff.sum()), "first_keys": [str(k) for k in j.loc[diff, key[0]][:10]]}
        else:
            a = a_raw.to_numpy(np.float64)
            b = b_raw.to_numpy(np.float64)
            na, nb = np.isnan(a), np.isnan(b)
            both = ~na & ~nb
            ba, bb = a.view(np.int64), b.view(np.int64)
            bits_diff = both & (ba != bb)
            diff = (na != nb) | bits_diff
            ulps = [abs(ordered(int(x)) - ordered(int(y))) for x, y in zip(ba[bits_diff], bb[bits_diff])]
            rep["cols"][c] = {"equal": int((~diff).sum()), "diff": int(diff.sum()), "nan_mask_diff": int((na != nb).sum()), "nan_both": int((na & nb).sum()),
                              "max_ulp": max(ulps) if ulps else 0, "zero_sign_diff": int((bits_diff & (a == b)).sum()),
                              "first_keys": [str(k) for k in j.loc[diff, key[0]][:10]]}
        rep["total_diffs"] += int(diff.sum())
        bad_rows |= diff
    rep["rows_with_diff"] = int(bad_rows.sum())
    rep["cols_compared"] = len(rep["cols"])
    rep["max_ulp"] = max((v.get("max_ulp", 0) for v in rep["cols"].values()), default=0)
    return rep


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    c = sp.add_parser("compare")
    c.add_argument("ref")
    c.add_argument("new")
    c.add_argument("--key", default="ep_id")
    c.add_argument("--side", default="")
    c.add_argument("--out", default="")
    c.add_argument("--exclude", default="", help="столбцы вне сравнения (различаются по построению маршрута; см. воротa ep_u0)")
    a = ap.parse_args()
    key = a.key.split(",")
    ref = ref_frame(a.ref, a.side or None)
    new = read_rust_csv(a.new)
    rep = compare(ref, new, key, tuple(x for x in a.exclude.split(",") if x))
    if a.out:
        json.dump(rep, open(a.out, "w"), ensure_ascii=False, indent=1)
    bad = {k: v for k, v in rep["cols"].items() if v["diff"]}
    print(json.dumps({k: v for k, v in rep.items() if k not in ("cols",)}, ensure_ascii=False))
    for k, v in bad.items():
        print("DIFF", k, json.dumps(v, ensure_ascii=False))
    ok = rep["total_diffs"] == 0 and rep.get("keys_only_ref", 0) == 0
    print("GATE", "PASS" if ok else "FAIL", f"rows={rep['rows_ref']} cols={rep.get('cols_compared', 0)} diffs={rep['total_diffs']} max_ulp={rep.get('max_ulp', '?')}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
