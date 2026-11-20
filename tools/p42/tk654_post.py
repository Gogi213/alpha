#!/usr/bin/env python3
"""П-42 §13.54 (TK-654): команды после PASS критика (читают P&L). Помощники — `tk654_run.py`, срез сделок и статистика — `tk638_post.py`:
  python3 tk654_post.py cells    — движок `breakout tick`: клетки (`--ftab`, `fa=<клетка> fo=lo fv=0.5`) + контроль `CTRL` (1,0 у всех строк), окна wa / wb / wc × стороны
  python3 tk654_post.py g8       — G8: no_key = 0 и dropped движка = независимому пересчёту по таблицам; G-base: `CTRL` = база по списку сделок и P&L
  python3 tk654_post.py tables   — таблицы клеток (n, Σ $, $/сделку, Sharpe, месяцы, 90 % интервал, просадка, KPI, R:R, стоп, Δ к базе в паре по суткам) → res/cells54-<сторона>.json"""
from __future__ import annotations

import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import tk604_feat as tf  # noqa: E402
import tk604_run as r4  # noqa: E402
import tk604_stats as st  # noqa: E402
import tk625_run as r5  # noqa: E402
import tk634_run as r34  # noqa: E402
import tk638_post as p8  # noqa: E402
import tk654_run as t4  # noqa: E402

ENG, BASE = r5.ENG, r5.BASE
SIDES = st.SIDES
FV_PASS = p8.FV_PASS
CTRL = "CTRL"
U = t4.U
say = t4.say


def cell_ids(side):
    return [c["cid"] for c in json.load(open(t4.cells_path(side), encoding="utf-8"))["cells"]]


def variants(side):
    return [(cid, "%s%s fa=%s fo=lo fv=%s" % (BASE[side], U.extra, cid, FV_PASS)) for cid in cell_ids(side) + [CTRL]]


def run_ftab(wname, side):
    """Таблица прогона: таблица `counts` + столбец `CTRL` = 1,0 (контроль G-base)."""
    src, dst = t4.ftab_path(wname, side), t4.qq("ftab54r", "%s-%s.csv" % (wname, side))
    with open(src, encoding="utf-8") as f, open(dst, "w", encoding="utf-8", newline="\n") as g:
        g.write(f.readline().rstrip("\n") + "," + CTRL + "\n")
        for ln in f:
            g.write(ln.rstrip("\n") + ",1.0\n")
    return dst


def cell_dir(wname, side, cid):
    return os.path.join(t4.ROOT, "cells54", "%s-%s" % (wname, side), cid)


def cmd_cells(a):
    for w in st.TRADE_WINDOWS:
        for side in SIDES:
            out = os.path.join(t4.ROOT, "cells54", "%s-%s" % (w.name, side))
            if os.path.exists(out + "/DONE"):
                continue
            ftab = run_ftab(w.name, side)
            v = variants(side)
            r5.tick(U, w, v, out, {}, t4.qq("log", "cells54-%s-%s.log" % (w.name, side)), ftab=ftab)
            with open(out + "/DONE", "w", encoding="utf-8") as f:
                f.write("%d %s\n" % (len(v), r4.sha256(ftab)))
            say("cells54 %s-%s: %d вариантов готово" % (w.name, side, len(v)))
    say("cells готово")


def cmd_g8(a):
    bad, res = [], {}
    for w in st.TRADE_WINDOWS:
        for side in SIDES:
            ftab = t4.qq("ftab54r", "%s-%s.csv" % (w.name, side))
            dsym, drel = tf.read_sigsx_keys(os.path.join(U.base(w.name, side), ENG[side] + "-sigsx.csv"))
            dkeys = [(s_, int(r_)) for s_, r_ in zip(dsym, drel)]
            for cid, _ in variants(side):
                tab = r34.read_table_col(ftab, cid)
                miss = sum(1 for k in dkeys if k not in tab)
                exp_u = sum(1 for v in tab.values() if v == v and v < FV_PASS)
                exp_m = sum(1 for k in dkeys if k in tab and tab[k] < FV_PASS)
                m = json.load(open(os.path.join(cell_dir(w.name, side, cid), "meta.json"), encoding="utf-8"))[ENG[side]]
                fc = r4.filter_counters(m)
                res["%s/%s/%s" % (w.name, side, cid)] = dict(counters=fc, expected_unique=exp_u, expected_rows=exp_m, dump_without_row=miss)
                if miss or fc is None or fc["no_key"] != 0 or fc["dropped"] not in (exp_u, exp_m):
                    bad.append("%s/%s/%s: %s, ожидалось dropped %d / %d, строк дампа без строки таблицы %d" % (w.name, side, cid, fc, exp_u, exp_m, miss))
    gb = {}
    for side in SIDES:
        bser, _, bp = p8.load_cell(U, side, {w.name: (U.base(w.name, side), side) for w in st.TRADE_WINDOWS})
        cser, _, cp = p8.load_cell(U, side, {w.name: (cell_dir(w.name, side, CTRL), side) for w in st.TRADE_WINDOWS})
        same = p8.keys_of(cser) == p8.keys_of(bser) and len(cp) == len(bp) and bool(np.allclose(cp, bp, atol=1e-6))
        gb[side] = dict(same=same, n_ctrl=len(cp), n_base=len(bp))
        if not same:
            bad.append("G-base %s: CTRL ≠ база (%d / %d сделок)" % (side, len(cp), len(bp)))
    r4.write_json(t4.qq("gates", "g8.json"), dict(res=res, gbase=gb, bad=bad))
    say("G8 + G-base", "FAIL " + "; ".join(bad[:8]) if bad else "ok: no_key = 0 и dropped = пересчёту во всех %d прогонах; CTRL = база (L %d, S %d сделок)" % (len(res), gb["L"]["n_base"], gb["S"]["n_base"]))
    if bad:
        sys.exit(1)


def delta90(Sc, Nc, Sr, Nr):
    """Δ $/сделку (клетка − база) и Δ Σ $ по суткам в паре: те же блоки суток, что `tk604_stats.delta_stat` (зерно md5, блок 4, 20 000); 90 % — 5-й и 95-й перцентили."""
    d = st.delta_stat(Sc, Nc, Sr, Nr)
    idx = st.block_idx(len(Sc), np.random.default_rng(st.seed_of(Sc, Nc, Sr, Nr)))
    bSc, bNc, bSr, bNr = (np.asarray(x, dtype=float)[idx].sum(axis=1) for x in (Sc, Nc, Sr, Nr))
    ok = (bNc > 0) & (bNr > 0)
    bd = bSc[ok] / bNc[ok] - bSr[ok] / bNr[ok]
    bs = (np.asarray(Sc, dtype=float) - np.asarray(Sr, dtype=float))[idx].sum(axis=1)
    lo, hi = np.percentile(bd, [5, 95])
    slo, shi = np.percentile(bs, [5, 95])
    return dict(d_mean=d.d, lo90=float(lo), hi90=float(hi), p_le0=d.p, d_sum=d.dsum, sum_lo90=float(slo), sum_hi90=float(shi))


def cmd_tables(a):
    """База и все клетки со всеми колонками; Δ $/сделку и Δ Σ $ к базе в паре по суткам с 90 % интервалом."""
    for side in SIDES:
        bser, bk, bp = p8.load_cell(U, side, {w.name: (U.base(w.name, side), side) for w in st.TRADE_WINDOWS})
        bset = set(p8.keys_of(bser))
        Sr, Nr = st.day_sums(bser)
        out = dict(uni="h775", side=side, base=p8.cell_stats(bser, bk, bp), cells={})
        for cid in cell_ids(side):
            ser, kinds, pnl = p8.load_cell(U, side, {w.name: (cell_dir(w.name, side, cid), side) for w in st.TRADE_WINDOWS})
            r = p8.cell_stats(ser, kinds, pnl)
            ks = p8.keys_of(ser)
            r["in_base"] = sum(1 for k in ks if k in bset)
            r["in_base_share"] = r["in_base"] / len(ks) if ks else float("nan")
            Sc, Nc = st.day_sums(ser)
            r["delta"] = delta90(Sc, Nc, Sr, Nr)
            out["cells"][cid] = r
        r4.write_json(t4.qq("res", "cells54-%s.json" % side), out)
        say("== h775 %s: база %d сделок, клеток %d" % (side, len(bp), len(out["cells"])))
    say("tables готово")


COMMANDS = {"cells": cmd_cells, "g8": cmd_g8, "tables": cmd_tables}


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=list(COMMANDS))
    a = ap.parse_args()
    COMMANDS[a.cmd](a)


if __name__ == "__main__":
    main()
