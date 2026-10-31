#!/usr/bin/env python3
"""TK-471: подмножество ячеек D2b для пересчёта Q-3 — только варианты, прошедшие Холм против C-X со знаком Δ>0 (1622: p_holm<0.05 & delta>0
в docs/findings/sancho-bp-exits-2026-10-31.csv). Для каждого файла ячеек исходного каталога cells-<tf>-<ev>-<dir>.csv оставляет строки,
чей (stop, take, par, be_r, ot, otp) встретился у прошедшего варианта того же (tf, ev, dir) (любая сторона). Пустые файлы не пишет.
python tk471_cells.py <sancho-bp-exits.csv> <family: Санчо|bp|S1-S5> <каталог ячеек исходный> <каталог выходной>"""
import os
import sys

import pandas as pd


def key(stop, take, par, be_r, ot, otp):
    f = lambda x: None if x is None or (isinstance(x, float) and x != x) or str(x).strip() in ("", "nan") else round(float(x), 9)
    return (str(stop), str(take), f(par), f(be_r), None if str(ot) in ("", "nan") else str(ot), f(otp))


def main():
    csvp, fam, src, dst = sys.argv[1:5]
    d = pd.read_csv(csvp)
    d = d[(d.family == fam) & (d.p_holm < 0.05) & (d.delta > 0)]
    keep = {}
    for r in d.itertuples(index=False):
        keep.setdefault((int(r.tf), r.ev, r.dir), set()).add(key(r.stop, r.take, r.par, r.be_r, r.ot, r.otp))
    os.makedirs(dst, exist_ok=True)
    nin = nout = 0
    for fn in sorted(os.listdir(src)):
        if not fn.startswith("cells-") or not fn[6:7].isdigit():
            continue
        stem = fn[6:-4]
        tf, rest = stem.split("-", 1)
        ev, dr = rest.rsplit("-", 1)
        ks = keep.get((int(tf), ev, dr))
        c = pd.read_csv(f"{src}/{fn}", dtype=str, keep_default_na=False)
        nin += len(c)
        if not ks:
            continue
        m = [key(r.stop, r.take, r.par, r.be_r, r.ot, r.otp) in ks for r in c.itertuples(index=False)]
        c = c[m]
        nout += len(c)
        if len(c):
            lines = open(f"{src}/{fn}").read().splitlines()
            sel = [ln for ln, ok in zip(lines[1:], m) if ok]
            with open(f"{dst}/{fn}", "w", newline="") as w:
                w.write("\n".join([lines[0]] + sel) + "\n")
    print(f"{fam}: ячеек в источнике {nin}, оставлено {nout}; прошедших вариантов (с учётом стороны) {len(d)}", file=sys.stderr)
    assert nout > 0


if __name__ == "__main__":
    main()
