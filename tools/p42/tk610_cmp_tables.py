"""П-42 §13.27 (TK-610): побитовое сравнение двух таблиц признаков (parquet) по ключу (sym, level, sig_ms): Rust-f12 против Python (п. 11.11, правило для `wd`).
  python3 tk610_cmp_tables.py <a.parquet> <b.parquet> [--out <json>]
Печатает JSON: строк, различающиеся столбцы (биты float64; остальное — равенство); код возврата 0 — идентичны."""
import json
import sys

import numpy as np
import pandas as pd

KEY = ["sym", "level", "sig_ms"]


def compare(pa, pb):
    a = pd.read_parquet(pa).sort_values(KEY).reset_index(drop=True)
    b = pd.read_parquet(pb).sort_values(KEY).reset_index(drop=True)
    res = {"a": pa, "b": pb, "rows_a": int(len(a)), "rows_b": int(len(b)), "diff_columns": [], "only_in_one": sorted(set(a.columns) ^ set(b.columns))}
    if len(a) != len(b) or not a[KEY].equals(b[KEY]):
        res["diff_columns"].append("KEY")
        res["ok"] = False
        return res
    for c in a.columns:
        if c not in b:
            continue
        x, y = a[c], b[c]
        if x.dtype.kind == "f" or y.dtype.kind == "f":
            same = np.array_equal(x.to_numpy(float).view(np.int64), y.to_numpy(float).view(np.int64))
        elif x.dtype == object or y.dtype == object:
            same = x.astype(str).equals(y.astype(str))
        else:
            same = np.array_equal(x.to_numpy(), y.to_numpy())
        if not same:
            res["diff_columns"].append(c)
    res["ok"] = not res["diff_columns"] and not res["only_in_one"]
    return res


if __name__ == "__main__":
    r = compare(sys.argv[1], sys.argv[2])
    if "--out" in sys.argv:
        with open(sys.argv[sys.argv.index("--out") + 1], "w", encoding="utf-8") as f:
            json.dump(r, f, ensure_ascii=False, indent=1)
    print(json.dumps(r, ensure_ascii=False))
    sys.exit(0 if r["ok"] else 1)
