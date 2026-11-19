#!/usr/bin/env python3
"""TK-616 / П-42 §13.28 п. 8: сборка файла заморозки `docs/research/freeze/tk616-frozen.json` из `results.json` (исходы G_mk) и бинаря.
Геометрия — п. 7: окна w1…w5, точки L1…L5, доли α по точкам; α клеток — по числу прошедших G_mk. Результат проходит `tk616_forward.load_freeze`.

  python3 tk616_freeze.py --results <results.json> --bin <alpha-mk> --symbols <symbols.txt> --out docs/research/freeze/tk616-frozen.json
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import retitr_full as rf  # noqa: E402
import retitr_full_run as rr  # noqa: E402
import tk616_forward as fw  # noqa: E402
import tk616_maker as mk  # noqa: E402

POINT_SHARES = {"L2": 0.04, "L3": 0.10, "L4": 0.20, "L5": 0.66}
POINT_DATES = (("L1", "2026-11-30"), ("L2", "2026-12-31"), ("L3", "2027-01-31"), ("L4", "2027-02-28"), ("L5", "2027-03-31"))
N_WINDOWS = 5
ALPHA_SPLIT_RULE = ("§13.28 п. 7: α(S_base) + Σ α(мейкеры TK-616) + Σ α(кандидаты TK-618) ≤ 0,05 при любом составе; кандидаты TK-618 берут α только из доли α(S_base); "
                    "вердикт TK-618 и tk618-frozen.json — до чтения точки L1 (30.11)")


def windows():
    t0 = datetime.date(2026, 10, 16)
    out = []
    for i in range(N_WINDOWS):
        t = t0 + datetime.timedelta(days=fw.STEP * i)
        out.append({"name": "w%d" % (i + 1), "t0": t.isoformat(), "lo": (t + datetime.timedelta(days=fw.CREDIT_FIRST)).isoformat(),
                    "hi": (t + datetime.timedelta(days=fw.CREDIT_LAST)).isoformat()})
    return out


def points(wins):
    ws = [rf.Window(w["name"], w["t0"], w["lo"], w["hi"]) for w in wins]
    out = []
    for pid, ds in POINT_DATES:
        dd = datetime.date.fromisoformat(ds)
        to = dd - datetime.timedelta(days=fw.TAIL_DAYS)
        need = [w.name for w in ws if w.lod <= to and w.hid >= fw.F0]
        out.append({"id": pid, "d": ds, "entries_to": to.isoformat(), "windows": need, "decision": pid in POINT_SHARES})
    return out


def alphas(passed):
    """§13.28 п. 7: α(S_base) = 0,05 при пустом M, иначе 0,03; α(мейкер) = 0,02 / max(|M|, 2) каждому."""
    a = {"S_base": 0.05 if not passed else 0.03}
    for c in passed:
        a[c] = 0.02 / max(len(passed), 2)
    return a


def build(results, results_sha256, bin_path, bin_md5, bin_sha256, symbols_sha256, spec=None):
    spec = spec or rf.Spec.load(rr.GRIDS)
    cs, bs = mk.cells(spec)
    gmk = results["gmk"]
    passed = sorted(c for c, v in gmk.items() if v["holm_reject"] and c.startswith("S_"))
    lost = sorted(c for c, v in gmk.items() if v["holm_reject"] and not c.startswith("S_"))
    if lost:
        raise rf.GuardError("лонг-клетка %s прошла G_mk: §13.28 п. 7 предусматривает вперёд только шорт-сторону — решение координатора" % lost)
    reg = {"S_base": bs["S"], **{c: cs[c] for c in passed}, "L_base": bs["L"]}
    cells = {c: {"side": r.side, "keys": r.keys, "vid": r.vid, "family": c != "L_base"} for c, r in reg.items()}
    wins = windows()
    return {"version": "tk616-frozen-1", "protocol": "П-42 §13.28 п. 7–8",
            "binary": {"md5": bin_md5, "sha256": bin_sha256, "path": bin_path},
            "symbols_sha256": symbols_sha256, "cells": cells, "alpha": alphas(passed), "point_shares": POINT_SHARES,
            "points": points(wins), "windows": wins, "alpha_split_rule": ALPHA_SPLIT_RULE,
            "gmk": {c: {k: v[k] for k in ("side", "sum", "n", "mean", "p", "holm_threshold", "holm_reject", "positive_months", "counted_months")} for c, v in gmk.items()},
            "results_sha256": results_sha256}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results", required=True)
    ap.add_argument("--bin", required=True)
    ap.add_argument("--symbols", default=rr.SYMBOLS)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    sha, md5 = rr.file_hashes(a.bin)
    fz = build(json.load(open(a.results, encoding="utf-8")), rf.sha256_file(a.results), a.bin, md5, sha, rf.sha256_file(a.symbols))
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:                 # LF: sha256 файла = sha256 блоба в git (охрана `--freeze-sha256` на srv)
        f.write(json.dumps(fz, ensure_ascii=False, indent=1) + "\n")
    fw.load_freeze(a.out)
    print("записано %s: семейство %s, α %s" % (a.out, [c for c, v in fz["cells"].items() if v["family"]], fz["alpha"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
