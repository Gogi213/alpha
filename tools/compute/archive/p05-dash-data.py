#!/usr/bin/env python3
"""Данные раздела «Сетки П-05» и переключателя «Размер позиции» дашборда (владелец 27.09 через CEO).

Вход (не в git, `data/p05/`):
    protection-p05-{a1,a2,b,c}.json   portfolio-sim по партиям сетки (счёт $2500, позиция $500)
    p05-{a1,a2,b,c}.json              чтение `p05-read.py`: Δ к главному по суткам с 95 % блочным бутстрепом
    protection-p05-lev-x{1,2,3,5}.json  точные прогоны `--size-mult` (главный + 5 точек П-05)

Выход — один JSON для `titration-dashboard-build.py --merge p05=<файл>`:
    cells     клетка → оси (возраст мин, размер $, сила %), партия, по месяцам n/$/Δ[ci]/просадка/доля в плюс
    account   период (aug/sep/augsep) → "p05:<клетка>" → сводка счёта в формате страницы (`account_summary` merge)
    size_dd   период → ключ варианта страницы → {"2": dd_pct, "3": …, "5": …} — точная просадка при позиции ×k
              (у остальных вариантов страница пересчитывает долларовые поля линейно, просадку в % — оценкой)

    p05-dash-data.py --p05-dir data/p05 --out data/titration-dashboard/p05-dash.json
"""
import argparse
import importlib.util
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
PARTS = ["a1", "a2", "b", "c"]
PERIOD_EPOCH = {"aug": "август", "sep": "сентябрь", "augsep": "август+сентябрь"}
MONTH = {"aug": "август", "sep": "сентябрь"}
SPAN_MIN = {"aug": 31 * 1440, "sep": 23 * 1440, "augsep": 54 * 1440}
NAME_RE = re.compile(r"^p05(?:-f(?P<f>[0-9_]+))?(?:-a(?P<a>\d+))?(?:-u(?P<u>\d+)k)?$")
# точные прогоны размера: имя варианта в protection-p05-lev-x*.json → ключ страницы
LEV_KEYS = {"главный": "btc4h_trail"}


def _merge_module():
    spec = importlib.util.spec_from_file_location("tdm", os.path.join(HERE, "titration-dashboard-merge.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--p05-dir", default="data/p05")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    M = _merge_module()
    combo = dict(max_pos=0, day_stop=0.0, kill=0.0, exclude_name="нет")
    cells, account = {}, {p: {} for p in PERIOD_EPOCH}
    for part in PARTS:
        prot = json.load(open(os.path.join(a.p05_dir, f"protection-p05-{part}.json"), encoding="utf-8"))
        read = json.load(open(os.path.join(a.p05_dir, f"p05-{part}.json"), encoding="utf-8"))
        dep = prot["deposit_usd"]
        for v in prot["variants"]:
            name = v["name"]
            if name == "главный" or name in cells:
                continue
            m = NAME_RE.match(name)
            assert m, name
            ax = {"age": int(m["a"]) if m["a"] else None, "usd": int(m["u"]) * 1000 if m["u"] else None,
                  "force": float(m["f"].replace("_", ".")) if m["f"] else None}
            months = {}
            for pk, mname in MONTH.items():
                r = read[name][mname]
                months[pk] = {"n": r["n"], "usd": r["usd"], "delta": r["delta"], "lo": r["delta_ci"][0],
                              "hi": r["delta_ci"][1], "dd_pct": r["dd_pct"], "win": r["win"]}
            cells[name] = {"part": part, **ax, "months": months}
            for pk, ep in PERIOD_EPOCH.items():
                account[pk]["p05:" + name] = M.account_summary(M.account_row(prot["grid"], name, ep, **combo), dep,
                                                               SPAN_MIN[pk])
    size_dd = {p: {} for p in PERIOD_EPOCH}
    for k in ("2", "3", "5"):
        lev = json.load(open(os.path.join(a.p05_dir, f"protection-p05-lev-x{k}.json"), encoding="utf-8"))
        names = {v["name"]: v["set"] for v in lev["variants"]}
        for vname, vset in names.items():
            key = LEV_KEYS.get(vname) or ("p05:" + vset if vset in cells else None)
            if key is None:
                continue
            for pk, ep in PERIOD_EPOCH.items():
                g = M.account_row(lev["grid"], vname, ep, **combo)
                if g is not None:
                    size_dd[pk].setdefault(key, {})[k] = round(g["dd_pct"], 3)
    out = {"status": "на проверке у Судьи", "cells": cells, "account": account, "size_dd": size_dd,
           "note": "Сетки П-05 — на тех же днях подбора (август, сентябрь), без поправки на число клеток; вердикт — "
                   "только по протоколу после «принято» Судьи. Δ — разница с главным по суткам, 95 % блочный бутстреп."}
    with open(a.out, "w", encoding="utf-8", newline="") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print(f"{a.out}: клеток {len(cells)}, {os.path.getsize(a.out) / 1e6:.2f} МБ")


if __name__ == "__main__":
    main()
