"""П-42 §13.23 / §13.23.1 (TK-598): ворота шага 0 по выходам движка.

  python3 tk598_gates.py eng --run <out/NAME> --win F|jul|aug|hold [--ref-l N,SUM --ref-s N,SUM]
      G-E2 (портфельный режим баз: число сделок и сумма — F обязательны, август справочно), G-E3 (≥ 99 % сделок баз найдены в SIGMODE-U1 с теми же
      entry_ms, exit_ms, kind, net), ключ (sym, level, sig_ms) уникален, номинал 500 / 250, net − (gross − fees) = funding. Метки окон июль / август / 14–16.11 (net, суммы)
      НЕ печатаются: в этих окнах — только счётчики, булевы итоги и максимум |Δ|; суммы баз печатаются только в F и августе (август читали TK-594 / TK-595, §13.23 п. 3).
  python3 tk598_gates.py win --run <out/NAME> --win … [--v L_u1,S_u1]
      G-W (граница окон), до заморозки — даты и число сделок по окну без сумм: сутки сигналов ⊂ [lo, hi], число различных суток.
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


def load(run, variant, side):
    d = os.path.join(run, "eng", variant)
    return L.trade_frame(os.path.join(d, f"{side}-trades.csv"), os.path.join(d, f"{side}-detail.jsonl"))


def eng_gates(run, win, ref_l=None, ref_s=None):
    lo, hi, _, _ = L.WINDOWS[win]
    show_sums = win in ("F", "aug")
    res = {"win": win, "gates": {}}
    ok_all = True
    pairs = (("L_base", "L_u1", "long", ref_l), ("S_base", "S_u1", "short", ref_s))
    for base, u1, side, ref in pairs:
        b = L.add_labels(load(run, base, side)) if os.path.isdir(os.path.join(run, "eng", base)) else None
        u = L.add_labels(load(run, u1, side))
        g = {}
        if b is not None:
            mday = (b["day"] >= lo) & (b["day"] <= hi)
            mentry = L.window_mask(b["entry_ms"] // L.DAY, win)     # `entry_day` в trades.csv — от первых суток окна (14), поэтому сутки входа — entry_ms // DAY
            g["base_rows"] = int(len(b))
            g["base_rows_sig_day"] = int(mday.sum())
            g["base_rows_entry_day"] = int(mentry.sum())
            if show_sums:
                g["base_net_sig_day"] = round(float(b.loc[mday, "net"].sum()), 4)
                g["base_net_entry_day"] = round(float(b.loc[mentry, "net"].sum()), 4)
                g["base_net_all"] = round(float(b["net"].sum()), 4)
            if ref:
                n_ref, s_ref = ref
                g["ref"] = {"n": n_ref, "sum": s_ref}
                g["e2_count_ok"] = bool(g["base_rows_sig_day"] == n_ref or g["base_rows_entry_day"] == n_ref)
                if show_sums:
                    g["e2_sum_ok"] = bool(abs(g["base_net_sig_day"] - s_ref) <= 0.0051 or abs(g["base_net_entry_day"] - s_ref) <= 0.0051)
            # G-E3: сделки базы найдены в SIGMODE-U1
            m = b.merge(u, on=L.KEY, how="left", suffixes=("", "_u"), indicator=True)
            found = m["_merge"] == "both"
            same = found & (m["entry_ms"] == m["entry_ms_u"]) & (m["exit_ms"] == m["exit_ms_u"]) & (m["kind"] == m["kind_u"]) & ((m["net"] - m["net_u"]).abs() <= 1e-5)
            g["e3_base"] = int(len(b))
            g["e3_found"] = int(found.sum())
            g["e3_same"] = int(same.sum())
            g["e3_share"] = float(same.mean()) if len(b) else float("nan")
            g["e3_ok"] = bool(g["e3_share"] >= 0.99)
            g["e3_exceptions"] = [f"{r.sym}|{int(r.level)}|{int(r.sig_ms)}" for r in m[~same].head(20).itertuples()]
        g["u1_rows"] = int(len(u))
        g["key_unique_u1"] = bool(not u.duplicated(L.KEY).any())
        # номинал и тождество net − (gross − fees) = funding
        nom_ok = u["nominal"].isin([500.0, 250.0]) if side == "short" else (u["nominal"] == 500.0)
        g["nominal_ok_u1"] = bool(nom_ok.all())
        g["nominal_counts"] = {str(k): int(v) for k, v in u["nominal"].value_counts().items()}
        ident = (u["net"] - (u["gross"] - u["fees"]) - u["funding"]).abs()
        g["net_identity_max_abs"] = float(ident.max()) if len(u) else 0.0
        g["net_identity_ok"] = bool(g["net_identity_max_abs"] <= 2e-6)
        for need in ("key_unique_u1", "nominal_ok_u1", "net_identity_ok"):
            ok_all &= g[need]
        for need in ("e2_count_ok", "e2_sum_ok", "e3_ok"):
            if need in g:
                if win == "aug" and need.startswith("e2"):
                    continue         # §13.23.1 п. 2: G-E2 августа — справочные
                ok_all &= g[need]
        res["gates"][side] = g
    # U0 (только F): ключи уникальны, U1 ⊂ U0 по ключам — справочно
    if win == "F":
        for side, v0, v1 in (("long", "L_u0", "L_u1"), ("short", "S_u0", "S_u1")):
            u0, u1 = load(run, v0, side), load(run, v1, side)
            res["gates"][side]["u0_rows"] = int(len(u0))
            res["gates"][side]["key_unique_u0"] = bool(not u0.duplicated(L.KEY).any())
            res["gates"][side]["u1_in_u0_keys"] = float(u1.merge(u0[L.KEY], on=L.KEY, how="left", indicator=True)["_merge"].eq("both").mean())
            ok_all &= res["gates"][side]["key_unique_u0"]
    res["ok"] = bool(ok_all)
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return res


def win_gates(run, win, variants):
    lo, hi, nd, t0 = L.WINDOWS[win]
    res = {"win": win, "t0": t0, "days": [lo, hi]}
    ok = True
    for v in variants:
        side = "long" if v.startswith("L") else "short"
        tr = pd.read_csv(os.path.join(run, "eng", v, f"{side}-trades.csv"), usecols=["sym", "level", "sig_ms", "entry_ms"])
        day = tr["sig_ms"] // L.DAY
        g = {"rows": int(len(tr)), "sig_days_min": int(day.min()) if len(tr) else None, "sig_days_max": int(day.max()) if len(tr) else None,
             "distinct_sig_days": int(day.nunique()), "rows_outside": int((~L.window_mask(day, win)).sum()),
             "entry_day_outside": int((~L.window_mask(tr["entry_ms"] // L.DAY, win)).sum())}
        g["ok"] = bool(g["rows_outside"] == 0)
        ok &= g["ok"]
        res[v] = g
    res["ok"] = bool(ok)
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return res


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    e = sp.add_parser("eng")
    e.add_argument("--run", required=True)
    e.add_argument("--win", required=True, choices=list(L.WINDOWS))
    e.add_argument("--ref-l")
    e.add_argument("--ref-s")
    w = sp.add_parser("win")
    w.add_argument("--run", required=True)
    w.add_argument("--win", required=True, choices=list(L.WINDOWS))
    w.add_argument("--v", default="L_u1,S_u1")
    a = ap.parse_args()
    if a.cmd == "eng":
        ref = lambda s: (int(s.split(",")[0]), float(s.split(",")[1])) if s else None  # noqa: E731
        r = eng_gates(a.run, a.win, ref(a.ref_l), ref(a.ref_s))
    else:
        r = win_gates(a.run, a.win, a.v.split(","))
    sys.exit(0 if r["ok"] else 1)


if __name__ == "__main__":
    main()
