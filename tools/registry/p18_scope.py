#!/usr/bin/env python3
"""TK-289 (П-18 §6, §9 п. 1): P-18-scope.json — 9 осей OFAT F1 (центр B1 + 73 клетки), m = 17, разбиение А/Б, канонические хеши.
Оси 1–5, 6а, 7, 8 — хеш клетки (форма/набор/σ); ось 6б (strength_min) — хеш варианта (семья p18b6 + квантиль), порог по месяцу
(P-18-thresholds.csv, считает проход меры — pending). Значения осей — из tools/compute/p18_plan.py (один источник с планом клеток).
    python tools/registry/p18_scope.py [--write-research] [--hashes <файл>]   # печатает проверку; --hashes пишет по строке «хеш<TAB>ось<TAB>значение»"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "compute"))
import cellhash as ch  # noqa: E402
import cellhash_reg as cr  # noqa: E402
import p17_scope as p17  # noqa: E402
import p18_plan as pl  # noqa: E402

RES = os.path.join(HERE, "..", "..", "docs", "research")
PAIRS = ["вход×σ", "стоп×тейк", "стоп×дедлайн", "тейк×дедлайн", "BTC×набор", "ttl×вход"]


def cfg(form=cr.B1_FORM, spec=p17.SET_B1, sigma=cr.B1_SIGMA):
    return {"form": form, "set": cr.set_of(spec), "exec": dict(cr.STD_EXEC), "sigma": sigma, "universe": cr.UNIVERSE["v171c"]}


def cfg_of(ax, v):
    """Конфиг клетки оси ax со значением v (ось 8 — окно σ, мин.); 6б — вариант p18b6 (порог по месяцу — отдельно)."""
    if ax == "8":
        return cfg(sigma=f"study/sigma{v}")
    key, _ = pl.AX[ax]
    p = dict(pl.B1)
    spec = "age=2700,side=bid"
    if key in p:
        p[key] = v
    if p["btc"] != "none":
        spec += f",btc4h_max={p['btc']}"
    if key == "usd_min":
        spec += f",usd_min={v}"
    if key == "flow+usd_min":
        sv, uv = v.split("|")
        spec += f",flow={sv}" + ("" if uv == "none" else f",usd_min={uv}")
    form = f"{pl.entry(p['to'])}-{p['stop']}-{p['take']}-{p['deadline']}-ttl{p['ttl']}"
    c = cfg(form, spec)
    if key == "strength_min":
        c["variant"] = {"family": "p18b6", "axes": {"strength_q": ch.num(int(v))}}
    return c


def cells():
    out = {}
    out["center"] = [{"axes": {}, "hash": ch.cell_hash(cfg())}]
    for ax, (key, vals) in pl.AX.items():
        rows = []
        for v in vals:
            row = {"axes": {key: v}}
            if key == "strength_min":
                row["threshold"] = "month"
            row["hash"] = ch.cell_hash(cfg_of(ax, v))
            rows.append(row)
        out[ax] = rows
    out["8"] = [{"axes": {"sigma_min": w}, "hash": ch.cell_hash(cfg_of("8", w))} for w in pl.AX["8"][1]]
    return out


def build():
    fam = cells()
    allh = [c["hash"] for cs in fam.values() for c in cs]
    assert len(allh) == len(set(allh)), "совпали хеши клеток"
    a, b = p17.split_ab()
    return {"protocol": "П-18 r3", "m": 17, "m_note": "9 осей + 6в (сила В-67 × usd_min, решение Судьи 19.10) + 6 пар (%s) + 1 стопка; фиксировано до первого исхода" % ", ".join(PAIRS), "seed": 18,
            "test_months": [4, 5, 6, 7, 8, 9], "boot": 20000, "window": "2026-01-01..2026-09-30 (В-211); октябрь — пометкой",
            "split": {"rule": "sha256(symbol) hex как целое, чётное -> A, нечётное -> B (П-13 §6 п. 1)", "pool": "v171c", "n_A": len(a), "n_B": len(b), "A": a, "B": b},
            "base": {"form": cr.B1_FORM, "set": p17.SET_B1, "sigma": cr.B1_SIGMA, "hash": ch.cell_hash(cfg())},
            "hash_rule": "оси 1–5, 6а, 7, 8 — хеш клетки (форма+набор+σ); ось 6б — хеш варианта (семья p18b6 + strength_q), порог strength_min — по месяцу",
            "pending": {"thresholds": ["6б strength_min по месяцам: P-18-thresholds.csv (квантили силы по сигналам B1 своего месяца)"], "sigma_tables": "окна 60…960 мин (sigma-table.py)"},
            "cells_total": len(allh), "families": fam}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write-research", action="store_true")
    ap.add_argument("--hashes")
    a = ap.parse_args()
    sc = build()
    print(json.dumps({"cells": sc["cells_total"], **{k: len(c) for k, c in sc["families"].items()}, "n_A": sc["split"]["n_A"], "n_B": sc["split"]["n_B"]}, ensure_ascii=False))
    if a.hashes:
        with open(a.hashes, "w", encoding="utf-8", newline="\n") as f:
            for ax, cs in sc["families"].items():
                for c in cs:
                    f.write(f"{c['hash']}\t{ax}\t{json.dumps(c['axes'], ensure_ascii=False)}\n")
    if a.write_research:
        json.dump(sc, open(os.path.join(RES, "P-18-scope.json"), "w", encoding="utf-8", newline="\n"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
