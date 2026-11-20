#!/usr/bin/env python3
"""П-42 §13.37 (TK-639): команды после PASS критика (читают P&L) — клетки лестниц движка, G8, таблицы клеток. Помощники — `tk639_run.py`, срез сделок и статистики — `tk638_post.py`:
  python3 tk639_post.py cells --uni h775|o566   — движок `breakout tick`: 32 клетки лестниц (`--ftab`, `fa=<клетка> fo=lo fv=0.5`), окна wa / wb / wc × стороны
  python3 tk639_post.py g8 --uni h775|o566      — G8: no_key = 0 и dropped движка = независимому пересчёту по таблицам `--ftab`
  python3 tk639_post.py tables --uni h775|o566  — таблицы клеток (n, Σ $, $/сделку, Sharpe, месяцы, 90 % интервал, просадка, KPI В-120, R:R, стоп) → res/cells39-<вс>-<сторона>.json"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import tk604_feat as tf  # noqa: E402
import tk604_run as r4  # noqa: E402
import tk604_stats as st  # noqa: E402
import tk625_run as r5  # noqa: E402
import tk634_run as r34  # noqa: E402
import tk638_post as p8  # noqa: E402
import tk639_run as t9  # noqa: E402

ENG, BASE = r5.ENG, r5.BASE
SIDES = st.SIDES
ROOT = t9.ROOT
FV_PASS = p8.FV_PASS
say, sub = t9.say, t9.sub


def q(*a):
    return t9.qq(*a)


def ladder_variants(u, side):
    thr = json.load(open(t9.thr_path(), encoding="utf-8"))[side]
    return [(c["cid"], "%s%s fa=%s fo=lo fv=%s" % (BASE[side], u.extra, c["cid"], FV_PASS)) for c in t9.cell_defs(thr)]


def kind_dir(u, kind, wname, side, cid):
    return os.path.join(ROOT, sub(u, kind), "%s-%s" % (wname, side), cid)


def ftab_path(u, wname, side):
    return q(sub(u, "ftab39"), "%s-%s.csv" % (wname, side))


def run_group(u, w, side, variants, ftab):
    out = os.path.join(ROOT, sub(u, "ladd39"), "%s-%s" % (w.name, side))
    if os.path.exists(out + "/DONE"):
        return
    r5.tick(u, w, variants, out, {}, q("log", "ladd39-%s-%s-%s.log" % (u.name, w.name, side)), ftab=ftab)
    with open(out + "/DONE", "w", encoding="utf-8") as f:
        f.write("%d %s\n" % (len(variants), r4.sha256(ftab)))
    say("ladd39 %s %s-%s: %d вариантов готово" % (u.name, w.name, side, len(variants)))


def cmd_cells(a):
    u = r5.Uni(a.uni)
    for w in st.TRADE_WINDOWS:
        for side in SIDES:
            run_group(u, w, side, ladder_variants(u, side), ftab_path(u, w.name, side))
    say("cells %s готово" % u.name)


def cmd_g8(a):
    """G8: `no_key = 0` и `dropped` движка = независимому пересчёту по таблице `--ftab` (значение < 0,5) — для всех прогонов лестниц (читает только счётчики фильтра)."""
    u = r5.Uni(a.uni)
    bad, res = [], {}
    for w in st.TRADE_WINDOWS:
        for side in SIDES:
            ftab = ftab_path(u, w.name, side)
            dsym, drel = tf.read_sigsx_keys(os.path.join(u.base(w.name, side), ENG[side] + "-sigsx.csv"))
            dkeys = [(s_, int(r_)) for s_, r_ in zip(dsym, drel)]
            for cid, _ in ladder_variants(u, side):
                tab = r34.read_table_col(ftab, cid)
                miss = sum(1 for k in dkeys if k not in tab)
                exp_u = sum(1 for v in tab.values() if v == v and v < FV_PASS)
                exp_m = sum(1 for k in dkeys if k in tab and tab[k] < FV_PASS)
                m = json.load(open(os.path.join(kind_dir(u, "ladd39", w.name, side, cid), "meta.json"), encoding="utf-8"))[ENG[side]]
                fc = r4.filter_counters(m)
                res["%s/%s/%s" % (w.name, side, cid)] = dict(counters=fc, expected_unique=exp_u, expected_rows=exp_m, dump_without_row=miss)
                if miss or fc is None or fc["no_key"] != 0 or fc["dropped"] not in (exp_u, exp_m):
                    bad.append("%s/%s/%s: %s, ожидалось dropped %d / %d, строк дампа без строки таблицы %d" % (w.name, side, cid, fc, exp_u, exp_m, miss))
    r4.write_json(q("gates", "g8-%s.json" % u.name), dict(res=res, bad=bad))
    say("G8 %s" % u.name, "FAIL " + "; ".join(bad[:8]) if bad else "ok: no_key = 0 и dropped = пересчёту во всех %d прогонах" % len(res))
    if bad:
        sys.exit(1)


def ln24_ref(u):
    """Сделки замороженного кандидата `L_n24` (клетка `N24_q60` TK-638, лонг; условие критика 20.11: у клетки, названной «вперёд», рядом с шумовой планкой печатается пересечение сделок с `L_n24`)."""
    dirs = {w.name: (p8.kind_dir(u, "ladd", w.name, "L", "N24_q60"), "L") for w in st.TRADE_WINDOWS}
    ser, _, _ = p8.load_cell(u, "L", dirs)
    return set(p8.keys_of(ser))


def overlap(ks, ref):
    c = set(ks)
    inter = len(c & ref)
    union = len(c | ref)
    return dict(ref=len(ref), cell=len(c), inter=inter, jaccard=inter / union if union else float("nan"),
                share_cell_in_ref=inter / len(c) if c else float("nan"), share_ref_in_cell=inter / len(ref) if ref else float("nan"))


def cmd_tables(a):
    """База и клетки лестниц: все клетки со всеми колонками, ни одна не отбрасывается; доля сделок клетки, что есть среди сделок базы."""
    u = r5.Uni(a.uni)
    for side in SIDES:
        base_dirs = {w.name: (u.base(w.name, side), side) for w in st.TRADE_WINDOWS}
        bser, bk, bp = p8.load_cell(u, side, base_dirs)
        bkeys = p8.keys_of(bser)
        bset = set(bkeys)
        out = dict(uni=u.name, side=side, base=p8.cell_stats(bser, bk, bp), ladders={})
        ref = ln24_ref(u) if side == "L" else None
        if ref is not None:
            out["base"]["ln24"] = overlap(bkeys, ref)
        for cid, _ in ladder_variants(u, side):
            ser, kinds, pnl = p8.load_cell(u, side, {w.name: (kind_dir(u, "ladd39", w.name, side, cid), side) for w in st.TRADE_WINDOWS})
            r = p8.cell_stats(ser, kinds, pnl)
            ks = p8.keys_of(ser)
            r["in_base"] = sum(1 for k in ks if k in bset)
            r["in_base_share"] = r["in_base"] / len(ks) if ks else float("nan")
            if ref is not None:
                r["ln24"] = overlap(ks, ref)
            out["ladders"][cid] = r
        r4.write_json(q("res", "cells39-%s-%s.json" % (u.name, side)), out)
        say("== %s %s: база %d сделок, клеток лестниц %d" % (u.name, side, len(bkeys), len(out["ladders"])))
    say("tables %s готово" % u.name)


COMMANDS = {"cells": cmd_cells, "g8": cmd_g8, "tables": cmd_tables}


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=list(COMMANDS))
    ap.add_argument("--uni", required=True, choices=("h775", "o566"))
    a = ap.parse_args()
    COMMANDS[a.cmd](a)


if __name__ == "__main__":
    main()
