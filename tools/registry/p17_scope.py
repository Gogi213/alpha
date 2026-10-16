#!/usr/bin/env python3
"""TK-279 (П-17 §0 п. 3, §3): P-17-scope.json — 6 гипотез, оси F1 (301 клетка), m = 6, разбиение А/Б, канонические хеши.
Г-98/Г-136/Г-137 — хеш набора (ключи size_risk/agemax/repeat_min входят в канон); Г-81/Г-101/Г-135 — порог по месяцам, хеш варианта
(семья g81/g101/g135 + оси), как Г-10 и П-15. Пороги (P-17-thresholds.csv) этих трёх — по касаниям B1 своего месяца, считает проход
меры (pending, см. scope.pending); хеши от них не зависят.
    python tools/registry/p17_scope.py [--write-research]   # печатает проверку; с флагом пишет docs/research/P-17-scope.json"""
import argparse
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cellhash as ch  # noqa: E402
import cellhash_reg as cr  # noqa: E402

BASE = cr.B1_FORM  # B1 (П-17 вводная)
SET_B1 = "age=2700,side=bid,btc4h_max=-44.55"
Q9 = (5, 10, 20, 33, 50, 67, 80, 90, 95)
W81 = (5, 10, 15, 20, 30, 45, 60, 90)  # с
R98 = (2, 3, 5, 8, 12, 20, 30, 50, 80)  # $
F101 = (("quarter", 2500), ("half", 5000), ("3q", 7500), ("3half", 15000), ("x2", 20000), ("x4", 40000))  # бп: f = бп/10⁴
KIND101 = ("ladder", "stop")
R135 = (2, 5, 10, 20, 40)  # bps
A136 = (2, 5, 10, 15, 20, 30, 45)  # мин
K136 = (0, 1, 2, 3, 4, 6, 8)  # 0 — контроль
K137 = (1, 2, 3, 4, 5, 6, 8, 10, 12)
FIND = os.path.join(HERE, "..", "..", "docs", "findings")
RES = os.path.join(HERE, "..", "..", "docs", "research")


def cfg_of(form=BASE, spec=SET_B1):
    return {"form": form, "set": cr.set_of(spec), "exec": dict(cr.STD_EXEC), "sigma": cr.B1_SIGMA, "universe": cr.UNIVERSE["v171c"]}


def variant_hash(fam, axes):
    cfg = cfg_of()
    cfg["variant"] = {"family": fam, "axes": {k: ch.num(v) for k, v in axes.items()}}
    return ch.cell_hash(cfg)


def cells():
    out = {k: [] for k in ("Г-81", "Г-98", "Г-101", "Г-135", "Г-136", "Г-137")}
    for w in W81:
        for q in Q9:
            ax = {"W_s": w, "cxl_q": q}
            out["Г-81"].append({"axes": ax, "hash": variant_hash("g81", ax), "threshold": "month"})
    for r in R98:
        spec = f"{SET_B1},size_risk={r}"
        out["Г-98"].append({"axes": {"R_usd": r}, "set": spec, "hash": ch.cell_hash(cfg_of(spec=spec))})
    for kind in KIND101:
        for t in Q9:
            for fn, f in F101:
                ax = {"part": kind, "T_q": t, "f_bp": f}
                out["Г-101"].append({"axes": ax, "hash": variant_hash("g101", ax), "threshold": "month"})
    for q in Q9:
        for ent in ("b1",) + R135:
            ax = {"swept_q": q, "entry": ent if ent == "b1" else f"postsweep{ent}"}
            form = BASE if ent == "b1" else BASE.replace("ladder3x0..0.0409sw2", f"postsweep{ent}")
            c = {"axes": ax, "form": form, "hash": variant_hash("g135", ax), "threshold": "month"}
            out["Г-135"].append(c)
    for a in A136:
        for k in K136:
            spec = f"side=bid,btc4h_max=-44.55,agemax={a * 60},repeat_min={k}"
            c = {"axes": {"A_min": a, "k": k}, "set": spec, "hash": ch.cell_hash(cfg_of(spec=spec))}
            if k == 0:
                c["control"] = True
            out["Г-136"].append(c)
    for k in K137:
        spec = f"{SET_B1},repeat_min={k}"
        out["Г-137"].append({"axes": {"k": k}, "set": spec, "hash": ch.cell_hash(cfg_of(spec=spec))})
    return out


def split_ab():
    sp = json.load(open(os.path.join(FIND, "p13-split-2026-10-09.json"), encoding="utf-8"))
    pool = sorted(sp["A"] + sp["B"])
    is_a = lambda x: int(hashlib.sha256(x.encode()).hexdigest(), 16) % 2 == 0  # noqa: E731
    a, b = [x for x in pool if is_a(x)], [x for x in pool if not is_a(x)]
    assert a == sorted(sp["A"]) and b == sorted(sp["B"]), "разбиение А/Б разошлось с p13-split"
    return a, b


def build():
    fam = cells()
    allh = [c["hash"] for cs in fam.values() for c in cs]
    assert len(allh) == len(set(allh)), "совпали хеши клеток"
    a, b = split_ab()
    return {"protocol": "П-17 r2", "m": 6, "m_note": "семья = гипотеза; Г-101 — одна семья из двух подвариантов (ladder|stop)", "seed": 17, "test_months": [4, 5, 6, 7, 8, 9], "boot": 20000,
            "window": "Г-81/Г-98/Г-101: 2026-01-01..2026-09-30; Г-135/Г-136/Г-137 (Д-класс): сито и финал 01.01–31.07, авг/сен печатаются (П-17 §4)",
            "split": {"rule": "sha256(symbol) hex как целое, чётное -> A, нечётное -> B (П-13 §6 п. 1)", "pool": "v171c", "n_A": len(a), "n_B": len(b), "A": a, "B": b},
            "base": {"form": BASE, "set": SET_B1, "hash": ch.cell_hash(cfg_of())},
            "hash_rule": "Г-98/Г-136/Г-137 — хеш набора (size_risk/agemax/repeat_min в каноне); Г-81/Г-101/Г-135 — хеш варианта (семья + оси), порог по месяцам из P-17-thresholds.csv",
            "pending": {"thresholds": ["Г-81", "Г-135"], "done": {"Г-101": "docs/research/P-17-thresholds.csv (tools/analyze/p17-btc4h-thresholds.py, btc4h по касаниям B1 своего месяца, 9 мес., 2751 касание)"}, "why": "Г-81 — мера отмен за W от взвода, Г-135 — swept_lots/size_at_touch: нужен проход меры в Rust (диагностический вывод по касаниям B1)"},
            "cells_total": len(allh), "families": fam}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write-research", action="store_true")
    a = ap.parse_args()
    sc = build()
    print(json.dumps({"cells": sc["cells_total"], **{h: len(c) for h, c in sc["families"].items()}, "n_A": sc["split"]["n_A"], "n_B": sc["split"]["n_B"]}, ensure_ascii=False))
    if a.write_research:
        json.dump(sc, open(os.path.join(RES, "P-17-scope.json"), "w", encoding="utf-8", newline="\n"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
