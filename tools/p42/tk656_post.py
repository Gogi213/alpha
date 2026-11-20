#!/usr/bin/env python3
"""П-42 §13.56 (TK-656): движок и таблицы. Помощники — `tk656_run.py`, ряды и статистика — `tk638_post.py`, `tk642_run.py`:
  python3 tk656_post.py cells   — движок `breakout tick`: ALL1 (контроль) + 12 клеток на сторону (`--ftab`, `fa=<клетка> fo=lo fv=0.5`), окна wa / wb / wc. P&L не читает
  python3 tk656_post.py gate    — G-base (ALL1 = базе h775 TK-625 байт в байт: trades / daily / coinday / entryrej) и G8 (no_key = 0, dropped = пересчёту по `--ftab`). P&L не читает
  python3 tk656_post.py ncount  — без P&L: число сделок клеток, пересечение с базой по ключу (монета, мс сигнала)
  python3 tk656_post.py tables  — P&L (только после PASS критика): n, Σ $, $/сделку, Sharpe, месяцы, R:R, просадка; парная ΔΣ по суткам, Холм m = 12 на сторону, MDE, шумовая планка 12 / 24, устойчивость, вердикт п. 7"""
from __future__ import annotations

import filecmp
import json
import math
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
import tk642_run as k2  # noqa: E402
import tk656_run as t6  # noqa: E402
import selection_bias as sb  # noqa: E402  (путь — lib.py через tk638_post)

ENG, BASE = r5.ENG, r5.BASE
SIDES = st.SIDES
ROOT = t6.ROOT
FV_PASS = p8.FV_PASS
FILES = ("trades", "daily", "coinday", "entryrej")
ALPHA = 0.05
say, sub = t6.say, t6.sub


def cells_of(side):
    thr = json.load(open(t6.thr_path(), encoding="utf-8"))[side]
    return t6.cell_defs(thr)


M_HOLM = 4 * len(t6.WS)                                  # 12 на сторону
Z_HOLM = k2.zq(1 - ALPHA / (2 * M_HOLM))
Z_POW = k2.Z_POW


def variants(u, side):
    return [(cid, "%s%s fa=%s fo=lo fv=%s" % (BASE[side], u.extra, cid, FV_PASS)) for cid in ["ALL1"] + [c["cid"] for c in cells_of(side)]]


def ftab_path(u, wname, side):
    return t6.qq(sub(u, "ftab56"), "%s-%s.csv" % (wname, side))


def kind_dir(u, wname, side, cid):
    return os.path.join(ROOT, sub(u, "cells56"), "%s-%s" % (wname, side), cid)


def cmd_cells(a):
    u = r5.Uni(t6.UNI)
    for w in st.TRADE_WINDOWS:
        for side in SIDES:
            out = os.path.join(ROOT, sub(u, "cells56"), "%s-%s" % (w.name, side))
            if os.path.exists(out + "/DONE"):
                continue
            ftab = ftab_path(u, w.name, side)
            v = variants(u, side)
            r5.tick(u, w, v, out, {}, t6.qq("log", "cells56-%s-%s.log" % (w.name, side)), ftab=ftab)
            with open(out + "/DONE", "w", encoding="utf-8") as f:
                f.write("%d %s\n" % (len(v), r4.sha256(ftab)))
            say("cells56 %s-%s: %d вариантов готово" % (w.name, side, len(v)))


def cmd_gate(a):
    u = r5.Uni(t6.UNI)
    bad, res = [], dict(gbase={}, g8={})
    for w in st.TRADE_WINDOWS:
        for side in SIDES:
            for fn in FILES:
                a_ = os.path.join(kind_dir(u, w.name, side, "ALL1"), "%s-%s.csv" % (ENG[side], fn))
                b_ = os.path.join(u.base(w.name, side), "%s-%s.csv" % (ENG[side], fn))
                same = os.path.exists(a_) and os.path.exists(b_) and filecmp.cmp(a_, b_, shallow=False)
                res["gbase"]["%s-%s/%s" % (w.name, side, fn)] = same
                if not same:
                    bad.append("G-base %s-%s %s" % (w.name, side, fn))
            ftab = ftab_path(u, w.name, side)
            dsym, drel = tf.read_sigsx_keys(os.path.join(u.base(w.name, side), ENG[side] + "-sigsx.csv"))
            dkeys = [(s_, int(r_)) for s_, r_ in zip(dsym, drel)]
            for cid, _ in variants(u, side):
                tab = r34.read_table_col(ftab, cid)
                miss = sum(1 for k in dkeys if k not in tab)
                exp_u = sum(1 for v in tab.values() if v == v and v < FV_PASS)
                exp_m = sum(1 for k in dkeys if k in tab and tab[k] < FV_PASS)
                m = json.load(open(os.path.join(kind_dir(u, w.name, side, cid), "meta.json"), encoding="utf-8"))[ENG[side]]
                fc = r4.filter_counters(m)
                res["g8"]["%s/%s/%s" % (w.name, side, cid)] = dict(counters=fc, expected_unique=exp_u, expected_rows=exp_m, dump_without_row=miss)
                if miss or fc is None or fc["no_key"] != 0 or fc["dropped"] not in (exp_u, exp_m):
                    bad.append("G8 %s/%s/%s: %s, ожидалось %d / %d, без строки %d" % (w.name, side, cid, fc, exp_u, exp_m, miss))
    res["bad"] = bad
    r4.write_json(t6.qq("gates", "gbase-g8.json"), res)
    say("G-base + G8:", "FAIL " + "; ".join(bad[:8]) if bad else "ok: %d файлов = базе, %d прогонов G8" % (len(res["gbase"]), len(res["g8"])))
    if bad:
        sys.exit(1)


def load(u, side, cid):
    dirs = {w.name: (kind_dir(u, w.name, side, cid) if cid else u.base(w.name, side), side) for w in st.TRADE_WINDOWS}
    return p8.load_cell(u, side, dirs)


def cmd_ncount(a):
    """Без P&L: только ключи сделок (монета, мс сигнала) из рядов; суммы не печатаются."""
    u = r5.Uni(t6.UNI)
    out = {}
    for side in SIDES:
        bk = set(p8.keys_of(load(u, side, None)[0]))
        out[side] = dict(base=len(bk), cells={})
        for c in cells_of(side):
            k = set(p8.keys_of(load(u, side, c["cid"])[0]))
            out[side]["cells"][c["cid"]] = dict(n=len(k), in_base=len(k & bk), only_cell=len(k - bk), only_base=len(bk - k))
        say("%s база %d | " % (side, len(bk)) + " ".join("%s:%d(+%d/−%d)" % (cid, v["n"], v["only_cell"], v["only_base"]) for cid, v in out[side]["cells"].items()))
    r4.write_json(t6.qq("res", "ncount56.json"), out)


def sharpe_noise(sharpe, trials=2 * M_HOLM):
    """Шумовая планка Шарпа по всем 24 клеткам (`selection_bias.py`; SE для 136 независимых суток, как TK-639)."""
    se = math.sqrt(365.0 / st.NDAYS * (1 + sharpe * sharpe / 730.0))
    return sb.significance(sharpe, se, trials)


def verdict(d, rej, rb):
    if rej and d["dsum"] > 0 and all(x > 0 for x in d["windows"]) and rb["holds"]:
        return "фильтр лучше базы (найдено на этих данных)"
    if rej and d["dsum"] < 0 and all(x < 0 for x in d["windows"]) and rb["holds"]:
        return "фильтр хуже базы"
    if rej:
        return "различие значимо, но не устойчиво (окна / сутки / монета)"
    return "не различимо: |ΔΣ| < MDE %.0f $ не исключено" % ((Z_HOLM + Z_POW) * d["se_sum"])


def cmd_tables(a):
    u = r5.Uni(t6.UNI)
    res = dict(m_holm=M_HOLM, z_holm=Z_HOLM, z_pow=Z_POW, noise={}, sides={})
    for n in (M_HOLM, 2 * M_HOLM):
        res["noise"][str(n)] = dict(zip(("emax_abs_z", "p95_max_abs_z"), k2.emax_abs_z(n, seed=656)))
    for side in SIDES:
        bser, bk, bp = load(u, side, None)
        r = dict(base=p8.cell_stats(bser, bk, bp), cells={})
        r["base"]["s42"] = k2.summary(bser)
        rows = []
        for c in cells_of(side):
            ser, kinds, pnl = load(u, side, c["cid"])
            d = k2.delta(ser, bser)
            d["mde_sum_holm"] = (Z_HOLM + Z_POW) * d["se_sum"]
            d["mde_mean_holm"] = (Z_HOLM + Z_POW) * d["se_mean"]
            rows.append((c, ser, kinds, pnl, d))
        rej = k2.holm([x[4]["p2"] for x in rows])
        for (c, ser, kinds, pnl, d), rj in zip(rows, rej):
            rb = k2.robust(ser, bser, d["dsum"])
            cs = p8.cell_stats(ser, kinds, pnl)
            cs["s42"] = k2.summary(ser)
            r["cells"][c["cid"]] = dict(cs, label=t6.label(c), sharpe_noise=sharpe_noise(cs["s42"]["sharpe"]), delta=d, holm_reject=bool(rj), robust=rb, verdict=verdict(d, rj, rb))
        res["sides"][side] = r
    r4.write_json(t6.qq("res", "tables56.json"), res)
    say("шум: max|z| из 12 — среднее %.2f, p95 %.2f; из 24 — %.2f / %.2f" % (res["noise"]["12"]["emax_abs_z"], res["noise"]["12"]["p95_max_abs_z"], res["noise"]["24"]["emax_abs_z"], res["noise"]["24"]["p95_max_abs_z"]))
    for side, r in res["sides"].items():
        b = r["base"]["s42"]
        say("== %s: база n %d, Σ %+.1f $, %+.3f $/сд., Sharpe %.2f" % (side, b["n"], b["sum"], b["mean"], b["sharpe"]))
        for cid, v in r["cells"].items():
            d, s4 = v["delta"], v["s42"]
            say("  %-12s n %4d Σ %+7.1f $/сд %+.3f Sh %5.2f | ΔΣ %+7.1f [%+.0f; %+.0f] p %.4f z %+.2f MDE %.0f окна %s | %s" % (
                cid, s4["n"], s4["sum"], s4["mean"], s4["sharpe"], d["dsum"], d["lo95"], d["hi95"], d["p2"], d["z"], d["mde_sum_holm"],
                "/".join("%+.0f" % x for x in d["windows"]), v["verdict"]))
            say("               Шарп-планка 24: %.2f, дефл. %.2f" % (v["sharpe_noise"]["noise_bar"], v["sharpe_noise"]["deflated"]))


COMMANDS = {"cells": cmd_cells, "gate": cmd_gate, "ncount": cmd_ncount, "tables": cmd_tables}


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=list(COMMANDS))
    a = ap.parse_args()
    COMMANDS[a.cmd](a)


if __name__ == "__main__":
    main()
