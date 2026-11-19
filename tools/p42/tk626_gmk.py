#!/usr/bin/env python3
"""TK-626 / П-42 §13.28.1 п. 6: ворота G-626a (байт в байт против TK-621) и G-626c (G_mk §13.28 п. 6 заново на честной вселенной `all`, `t24=12`).
Запускать на srv под `systemd-run` (не с ПК). Драйвер движка, швы и статистика — TK-599 / TK-616 (`retitr_full_run.Engine`, `tk616_maker.gmk`).

  python3 tk626_gmk.py plan                                   # клетки, vid, ключи — без запуска
  TK626_GO=1 python3 tk626_gmk.py run --bin <alpha-mk>        # 6 клеток (L_base, S_base + L_mk1, L_mk2, S_mk1, S_mk2; все `t24=12`) на wa/wb/wc, вселенная all (775), швы G3
  python3 tk626_gmk.py gate-a                                 # G-626a: `L_base`, `S_base` побайтно против /data/p42-tk621/out/all_t24 (a112684a) → gate-a.json
  python3 tk626_gmk.py report [--nboot 100000]                # G_mk (одностороннее 5 %, Холм m = 4, g1–g3) → results.json + таблица

Корень счёта /data/p42-tk626/gmk: ссылки `wa`, `wb`, `wc` на деревья TK-621 (775 монет, кэш `a112684a`), склад сделок TK-599; свои `out/`, `var/`, `log/`. Кэша сигналов нет (ключ `t24` — часть сигнала).
Метрика — средний $ на сделку = ΣS/ΣN (gross − fees, без фандинга); единица статистики — сутки.
"""
from __future__ import annotations

import argparse
import filecmp
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import retitr_full as rf  # noqa: E402
import retitr_full_run as rr  # noqa: E402
import tk616_maker as mk  # noqa: E402
import tk626_freeze as fz  # noqa: E402

GO_ENV = "TK626_GO"
ROOT = "/data/p42-tk626/gmk"
TREES = "/data/p42-tk621"
UNIVERSE = TREES + "/universe/symbols-all.txt"
REF = TREES + "/out/all_t24"                       # выходы TK-621 (a112684a): <окно>/<L_base|S_base>/…
STORE = "/data/p42-tk599/tick-store"
FROZEN_OLD = os.path.join(HERE, "..", "..", "docs", "research", "freeze", "tk616-frozen.json")
FILES = ("trades.csv", "daily.csv", "coinday.csv", "entryrej.csv")
SIDE_FILE = {"L": "long", "S": "short"}
REF_NAME = {"L_base": "L_base", "S_base": "S_base"}


def cells():
    out = {c: rf.Req(fz.SIDE.get(c, c[0]), k, False) for c, k in {"L_base": fz.CELL_KEYS["L_base"], "S_base": fz.CELL_KEYS["S_base"], **fz.MAKER_KEYS}.items()}
    return out


def go_required(env=None):
    if (env if env is not None else os.environ).get(GO_ENV) != "1":
        raise rf.GuardError("%s=1 не задан: запуск движка запрещён до команды исполнителя (§13.28.1 п. 8, после PASS критика)" % GO_ENV)


def prepare_root(root=ROOT):
    os.makedirs(root, exist_ok=True)
    for name, tgt in (("wa", TREES + "/wa"), ("wb", TREES + "/wb"), ("wc", TREES + "/wc"), ("tick-store", STORE)):
        p = os.path.join(root, name)
        if not os.path.lexists(p):
            os.symlink(tgt, p)


def cmd_plan(a):
    for c, r in cells().items():
        print("%s vid %s  %s" % (c, r.vid, r.engine_keys))
    text, coins = rr.read_symbols(a.symbols)
    print("корень %s; вселенная %s (%d монет); бинарь %s" % (a.root, a.symbols, len(coins), a.bin or "(не задан)"))
    return 0


def check_bin(path, old_freeze=FROZEN_OLD):
    if not path or not os.path.exists(path):
        raise rf.GuardError("--bin: нет файла %r" % path)
    sha, md5 = rr.file_hashes(path)
    b = json.load(open(old_freeze, encoding="utf-8"))["binary"]
    if md5 != b["md5"] or sha != b["sha256"]:
        raise rf.GuardError("бинарь %s: md5 %s ≠ замороженному %s" % (path, md5, b["md5"]))
    return md5, sha


def cmd_run(a):
    go_required()
    md5, sha = check_bin(a.bin, a.old_freeze)
    prepare_root(a.root)
    text, coins = rr.read_symbols(a.symbols)
    cs = cells()
    paths = rr.Paths(a.root, tick_store=os.path.join(a.root, "tick-store"))
    os.makedirs(a.root, exist_ok=True)
    json.dump({"bin": a.bin, "md5": md5, "sha256": sha, "symbols_sha256": rf.sha256_file(a.symbols), "n_symbols": len(coins),
               "cells": {c: {"vid": r.vid, "keys": r.engine_keys} for c, r in cs.items()}}, open(os.path.join(a.root, "run.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    engine = rr.Engine(paths, rr.SubprocessRunner(), text, a.bin, a.threads, None, prefix="g")
    engine.launch(list(cs.values()))
    seam = {c: mk.seams(a.root, r.vid, r.side) for c, r in cs.items()}
    json.dump(seam, open(os.path.join(a.root, "seams.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    bad = {c: s["diff"][:5] for c, s in seam.items() if s["diff"]}
    if bad:
        raise rf.GuardError("G3 швов окон: %s" % bad)
    print("прогон закончен; бинарь md5 %s; швы окон: различий 0" % md5)
    return 0


def cmd_gate_a(a):
    cs = cells()
    n, diff = 0, []
    for w in mk.WINS:
        for c in ("L_base", "S_base"):
            r = cs[c]
            side = SIDE_FILE[r.side]
            for f in FILES:
                x = os.path.join(a.root, "out", w, r.vid, side + "-" + f)
                y = os.path.join(a.ref, w, REF_NAME[c], side + "-" + f)
                if not os.path.exists(x) and not os.path.exists(y):
                    continue
                n += 1
                if not os.path.exists(x) or not os.path.exists(y) or not filecmp.cmp(x, y, shallow=False):
                    diff.append("%s %s %s" % (w, c, side + "-" + f))
    run = json.load(open(os.path.join(a.root, "run.json"), encoding="utf-8"))
    res = {"ok": not diff and n > 0, "files": n, "diff": diff, "bin_md5": run["md5"], "ref": a.ref, "ref_bin": "a112684a495e61cf5219610e4eecff22"}
    json.dump(res, open(os.path.join(a.root, "gate-a.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("G-626a: %s, файлов %d, различий %d %s" % ("OK" if res["ok"] else "DIFF", n, len(diff), diff[:5]))
    return 0 if res["ok"] else 3


def f2(x, nd=2):
    return "—" if x is None or (isinstance(x, float) and np.isnan(x)) else ("%+." + str(nd) + "f") % x


def render(res):
    L = ["# G_mk на честной вселенной (all, t24=12; §13.28.1 п. 6, G-626c)", "",
         "| клетка | ΣS $ | сделок | $/сделку | p | порог Холма | месяцев плюс | g1 | g2 | g3 | прошла |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for c, g in res["gmk"].items():
        L.append("| %s | %s | %d | %s | %.4f | %.4f | %d из %d | %s | %s | %s | **%s** |" % (c, f2(g["sum"]), g["n"], f2(g["mean"], 3), g["p"], g["holm_threshold"], g["positive_months"],
                                                                                         g["counted_months"], g["g1"], g["g2"], g["g3"], "да" if g["pass"] else "нет"))
    L += ["", "Тейкер-базы (та же вселенная, t24=12): " + "; ".join("%s ΣS %s / %d сделок / %s $/сделку" % (s, f2(v["sum"]), v["n"], f2(v["mean"], 3)) for s, v in res["bases"].items())]
    L += ["", "| клетка | " + " | ".join("%s ΣS (n)" % m for m in rf.MONTH_NAMES) + " |", "|---|" + "---|" * len(rf.MONTH_NAMES)]
    for c, g in res["gmk"].items():
        L.append("| %s | " % c + " | ".join("%s (%d)" % (f2(m["sum"]), m["n"]) for m in g["months"]) + " |")
    return "\n".join(L) + "\n"


def cmd_report(a):
    cs = cells()
    text, coins = rr.read_symbols(a.symbols)
    stats, base = {}, {}
    for c, r in cs.items():
        S, N = mk.series_of(a.root, r.vid, r.side, coins)
        if c in ("L_base", "S_base"):
            base[r.side] = (S, N)
        else:
            stats[c] = {"side": r.side, "S": S, "N": N}
    res = {"gmk": mk.gmk(stats, base, a.nboot), "bases": {}, "nboot": a.nboot}
    for s, (S, N) in base.items():
        row = mk.month_rows(S, N)[-1]
        res["bases"]["%s_base" % s] = {"sum": row["sum"], "n": row["n"], "mean": row["mean"]}
    json.dump(res, open(os.path.join(a.root, "results.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=float)
    md = render(res)
    if a.md:
        open(a.md, "w", encoding="utf-8").write(md)
    print(md)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["plan", "run", "gate-a", "report"])
    ap.add_argument("--root", default=ROOT)
    ap.add_argument("--bin", default="")
    ap.add_argument("--symbols", default=UNIVERSE)
    ap.add_argument("--ref", default=REF)
    ap.add_argument("--old-freeze", default=FROZEN_OLD)         # на srv: /data/p42-tk616/tk616-frozen.json
    ap.add_argument("--threads", type=int, default=rr.THREADS)
    ap.add_argument("--nboot", type=int, default=mk.NBOOT)
    ap.add_argument("--md", default="")
    a = ap.parse_args(argv)
    try:
        return {"plan": cmd_plan, "run": cmd_run, "gate-a": cmd_gate_a, "report": cmd_report}[a.cmd](a)
    except rf.GuardError as e:
        print("ОСТАНОВКА (охрана): %s" % e, file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
