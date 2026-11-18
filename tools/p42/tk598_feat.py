"""П-42 §13.23 (TK-598): признаки f12 на сделки окна — производственным кодом TK-588/592 (`CandWorker` из `retitr_ftab.py`, маршрут U0), ворота G-F.

  python3 tk598_feat.py build --run <out/NAME> --variant L_u1 --win F|jul|aug|hold --prod <копия tools/p42> --work <каталог> [--procs 6]
      сигналы `<side>-sigsx.csv` варианта, отфильтрованные по ключам сделок варианта `(sym, level, sig_ms)` (читаются только столбцы ключей trades.csv — метки не открываются),
      → `retitr_ftab.load_prod` (окружение окна: T0, кэш, сделки, SIGFEAT_DAYS = 80) → `build_episodes` → `<work>/feat-<NAME>-<variant>.parquet`:
      sym, level, sig_ms, dist_e8, lp_e8, tick_ok, f12 (lturn24 = log10(turn24h.clip(lower=1))).
      Номер уровня в разных прогонах не совпадает (U0 и U1 — разные прогоны), поэтому таблица строится на каждый вариант отдельно.
  python3 tk598_feat.py gate --feat <feat-….parquet> --ref <ep_u0.parquet | aug_u0.parquet> --side long|short [--min-keys 200]
      G-F: общие ключи `(sym, sig_ms, dist_e8, lp_e8)`; читаются только столбцы ключей и f12 эталона (метки `y`, `c_f` и др. не открываются); допуск 1e-9.
      `--exclude-syms <файл>` — монеты с отличающимся входом (август, §13.23.1 п. 2), исключаются из сравнения и перечисляются.
  python3 tk598_feat.py nan --feat <feat-….parquet> [--ref-nan <json>]   — доля NaN и диапазоны признаков (июль: доля NaN ≤ F + 5 п.п., диапазоны в пределах F).
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

# окружение окон (§13.23 п. 3, §13.23.1): (T0, кэш минуток, каталог aggTrades)
ENV = {
    "F": ("2026-09-01", "/data/p42-t566/cache80", "/data/aggtrades/binance"),
    "jul": ("2026-06-17", "/data/p42-tk596/jul/cache", "/data/p42-tk596/jul/aggtrades"),
    "aug": ("2026-07-18", "/data/p42-tk596/aug/cache", "/data/p42-tk596/aug/aggtrades"),
    "hold": ("2026-10-31", "/data/p42-tk598/hold-in/cache", "/data/p42-tk598/hold-in/aggtrades"),
}
SIDE_OF = {"L": "long", "S": "short"}
TOL = 1e-9


def build(a):
    t0, cache, trades_dir = ENV[a.win]
    side = SIDE_OF[a.variant[0]]
    sd = "L" if side == "long" else "S"
    d = os.path.join(a.run, "eng", a.variant)
    keys = pd.read_csv(os.path.join(d, f"{side}-trades.csv"), usecols=L.KEY)
    sys.path.insert(0, a.prod)
    import retitr_ftab as rf
    rf.load_prod(a.prod, cache, trades_dir, days=80, base_date=t0)
    sig = rf._SR.read_sigsx(os.path.join(d, f"{side}-sigsx.csv"))
    sig["level"] = sig["level"].astype(np.int64)
    sig["sig_ms"] = sig["sig_ms"].astype(np.int64)
    m = sig.merge(keys.assign(_k=1), on=L.KEY, how="inner", validate="many_to_one")
    miss = len(keys) - m[L.KEY].drop_duplicates().shape[0]
    if miss:
        raise SystemExit(f"{miss} ключей сделок не найдены в sigsx варианта {a.variant}")
    if m.duplicated(L.KEY).any():
        raise SystemExit("дубль ключа (sym, level, sig_ms) в sigsx")
    m = m.drop(columns=["_k"])
    os.makedirs(a.work, exist_ok=True)
    flt = os.path.join(a.work, f"sigsx-{os.path.basename(a.run.rstrip('/'))}-{a.variant}.csv")
    m.to_csv(flt, index=False)
    ep_path = os.path.join(a.work, f"eps-{os.path.basename(a.run.rstrip('/'))}-{a.variant}.parquet")
    rf.build_episodes(side, flt, ep_path, a.procs)
    ep = pd.read_parquet(ep_path)
    if "tick_ok" not in ep:
        ep["tick_ok"] = True
    ep["tick_ok"] = ep["tick_ok"].fillna(False).astype(bool)
    ep["lturn24"] = np.log10(ep["turn24h"].astype(float).clip(lower=1)) if "turn24h" in ep else np.nan
    cols = ["sym", "level", "sig_ms", "dist_e8", "lp_e8", "tick_ok"] + L.FEATS
    for c in cols:
        if c not in ep:
            ep[c] = np.nan
    out = ep[cols].copy()
    if out.duplicated(L.KEY).any():
        raise SystemExit("дубль ключа в таблице признаков")
    if len(out) != len(keys):
        raise SystemExit(f"строк признаков {len(out)} ≠ сделок {len(keys)}")
    fp = os.path.join(a.work, f"feat-{os.path.basename(a.run.rstrip('/'))}-{a.variant}.parquet")
    out.to_parquet(fp)
    info = {"variant": a.variant, "win": a.win, "rows": int(len(out)), "tick_ok": int(out["tick_ok"].sum()),
            "nan_share": {c: float(out[c].isna().mean()) for c in L.FEATS}}
    with open(fp + ".info.json", "w", encoding="utf-8") as _f:
        json.dump(info, _f, ensure_ascii=False, indent=1)
    print(json.dumps(info, ensure_ascii=False))


def compare_values(a, b, tol=TOL):
    a = np.asarray(a, float)
    b = np.asarray(b, float)
    na, nb = np.isnan(a), np.isnan(b)
    both = ~na & ~nb
    diff = np.abs(a[both] - b[both])
    bad = int((diff > tol).sum()) + int((na != nb).sum())
    return {"n_common": int(len(a)), "n_both_finite": int(both.sum()), "n_nan_mismatch": int((na != nb).sum()), "n_mismatch": bad,
            "max_abs_diff": float(diff.max()) if len(diff) else 0.0}


def gate(a):
    mine = pd.read_parquet(a.feat)
    key = ["sym", "sig_ms", "dist_e8", "lp_e8"]
    ref = pd.read_parquet(a.ref, columns=["side"] + key + [c for c in L.FEATS if c != "lturn24"] + ["turn24h"]) if _has_cols(a.ref, ["side"]) else \
        pd.read_parquet(a.ref, columns=key + [c for c in L.FEATS if c != "lturn24"] + ["turn24h"])
    if "side" in ref:
        ref = ref[ref["side"] == a.side]
    ref = ref.copy()
    ref["lturn24"] = np.log10(ref["turn24h"].astype(float).clip(lower=1))
    excl = set()
    if a.exclude_syms:
        excl = {s.strip() for s in open(a.exclude_syms, encoding="utf-8") if s.strip()}
    mine = mine[mine["tick_ok"]]
    for df in (mine, ref):
        df["sig_ms"] = df["sig_ms"].astype(np.int64)
        df["dist_e8"] = df["dist_e8"].astype(np.int64)
        df["lp_e8"] = df["lp_e8"].astype(np.int64)
    rc = ref.groupby(key).size().rename("n_ref").reset_index()
    ref1 = ref.merge(rc, on=key)
    ref1 = ref1[ref1["n_ref"] == 1].drop(columns=["n_ref"])          # неоднозначная личность эталона — не сравнивается
    j = mine.merge(ref1, on=key, suffixes=("", "_ref"), how="inner")
    n_excl = int(j["sym"].isin(excl).sum()) if excl else 0
    if excl:
        j = j[~j["sym"].isin(excl)]
    res = {"side": a.side, "common_keys": int(len(j)), "excluded_by_input": n_excl, "tol": TOL, "features": {}}
    ok = len(j) >= a.min_keys
    for c in L.FEATS:
        r = compare_values(j[c].to_numpy(), j[c + "_ref"].to_numpy())
        res["features"][c] = r
        ok &= r["n_mismatch"] == 0
    res["ok"] = bool(ok)
    res["excluded_syms"] = sorted(excl & set(mine["sym"]))
    print(json.dumps(res, ensure_ascii=False, indent=1))
    sys.exit(0 if ok else 1)


def _has_cols(path, cols):
    import pyarrow.parquet as pq
    names = set(pq.ParquetFile(path).schema.names)
    return all(c in names for c in cols)


def nan_report(a):
    df = pd.read_parquet(a.feat)
    res = {"rows": int(len(df)), "tick_ok": int(df["tick_ok"].sum()), "nan_share": {}, "range": {}}
    for c in L.FEATS:
        x = df[c].astype(float)
        res["nan_share"][c] = float(x.isna().mean())
        res["range"][c] = [float(x.min()), float(x.max())] if x.notna().any() else [None, None]
    if a.ref_nan:
        ref = json.load(open(a.ref_nan))
        ok = True
        for c in L.FEATS:
            lim = ref["nan_share"][c] + 0.05
            good = res["nan_share"][c] <= lim
            lo, hi = ref["range"][c]
            if lo is not None and res["range"][c][0] is not None:
                good &= (res["range"][c][0] >= lo - 1e-9) and (res["range"][c][1] <= hi + 1e-9)
            res.setdefault("ok_by_feature", {})[c] = bool(good)
            ok &= good
        res["ok"] = bool(ok)
    print(json.dumps(res, ensure_ascii=False, indent=1))


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    b = sp.add_parser("build")
    b.add_argument("--run", required=True)
    b.add_argument("--variant", required=True)
    b.add_argument("--win", required=True, choices=list(ENV))
    b.add_argument("--prod", required=True)
    b.add_argument("--work", required=True)
    b.add_argument("--procs", type=int, default=6)
    g = sp.add_parser("gate")
    g.add_argument("--feat", required=True)
    g.add_argument("--ref", required=True)
    g.add_argument("--side", required=True, choices=["long", "short"])
    g.add_argument("--min-keys", type=int, default=200)
    g.add_argument("--exclude-syms")
    n = sp.add_parser("nan")
    n.add_argument("--feat", required=True)
    n.add_argument("--ref-nan")
    a = ap.parse_args()
    {"build": build, "gate": gate, "nan": nan_report}[a.cmd](a)


if __name__ == "__main__":
    main()
