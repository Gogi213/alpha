#!/usr/bin/env python3
"""TK-464 гейт режима --agg: агрегаты ядра против агрегации построчного вывода того же ядра.
python tk464_agg_gate.py <rows.csv[.gz]> <agg.csv> -> 'IDENTICAL_AGG групп N' или 'DIFF ...' (rc 1).
Счётчики — точно; суммы f64 — rtol 1e-9 (порядок суммирования другой). Ключ группы: все колонки до reason (t0 -> сутки UTC)."""
import sys
import numpy as np
import pandas as pd

SUMS = ["gross", "fund", "net", "net_tk", "net_mk", "sq", "bars", "mfe_bps"]
CNT = ["n", "nofill", "no_target", "n_stop", "n_target", "n_deadline", "n_ot", "win"]


def main(rows, agg):
    r = pd.read_csv(rows, dtype=str, keep_default_na=False)
    a = pd.read_csv(agg, dtype=str, keep_default_na=False)
    ri = list(r.columns).index("reason")
    key_r = [c for c in r.columns[:ri]]
    key_a = ["day" if c == "t0" else c for c in key_r]
    assert list(a.columns[: len(key_a)]) == key_a, (list(a.columns[: len(key_a)]), key_a)
    r = r.copy()
    r["day"] = (r["t0"].astype(np.int64) // 86400000).astype(str)
    key = [c for c in key_a]
    rej = r.reason.isin(["nofill", "no_target"])
    v = r[~rej].copy()
    for c in ["gross", "fee", "fee_mk", "fee_tk", "fund", "bars", "mfe_bps"]:
        v[c] = pd.to_numeric(v[c].replace("", "nan"))
    v["net"] = v.gross - v.fee - v.fund
    v["net_tk"] = v.gross - v.fee_tk - v.fund
    v["net_mk"] = v.gross - v.fee_mk - v.fund
    v["sq"] = v.net ** 2
    v["win"] = (v.net > 0).astype(np.int64)
    v["n"] = 1
    for w in ("stop", "target", "deadline", "ot"):
        v["n_" + w] = (v.reason == w).astype(np.int64)
    exp = v.groupby(key, sort=False)[["n", "n_stop", "n_target", "n_deadline", "n_ot", "win"] + SUMS[:2] + ["net", "net_tk", "net_mk", "sq", "bars", "mfe_bps"]].sum(min_count=1)
    cnt = r.assign(nofill=(r.reason == "nofill").astype(np.int64), no_target=(r.reason == "no_target").astype(np.int64)).groupby(key, sort=False)[["nofill", "no_target"]].sum()
    exp = cnt.join(exp, how="left")
    for c in ["n", "n_stop", "n_target", "n_deadline", "n_ot", "win"]:
        exp[c] = exp[c].fillna(0).astype(np.int64)
    g = a.set_index(key)
    bad = []
    if len(g) != len(exp):
        bad.append(f"групп {len(g)} != {len(exp)}")
    e = exp.reindex(g.index)
    if e.isna().all(axis=1).any():
        bad.append("ключи не совпали")
    for c in CNT:
        if not (g[c].astype(np.int64).values == e[c].astype(np.int64).values).all():
            bad.append("счётчик " + c)
    for c in SUMS:
        x = pd.to_numeric(g[c].replace("", "nan")).values
        y = e[c].astype(float).values
        # группа без разрешённых сделок: сумма 0 в ядре, NaN (min_count) тут
        y = np.where(np.isnan(y) & (e["n"].values == 0), 0.0, y)
        if not np.allclose(x, y, rtol=1e-9, atol=1e-6, equal_nan=True):
            bad.append("сумма " + c)
    if bad:
        print("DIFF", "; ".join(bad))
        return 1
    print("IDENTICAL_AGG групп", len(g), "строк-источников", len(r))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
