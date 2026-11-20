#!/usr/bin/env python3
"""П-42 §13.36 (TK-638): команды после PASS критика (читают P&L) — клетки движка, G8, G-base / G-pool, таблицы клеток. Помощники — `tk638_run.py`:
  python3 tk638_post.py cells --uni h775|o566    — движок `breakout tick`: клетки пула (D × H; у o566 ещё строка без ключа D) и лестницы n60 / n24 (`--ftab`), окна wa / wb / wc × стороны
  python3 tk638_post.py g8 --uni h775|o566       — G8: no_key = 0 и dropped движка = независимому пересчёту по таблицам `--ftab`
  python3 tk638_post.py tables --uni h775|o566   — G-base, G-pool, таблицы клеток (n, Σ $, $/сделку, Sharpe, месяцы, 90 % интервал, просадка, KPI В-120, R:R, стоп) → res/cells38-<вс>-<сторона>.json"""
from __future__ import annotations

import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import lib  # noqa: E402
import tk604_feat as tf  # noqa: E402
import tk604_run as r4  # noqa: E402
import tk604_stats as st  # noqa: E402
import tk620_rr as rr  # noqa: E402
import tk625_run as r5  # noqa: E402
import tk634_run as r34  # noqa: E402
import tk638_run as t8  # noqa: E402

ENG, BASE = r5.ENG, r5.BASE
SIDES = st.SIDES
ROOT = t8.ROOT
FV_PASS = 0.5                                                    # движок: `fa=<клетка> fo=lo fv=0.5` — исключает значение 0,0, пропускает 1,0
MARGIN = rr.MARGIN
say, q, sub = t8.say, t8.q, t8.sub


def pool_variants(u, side):
    """Клетки пула: «торгуем, только если» — ключи движка `t24=D t1h=H` вместо базовых, остальное — база стороны БЕЗ `u.extra`; у o566 ещё строка «без ключа D» (`t1h=H`)."""
    v = [(t8.cell_id(d, h), "%s t24=%g t1h=%g" % (BASE[side], d, h)) for d in t8.D_LEVELS for h in t8.H_LEVELS]
    if u.name == "o566":
        v += [("Dnone_H%g" % h, "%s t1h=%g" % (BASE[side], h)) for h in t8.H_LEVELS]
    return v


def ladder_variants(u, side):
    thr = json.load(open(t8.thr_path(), encoding="utf-8"))[side]
    return [(c["cid"], "%s%s fa=%s fo=lo fv=%s" % (BASE[side], u.extra, c["cid"], FV_PASS)) for c in t8.ladder_cells(thr)]


def kind_dir(u, kind, wname, side, cid):
    return os.path.join(ROOT, sub(u, kind), "%s-%s" % (wname, side), cid)


def ftab_path(u, wname, side):
    return q(sub(u, "ftab38"), "%s-%s.csv" % (wname, side))


def run_group(u, w, side, kind, variants, ftab=None):
    out = os.path.join(ROOT, sub(u, kind), "%s-%s" % (w.name, side))
    if os.path.exists(out + "/DONE"):
        return
    r5.tick(u, w, variants, out, {}, q("log", "%s-%s-%s-%s.log" % (kind, u.name, w.name, side)), ftab=ftab)
    with open(out + "/DONE", "w", encoding="utf-8") as f:
        f.write("%d %s\n" % (len(variants), r4.sha256(ftab) if ftab else "-"))
    say("%s %s %s-%s: %d вариантов готово" % (kind, u.name, w.name, side, len(variants)))


def cmd_cells(a):
    u = r5.Uni(a.uni)
    for w in st.TRADE_WINDOWS:
        for side in SIDES:
            run_group(u, w, side, "pool", pool_variants(u, side))
            run_group(u, w, side, "ladd", ladder_variants(u, side), ftab_path(u, w.name, side))
    say("cells %s готово" % u.name)


def read_tables(dirs):
    """({окно: TradeTable}, {окно: daily}) по каталогам вариантов окон."""
    tables, daily = {}, {}
    for w in st.TRADE_WINDOWS:
        d, side = dirs[w.name]
        tables[w.name] = st.read_trades_csv(os.path.join(d, ENG[side] + "-trades.csv"))
        daily[w.name] = st.read_daily_csv(os.path.join(d, ENG[side] + "-daily.csv"))
    return tables, daily


def kinds_pnl(tables):
    """Вид выхода и P&L ($, gross − fees) в порядке `st.splice` (охрана порядка — равенство с val ряда)."""
    K, P = [], []
    for w in st.TRADE_WINDOWS:
        t = tables[w.name]
        d = st.abs_day(w, t.entry_ms)
        lo_i, hi_i = (w.lod - st.D0).days, (w.hid - st.D0).days
        for i in np.nonzero((d >= lo_i) & (d <= hi_i))[0]:
            K.append(t.kind[i])
            P.append(float(t.gross[i] - t.fees[i]))
    return K, np.array(P, dtype=float)


def load_cell(u, side, dirs):
    tables, daily = read_tables(dirs)
    ser = st.splice(tables, u.coins(), daily=daily)
    kinds, pnl = kinds_pnl(tables)
    if len(pnl) != len(ser.val) or not np.allclose(pnl, ser.val, atol=1e-6):
        raise t8.GuardError("%s %s: P&L сделок из файла ≠ val ряда" % (u.name, side))
    return ser, kinds, pnl


def cell_stats(ser, kinds, pnl):
    r = r34.slice_plus(ser.day, ser.val)
    Sd = np.bincount(ser.day, weights=ser.val, minlength=st.NDAYS)
    cum = np.concatenate([[0.0], np.cumsum(Sd)])
    r["maxdd"] = float((np.maximum.accumulate(cum) - cum).max())
    r["maxdd_pct_margin"] = r["maxdd"] / MARGIN * 100
    r["kpi"] = lib.drawdown_kpi(Sd)
    r["rr"] = rr.rr_stats([dict(pnl=p, kind=k) for p, k in zip(pnl, kinds)])
    r["few"] = bool(r["n"] < t8.MIN_TRADES)
    r["months_few"] = [bool(m[0] < 20) for m in r["months"]]
    return r


def keys_of(ser):
    return list(zip(ser.sym_name, [int(x) for x in ser.key_ms]))


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
                m = json.load(open(os.path.join(kind_dir(u, "ladd", w.name, side, cid), "meta.json"), encoding="utf-8"))[ENG[side]]
                fc = r4.filter_counters(m)
                res["%s/%s/%s" % (w.name, side, cid)] = dict(counters=fc, expected_unique=exp_u, expected_rows=exp_m, dump_without_row=miss)
                if miss or fc is None or fc["no_key"] != 0 or fc["dropped"] not in (exp_u, exp_m):
                    bad.append("%s/%s/%s: %s, ожидалось dropped %d / %d, строк дампа без строки таблицы %d" % (w.name, side, cid, fc, exp_u, exp_m, miss))
    r4.write_json(q("gates", "g8-%s.json" % u.name), dict(res=res, bad=bad))
    say("G8 %s" % u.name, "FAIL " + "; ".join(bad[:8]) if bad else "ok: no_key = 0 и dropped = пересчёту во всех %d прогонах" % len(res))
    if bad:
        sys.exit(1)


def cmd_tables(a):
    """G-base (D12_H0.5 = база h775 по списку и P&L; o566: Dnone_H0.5 = база o566), G-pool (доля сделок клетки, которые есть среди сделок базы), таблицы клеток — все клетки, ни одна не отбрасывается."""
    u = r5.Uni(a.uni)
    gb = "D12_H0.5" if u.name == "h775" else "Dnone_H0.5"
    bad = []
    for side in SIDES:
        base_dirs = {w.name: (u.base(w.name, side), side) for w in st.TRADE_WINDOWS}
        bser, bk, bp = load_cell(u, side, base_dirs)
        bkeys = keys_of(bser)
        bset = set(bkeys)
        out = dict(uni=u.name, side=side, base=cell_stats(bser, bk, bp), pool={}, ladders={}, gbase=None)
        for cid, _ in pool_variants(u, side):
            ser, kinds, pnl = load_cell(u, side, {w.name: (kind_dir(u, "pool", w.name, side, cid), side) for w in st.TRADE_WINDOWS})
            r = cell_stats(ser, kinds, pnl)
            ks = keys_of(ser)
            r["in_base"] = sum(1 for k in ks if k in bset)
            r["in_base_share"] = r["in_base"] / len(ks) if ks else float("nan")
            out["pool"][cid] = r
            if cid == gb:
                same = ks == bkeys and bool(np.allclose(pnl, bp, atol=1e-6))
                out["gbase"] = dict(cell=cid, same=same, n_cell=len(ks), n_base=len(bkeys), only_cell=len(set(ks) - bset), only_base=len(bset - set(ks)))
                if not same:
                    bad.append("%s %s: %s ≠ база (в клетке %d, в базе %d, только в клетке %d, только в базе %d)" % (u.name, side, cid, len(ks), len(bkeys), len(set(ks) - bset), len(bset - set(ks))))
        for cid, _ in ladder_variants(u, side):
            ser, kinds, pnl = load_cell(u, side, {w.name: (kind_dir(u, "ladd", w.name, side, cid), side) for w in st.TRADE_WINDOWS})
            r = cell_stats(ser, kinds, pnl)
            r["in_base"] = sum(1 for k in keys_of(ser) if k in bset)
            out["ladders"][cid] = r
        r4.write_json(q("res", "cells38-%s-%s.json" % (u.name, side)), out)
        g = out["gbase"]
        say("== %s %s: база %d сделок; G-base %s; клеток пула %d, лестниц %d" % (u.name, side, len(bkeys), "ok (%s = база по списку и P&L)" % g["cell"] if g and g["same"] else "ОШИБКА %s" % g, len(out["pool"]), len(out["ladders"])))
        for d in t8.D_LEVELS:
            if d < 25:
                continue
            say("   G-pool D=%-3g | доля сделок клетки, что есть в базе, H = 0,5 … 5: %s" % (d, "  ".join("%g:%.0f%%" % (h, 100 * out["pool"][t8.cell_id(d, h)]["in_base_share"]) for h in t8.H_LEVELS if h >= 0.5)))
    if bad:
        say("G-base СТОП: " + "; ".join(bad))
        sys.exit(1)
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
