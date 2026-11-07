#!/usr/bin/env python3
"""TK-493, задача C (не код репозитория): срезы подсказок видео для ВСЕХ связок Gate (c1d2 = 1) на формировании.

Вызывает conv_hslice.main с расширенным списком HINTS (подсказки со связками Gate из hints_membership_gate.csv); пишет в отдельный каталог OUT_HX/k7,
не трогая выходы конвейера. Окно не читается (conv_guard, last_day 2478).
  C2_HOME=... python gx_hints_extra.py --src OUT --hx OUT_HX --members hints_membership_gate.csv
"""
import argparse
import csv
import os
import shutil
import sys

CODE = os.path.expanduser("~/xdl9/c2g/repo/tools/compute/statarb/xdl9/conv")
sys.path.insert(0, CODE)
import conv_hslice as H     # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--hx", required=True)
    ap.add_argument("--members", required=True)
    ap.add_argument("--gate-flag", default="--with-gate")
    a = ap.parse_args()
    rows = list(csv.DictReader(open(a.members, encoding="utf-8")))
    have = {(n.split()[0], e1, s1, e2, s2) for n, e1, s1, e2, s2 in H.HINTS}
    base = {(e1, s1, e2, s2) for n, e1, s1, e2, s2 in H.HINTS}
    extra = []
    for r in rows:
        if r["c1d2"] != "1":
            continue
        key = (r["ex1"], r["sym1"], r["ex2"], r["sym2"])
        if key in base:
            continue
        extra.append(("%s %s %s_%s x %s_%s" % (r["hint"], r["asset"], r["ex1"], r["sym1"], r["ex2"], r["sym2"]), r["ex1"], r["sym1"], r["ex2"], r["sym2"]))
        base.add(key)
    H.HINTS = list(H.HINTS) + extra
    os.makedirs(os.path.join(a.hx, "k7"), exist_ok=True)
    for sub in ("calib",):
        dst = os.path.join(a.hx, sub)
        if not os.path.exists(dst):
            os.symlink(os.path.join(a.src, sub), dst)
    for fn in os.listdir(os.path.join(a.src, "k7")):
        if fn.startswith("selection_") and fn.endswith(".json"):
            shutil.copy(os.path.join(a.src, "k7", fn), os.path.join(a.hx, "k7", fn))
    print("подсказок всего %d (из них добавлено связок Gate: %d)" % (len(H.HINTS), len(extra)))
    return H.main(["--out", a.hx, "--classes", "crypto,eq", a.gate_flag])


if __name__ == "__main__":
    sys.exit(main())
