#!/usr/bin/env python3
"""TK-493 (GW 07.11): вспомогательное для g_c2_9wg.sh - числа варианта из spec окна для базовой сетки conv_run.
  python spec_variant.py SPEC main|gate V1|V2 BASEDIR     пишет BASEDIR/combos.json ([combo]) и печатает присваивания shell: CP (classpar), Q5 (q5 или пусто), CLS (класс)
Только чтение spec; панели и окно не читаются."""
import json
import os
import sys


def main(argv):
    spec_path, which, vid, base = argv[1:5]
    sp = json.load(open(spec_path, encoding="utf-8"))
    vs = sp["variants"] if which == "main" else sp["gate"]["variants"]
    v = [x for x in vs if x["id"] == vid][0]
    base_dir = os.path.dirname(os.path.abspath(spec_path))
    cp = v["classpar"] if os.path.isabs(v["classpar"]) else os.path.normpath(os.path.join(base_dir, v["classpar"]))
    q5 = v.get("q5") or ""
    if q5 and not os.path.isabs(q5):
        q5 = os.path.normpath(os.path.join(base_dir, q5))
    os.makedirs(base, exist_ok=True)
    json.dump([v["combo"]], open(os.path.join(base, "combos.json"), "w"))
    print("CP='%s'" % cp)
    print("Q5='%s'" % q5)
    print("CLS='%s'" % v["cls"])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
