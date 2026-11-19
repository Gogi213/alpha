#!/usr/bin/env python3
"""TK-626 / П-42 §13.28.1: сборка файла заморозки `docs/research/freeze/tk626-frozen.json` (версия `tk626-frozen-1`) — заменяет `tk616-frozen.json` (коммит 96e302480).
Берёт из прежней заморозки бинарь, геометрию окон / точек / долей α и ключи клеток; добавляет `t24=12`, честную вселенную (sha256 базовых списков 775 / 539 / 236 и `contracts.csv`),
исход G-626c (G_mk на `all` с `t24=12`: допуск `S_mk1`) и исход G-626a (байт в байт против TK-621). Результат проходит `tk616_forward.load_freeze`.

  python3 tk626_freeze.py --old docs/research/freeze/tk616-frozen.json --base-dir docs/findings/p42-honest-universe-2026-11-19 \\
      --gmk <results.json G-626c> --gate-a <gate-a.json> --out docs/research/freeze/tk626-frozen.json

α (§13.28.1 п. 3): `S_mk1` прошёл G-626c — `S_base` 0,03 / `S_mk1` 0,01 / `L_base` 0,01; не прошёл — `S_mk1` выходит из F, `S_base` 0,04 / `L_base` 0,01. Сумма 0,05.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import retitr_full as rf  # noqa: E402
import tk616_forward as fw  # noqa: E402
import tk616_freeze as old  # noqa: E402

T24 = "t24=12"
S_BASE = "lat=4 latx=4 pk=1 pb=1 fh=4 fk=120 fm=50 fb=0.5 ew=5 ex=0 tt=20 tb=0.1 rf=0.2 rs=2 bm=1 e=0.15 e2=0.05"
L_BASE = "lat=4 latx=4 pk=1 pb=1 fh=4 fk=120 fm=50 fb=0.5 ew=5 ex=0 rf=0.2 rs=20"
CELL_KEYS = {"S_base": "%s %s" % (S_BASE, T24), "S_mk1": "%s mk=1 %s" % (S_BASE, T24), "L_base": "%s %s" % (L_BASE, T24)}
MAKER_KEYS = {"L_mk1": "%s mk=1 %s" % (L_BASE, T24), "L_mk2": "%s mk=2 %s" % (L_BASE, T24), "S_mk1": CELL_KEYS["S_mk1"], "S_mk2": "%s mk=2 %s" % (S_BASE, T24)}   # §13.28 п. 6, m = 4
SIDE = {"S_base": "S", "S_mk1": "S", "L_base": "L"}
ALPHA_SPLIT_RULE = ("§13.28.1 п. 3: α(S_base) + α(S_mk1) + α(L_base) ≤ 0,05; кандидаты TK-618/620 (их нет) берут α только из доли α(S_base); "
                    "S_mk1 не прошёл G-626c — его 0,01 переходит к α(S_base)")


def alphas(smk1_passed):
    a = {"S_base": 0.03, "S_mk1": 0.01, "L_base": 0.01} if smk1_passed else {"S_base": 0.04, "L_base": 0.01}
    assert abs(sum(a.values()) - 0.05) < 1e-12
    return a


def vid_of(cell, keys=None):
    return rf.Req(SIDE.get(cell, cell[0]), keys or CELL_KEYS[cell], False).vid


def build(old_fz, old_sha256, base, contracts_sha256, gmk, gate_a, gmk_sha256="", gate_a_sha256=""):
    """old_fz — прежняя заморозка (словарь); base — {имя: {"sha256", "n"}} для all / crypto / tradfi; gmk — `results.json` G-626c (поле `gmk`); gate_a — исход G-626a;
    gmk_sha256 / gate_a_sha256 — sha256 файлов `results.json` / `gate-a.json` (`results_sha256` п. 6)."""
    for c, k in (("S_base", S_BASE), ("S_mk1", S_BASE + " mk=1"), ("L_base", L_BASE)):
        if old_fz["cells"][c]["keys"] != k:
            raise rf.GuardError("клетка %s: ключи прежней заморозки не те, что в §13.28 п. 2" % c)
    passed = bool(gmk["S_mk1"]["pass"])
    members = ["S_base", "S_mk1", "L_base"] if passed else ["S_base", "L_base"]
    cells = {c: {"side": SIDE[c], "keys": CELL_KEYS[c], "vid": vid_of(c), "family": True} for c in members}
    wins = old.windows()
    return {"version": fw.VERSION, "protocol": "П-42 §13.28.1 (поправка к §13.28 п. 7–8)",
            "supersedes": {"file": "docs/research/freeze/tk616-frozen.json", "commit": "96e302480", "sha256": old_sha256, "version": old_fz["version"]},
            "binary": old_fz["binary"],
            "universe": {"read": list(fw.UNI_NAMES), "decision": "all", "base": base, "contracts_sha256": contracts_sha256, "listing_after": fw.LISTING_AFTER,
                         "t24": "12"},
            "cells": cells, "alpha": alphas(passed), "point_shares": old.POINT_SHARES, "points": old.points(wins), "windows": wins, "alpha_split_rule": ALPHA_SPLIT_RULE,
            "gates": {"G-626a": dict(gate_a, results_sha256=gate_a_sha256),
                      "G-626c": {"smk1_passed": passed, "results_sha256": gmk_sha256, "cells": {c: {k: gmk[c][k] for k in ("side", "sum", "n", "mean", "p", "holm_threshold", "holm_reject", "positive_months", "counted_months",
                                                                                              "g1", "g2", "g3", "pass")} for c in sorted(gmk)}}}}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--old", required=True)
    ap.add_argument("--base-dir", required=True)
    ap.add_argument("--gmk", required=True)
    ap.add_argument("--gate-a", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    old_fz = json.load(open(a.old, encoding="utf-8"))
    base = {}
    for n in fw.UNI_NAMES:
        p = os.path.join(a.base_dir, "symbols-%s.txt" % n)
        base[n] = {"sha256": rf.sha256_file(p), "n": len([x for x in open(p, encoding="utf-8").read().replace("\n", ",").split(",") if x])}
    gmk = json.load(open(a.gmk, encoding="utf-8"))["gmk"]
    gate_a = json.load(open(a.gate_a, encoding="utf-8"))
    if not gate_a.get("ok"):
        raise rf.GuardError("G-626a не пройдены: заморозка не собирается (запасной вариант — §13.28.1 п. 8)")
    fz = build(old_fz, rf.sha256_file(a.old), base, rf.sha256_file(os.path.join(a.base_dir, "contracts.csv")), gmk, gate_a, rf.sha256_file(a.gmk), rf.sha256_file(a.gate_a))
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:                 # LF: sha256 файла = sha256 блоба в git (охрана `--freeze-sha256` на srv)
        f.write(json.dumps(fz, ensure_ascii=False, indent=1) + "\n")
    fw.load_freeze(a.out)
    print("записано %s: семейство %s, α %s" % (a.out, list(fz["cells"]), fz["alpha"]))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except rf.GuardError as e:
        print("ОСТАНОВКА (охрана): %s" % e, file=sys.stderr)
        sys.exit(3)
