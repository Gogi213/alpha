#!/usr/bin/env python3
"""П-08: клетки-фильтры сигналов B1 → busy-replay → portfolio-sim (август + сентябрь).

Путь тот же, что у H9р/H14 П-07 (`p07-h9r-h14.py`): функции `busy_replay_for` / `portfolio_sim_for` берутся
импортом из `p07-h9h10.py` (база байт-в-байт тем же кодом), меняется только каталог выхода — `tmp-p08/run`.
keep-файлы клеток — из `p08-feats.py` (`<cov>/{aug,sep}-<sub>/keep-<клетка>.csv`), месяцы склеиваются.

Режимы:
  gate   — ворота (г) П-08 §12 п. 10: `p08-keepall` (все сигналы B1 из feats-файла) без потолка и с потолком 3
           против принятых закрытий TK-004 (`tmp-p07/h9-b-h9r-keep`, `tmp-p07/h9-b-cap3`) — байт в байт по
           ps-closes.json; печать только статуса.
  cells  — клетки из `--cells` (после заморозки охвата §12 п. 9); числа — в `tmp-p08/run/<клетка>/`, в вывод —
           только путь и число оставленных сигналов.

На деке: python3 bin/p08-run.py gate --cov tmp-p08/cov
"""
import argparse
import csv
import glob
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.expanduser("~/alpha")
CAP3 = {"p08-g126-5", "p08-g126-10", "p08-keepall-b2"}  # база B2 — потолок 3 (§5)
SUB_OF = {"g57": "g57", "g78": "g78", "g126": "g126", "g36": "g36", "g55": "g55", "g140": "g140", "g07": "g07"}


def load_p07base():
    for d in (HERE, os.path.join(HOME, "tmp-p07")):
        path = os.path.join(d, "p07-h9h10.py")
        if os.path.exists(path):
            spec = importlib.util.spec_from_file_location("p07base", path)
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
            m.OUT_ROOT = os.path.join(HOME, "tmp-p08", "run")
            return m
    sys.exit("нет p07-h9h10.py рядом и в ~/alpha/tmp-p07")


def read_rows(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def merged_keep(cov, cell, out_path):
    """keep клетки = объединение keep-файлов августа и сентября той же подкоманды."""
    sub = cell.split("-")[1]
    rows = []
    for m in ("aug", "sep"):
        p = os.path.join(cov, f"{m}-{SUB_OF[sub]}", f"keep-{cell}.csv")
        if not os.path.exists(p):
            sys.exit(f"нет {p}")
        rows += [(r["symbol"], r["t0_ns"], r["price_tick"]) for r in read_rows(p)]
    return rows


def keepall_rows(cov):
    """все сигналы B1 (без TRX) из feats-файла g57 обоих месяцев."""
    rows = []
    for m in ("aug", "sep"):
        p = os.path.join(cov, f"{m}-g57", "feats-g57.csv")
        rows += [(r["symbol"], r["t0_ns"], r["price_tick"]) for r in read_rows(p)]
    return rows


def run_cell(p, name, rows, max_pos):
    keep_path = os.path.join(p.OUT_ROOT, f"h9-b-{name}", "keep.csv")
    p.write_keep(sorted(set(rows)), keep_path)
    outs = p.busy_replay_for("b", name, keep_path)
    p.portfolio_sim_for("b", name, outs, max_pos=max_pos)
    return os.path.join(p.OUT_ROOT, f"h9-b-{name}", "ps-closes.json"), len(set(rows))


def same_file(a, b):
    with open(a, "rb") as fa, open(b, "rb") as fb:
        return fa.read() == fb.read()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["gate", "cells"])
    ap.add_argument("--cov", default=os.path.join(HOME, "tmp-p08/cov"))
    ap.add_argument("--cells", default="")
    a = ap.parse_args()
    p = load_p07base()
    if a.cmd == "gate":
        rows = keepall_rows(a.cov)
        ok = True
        for name, cap, ref in (("p08-keepall", 0, "tmp-p07/h9-b-h9r-keep/ps-closes.json"),
                               ("p08-keepall-b2", 3, "tmp-p07/h9-b-cap3/ps-closes.json")):
            got, n = run_cell(p, name, rows, cap)
            ref = os.path.join(HOME, ref)
            same = same_file(got, ref)
            if not same:  # ключи closes — по --max-pos; сравнить по содержимому того же потолка
                g, r = json.load(open(got, encoding="utf-8")), json.load(open(ref, encoding="utf-8"))
                same = all(g.get(v, {}).get(pk, {}).get(str(cap)) == r.get(v, {}).get(pk, {}).get(str(cap))
                           and r.get(v, {}).get(pk, {}).get(str(cap)) is not None
                           for v in g for pk in ("август", "сентябрь"))
            print(f"ворота (г) {name}: сигналов {n}, закрытия против {ref}: {'ЗЕЛЁНОЕ' if same else 'КРАСНОЕ'}")
            ok &= same
        print("ИТОГ ворот (г):", "ЗЕЛЁНОЕ" if ok else "КРАСНОЕ — стоп")
        sys.exit(0 if ok else 1)
    for cell in [c for c in a.cells.split(",") if c]:
        path, n = run_cell(p, cell, merged_keep(a.cov, cell, None), 3 if cell in CAP3 else 0)
        print(f"{cell}: оставлено сигналов {n} → {path}")


if __name__ == "__main__":
    main()
