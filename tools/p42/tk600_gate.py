"""П-42 §13.25 п. 9 (TK-600): ворота G-T2 (независимый пересчёт TOX13 из сырых zip) и G-T3 (без заглядывания на реальных строках); метки не открываются.

  python3 tk600_gate.py --feat <feat-X-….parquet> --eps <eps-….parquet> --base-date <T0> [--n 200] [--n3 60] --out <gate.json>   (TK594_TRADES — каталог сделок окна)
G-T2: n случайных строк (seed 600); эталон — `test_tk600.ReferenceTests.ref` (чистый Python, bisect), сравнение с сохранёнными значениями (отн. 1e-9), `bigimb*` — с q99 из процесса монеты не сверяются
      (q99 берётся из гистограммы 168 ч — проверяется юнит-тестом; здесь эталон с q99 = None → NaN, сохранённые `bigimb*` исключаются).
G-T3: n3 строк: значения по ленте с записями t ≥ σ и по ленте, обрезанной на t < σ, совпадают; значения совпадают с сохранёнными (кроме `bigimb*`).
Несовпадение любой строки — код возврата 1.
"""
import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk594_feat as F4  # noqa: E402
import tk600_feat as F  # noqa: E402
import test_tk600 as T  # noqa: E402

SKIP = ("bigimb300", "bigimb1800")


def tape(sym, sig_abs):
    d = sig_abs // F.DAY_MS
    parts = [x for x in (F.read_trades_day_p(sym, d - 1), F.read_trades_day_p(sym, d)) if x is not None]
    if not parts:
        return None
    avail = (d - 1) * F.DAY_MS if F.read_trades_day_p(sym, d - 1) is not None else d * F.DAY_MS
    return [np.concatenate([p[i] for p in parts]) for i in range(4)] + [avail]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--feat", required=True); ap.add_argument("--eps", required=True); ap.add_argument("--base-date", required=True)
    ap.add_argument("--n", type=int, default=200); ap.add_argument("--n3", type=int, default=60); ap.add_argument("--out", required=True)
    a = ap.parse_args()
    f = pd.read_parquet(a.feat, columns=F.KEY + F.TOX)
    e = pd.read_parquet(a.eps, columns=["sym", "level", "sig_ms", "side"])
    e["level"] = e["level"].astype(np.int64)
    df = f.merge(e, on=F.KEY, how="inner", validate="one_to_one").sample(frac=1.0, random_state=600).reset_index(drop=True)
    base = F4.base_ms(a.base_date)
    res = {"G-T2": {"rows": 0, "values": 0, "nonnan": 0, "mismatch": 0, "skipped_dense": 0}, "G-T3": {"rows": 0, "mismatch": 0}}
    first_cache = {}
    for _, r in df.iterrows():
        if res["G-T2"]["rows"] >= a.n and res["G-T3"]["rows"] >= a.n3:
            break
        sig = base + int(r["sig_ms"])
        tp = tape(r["sym"], sig)
        if tp is None:
            continue
        Tm, P, V, B, avail = tp
        i_hi = int(np.searchsorted(Tm, sig, "left"))
        lo24 = int(np.searchsorted(Tm, sig - 24 * F.HOUR_MS, "left"))
        s = 1 if r["side"] == "long" else -1
        first = first_cache.setdefault(r["sym"], None)
        if res["G-T2"]["rows"] < a.n:
            if i_hi - lo24 > 150_000:
                res["G-T2"]["skipped_dense"] += 1
            else:
                recs = [(int(Tm[i]), float(P[i]), float(V[i] / P[i]), bool(B[i])) for i in range(max(lo24 - 200, 0), i_hi)]
                ref = T.ReferenceTests.ref(recs, sig, s, None, 0, avail)    # first = 0: условие «монета старше окна» выполнено; coin_first проверяется юнит-тестом
                stored = {k: float(r[k]) for k in F.TOX}
                bad = 0
                for k in F.TOX:
                    if k in SKIP or k.startswith("vpin"):
                        continue
                    res["G-T2"]["values"] += 1
                    res["G-T2"]["nonnan"] += int(stored[k] == stored[k])
                    if not T.same(stored[k], ref[k], 1e-9):
                        bad += 1
                res["G-T2"]["mismatch"] += bad
                res["G-T2"]["rows"] += 1
        if res["G-T3"]["rows"] < a.n3:
            full = F.tox_features(Tm, P, V, B, sig, s, None, int(Tm[0]), avail)
            cut = F.tox_features(Tm[:i_hi], P[:i_hi], V[:i_hi], B[:i_hi], sig, s, None, int(Tm[0]), avail)
            bad = sum(0 if T.same(full[k], cut[k], 1e-12) else 1 for k in F.TOX)
            bad += sum(0 if T.same(full[k], float(r[k]), 1e-9) else 1 for k in F.TOX if k not in SKIP and not k.startswith("vpin"))
            res["G-T3"]["mismatch"] += bad
            res["G-T3"]["rows"] += 1
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=1)
    print(json.dumps(res, ensure_ascii=False))
    if res["G-T2"]["mismatch"] or res["G-T3"]["mismatch"] or res["G-T2"]["rows"] < min(a.n, 50):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
