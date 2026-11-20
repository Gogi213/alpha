#!/usr/bin/env python3
"""П-42 §13.50 (TK-650): сетка тейков γ × κ (блок G) и период NATR выхода (блок N) на h775 до предела формы лестницы; `ns=1` — справочно.
  python3 tk650_run.py variants   — клетки → res/variants50.json (без движка и без P&L)
  python3 tk650_run.py cells      — движок `breakout tick`: строка базы + G + N + справочная `ns=1`, окна wa / wb / wc × стороны (читает P&L: только после PASS)
  python3 tk650_run.py tables     — G-base, G-same, G-tp, G-dup, G-win; статистика клеток и парная Δ к базе → res/cells50-<сторона>.json
  python3 tk650_run.py verdict    — правило п. 7 (а)–(з) + поворот поверхности → res/verdict50.json
Механика ячеек, склейка, статистика, Холм, шум — из `tk643_run` (§13.43) без изменений. Доллары — ставка 500 $; Sharpe — годовой; без бп, без медиан."""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk643_run as t  # noqa: E402

ROOT = os.environ.get("TK650_ROOT", "/data/p42-tk650")
t.ROOT = ROOT                                   # run_variants / q / cell_dir пишут в каталог этой задачи
SIDES = t.SIDES
GAMMAS = (0.05, 0.125, 0.25, 0.5, 1, 2, 4, 8)
KAPPAS = (-0.9, -0.75, 0, 1, 3, 6, 10, 30, 100)
NATR_P = (15, 30, 60)
BASE = dict(tpg=1, tpk=0, natr_exit_p=30)
PRIOR_TRIALS = 270                              # п. 7 (ж): §13.12 ≤ 52 + §13.43 блок C 215 + natr_exit_p 14.11 2, вверх
MIN_LOT_USD = 5.0                               # п. 7 (з): минимальная стоимость ордера Bybit
NOTIONAL_U, N_TP, Q_PCT = 500_000_000, 8, 50    # `trade.rs` NOTIONAL_U; база n_tp 8, q 50


def tp_lots(kappa):
    """Лоты ступеней, мкУЕ — копия `tick.rs` `tp_lots_n` (целая арифметика, остаток — последней)."""
    km = round(kappa * 1000)
    if km == 0:
        return [NOTIONAL_U * Q_PCT // (100 * N_TP)] * N_TP
    total = NOTIONAL_U * Q_PCT // 100
    w = [1000 * (N_TP - 1) + km * i for i in range(N_TP)]
    sw = sum(w)
    lots = [total * x // sw for x in w]
    lots[-1] += total - sum(lots)
    return lots


def min_lot_usd(kappa):
    return min(tp_lots(kappa)) / 1e6


def cells():
    """[клетка]; клетка = dict(cid, block, ax, patch, m (в семействе)). База не клетка."""
    out = []
    for g in GAMMAS:
        for k in KAPPAS:
            if g == 1 and k == 0:
                continue
            p = {}
            if g != 1:
                p["tpg"] = g
            if k != 0:
                p["tpk"] = k
            out.append(dict(cid="G_g%g_k%g" % (g, k), block="G", ax=dict(tpg=g, tpk=k), patch=p, m=True))
    for n in NATR_P:
        if n != 30:
            out.append(dict(cid="N_p%d" % n, block="N", ax=dict(natr_exit_p=n), patch={"natr_exit_p": n}, m=True))
    out.append(dict(cid="R_ns1", block="R", ax={}, patch={"ns": 1}, m=False))
    return out


def family():
    return [c for c in cells() if c["m"]]


def ax_index(cs):
    return {(c["block"], tuple(sorted(c["ax"].items()))): c["cid"] for c in cs}


def neighbours(c, idx):
    """п. 7 (е): {ось: [cid]} — ближайшее значение в каждую сторону при прочих ключах; база (Δ ≡ 0) пропускается — берётся следующее."""
    if c["block"] == "G":
        axes = (("tpg", GAMMAS), ("tpk", KAPPAS))
    elif c["block"] == "N":
        axes = (("natr_exit_p", NATR_P),)
    else:
        return {}
    out = {}
    for name, vals in axes:
        vals = list(vals)
        i = vals.index(c["ax"][name])
        res = []
        for seq in (vals[:i][::-1], vals[i + 1:]):
            for v in seq:
                ax = dict(c["ax"], **{name: v})
                if all(ax[k] == BASE[k] for k in ax):
                    continue
                cid = idx.get((c["block"], tuple(sorted(ax.items()))))
                if cid is not None:
                    res.append(cid)
                break
        if res:
            out[name] = res
    return out


def exec_ok(c):
    return c["block"] != "G" or min_lot_usd(c["ax"]["tpk"]) >= MIN_LOT_USD


def verdict_side(side, rows):
    fam = family()
    ids = [c["cid"] for c in fam]
    rej = dict(zip(ids, t.holm([rows[c]["delta"]["p"] for c in ids])))
    dmap = {c: rows[c]["delta"]["d"] for c in ids}
    idx = ax_index(fam)
    trials = len(ids) + PRIOR_TRIALS
    res = {}
    for c in fam:
        cid, r = c["cid"], rows[c["cid"]]
        dl = r["delta"]
        g = {}
        g["a"] = r["n"] >= t.MIN_N and all(mo[0] >= mn for mo, mn in zip(r["months"], t.MIN_MONTH))
        g["b"] = bool(rej[cid])
        g["c"] = sum(1 for x in r["dmonths"] if x is not None and x > 0) >= t.NEED_MONTHS
        g["d"] = r["robust_days"] is not None and r["robust_coins"] is not None and r["robust_days"] > 0 and r["robust_coins"] > 0
        g["e"] = dl["s_c"] > 0 and dl["dsum"] >= -1.0 * dl["se_sum"]
        nb = neighbours(c, idx)
        g["f"] = dl["d"] > 0 and t.plateau_ok(dl["d"], nb, dmap)
        nz = t.noise_bar(r["sharpe"], trials)
        g["g"] = bool(r["sharpe"] > nz["noise_bar"] and nz["survives"])
        g["h"] = exec_ok(c)
        res[cid] = dict(gates=g, passed=all(g.values()), neighbours=nb, noise=nz)
    passed = [c for c in ids if res[c]["passed"]]
    return dict(side=side, m=len(ids), trials_noise=trials, passed=passed, cells=res, turn=turn(rows),
                answer=("кандидат вперёд (найдено на этих данных): " + ", ".join(passed)) if passed else "нет: база стороны остаётся")


def turn(rows):
    """Поворот поверхности G (описательно): сглаженная Δ = среднее Δ клетки и соседей по 4 сторонам (база — 0), только клетки с (а)."""
    def ok(r):
        return r["n"] >= t.MIN_N and all(mo[0] >= mn for mo, mn in zip(r["months"], t.MIN_MONTH))

    def d_at(gi, ki):
        g, k = GAMMAS[gi], KAPPAS[ki]
        if g == 1 and k == 0:
            return 0.0
        r = rows["G_g%g_k%g" % (g, k)]
        return r["delta"]["d"] if ok(r) else None

    sm = {}
    for gi in range(len(GAMMAS)):
        for ki in range(len(KAPPAS)):
            if d_at(gi, ki) is None or (GAMMAS[gi] == 1 and KAPPAS[ki] == 0):
                continue
            vals = [d_at(gi, ki)]
            for a, b in ((gi - 1, ki), (gi + 1, ki), (gi, ki - 1), (gi, ki + 1)):
                if 0 <= a < len(GAMMAS) and 0 <= b < len(KAPPAS) and d_at(a, b) is not None:
                    vals.append(d_at(a, b))
            sm[(gi, ki)] = float(np.mean(vals))
    if not sm:
        return dict(where="нет клеток с минимумом сделок")
    gi, ki = max(sm, key=lambda x: sm[x])
    g, k = GAMMAS[gi], KAPPAS[ki]
    edges = []
    if gi == 0 or ki == len(KAPPAS) - 1:
        edges.append("дальний предел (γ = %g / κ = %g)" % (GAMMAS[0], KAPPAS[-1]))
    if gi == len(GAMMAS) - 1 or ki == 0:
        edges.append("ближний предел (γ = %g / κ = %g)" % (GAMMAS[-1], KAPPAS[0]))
    where = "на краю: " + " и ".join(edges) if edges else "внутри сетки"
    prof_g = {"%g" % gg: _prof([rows["G_g%g_k%g" % (gg, kk)]["delta"]["d"] for kk in KAPPAS if not (gg == 1 and kk == 0)]) for gg in GAMMAS}
    prof_k = {"%g" % kk: _prof([rows["G_g%g_k%g" % (gg, kk)]["delta"]["d"] for gg in GAMMAS if not (gg == 1 and kk == 0)]) for kk in KAPPAS}
    old = [rows["G_g%g_k%g" % (gg, kk)]["delta"]["d"] for gg in GAMMAS if gg <= 0.5 for kk in KAPPAS if kk >= 3]
    return dict(argmax=dict(tpg=g, tpk=k, smooth=sm[(gi, ki)], d=rows["G_g%g_k%g" % (g, k)]["delta"]["d"]), where=where,
                smooth={"G_g%g_k%g" % (GAMMAS[a], KAPPAS[b]): v for (a, b), v in sm.items()}, prof_tpg=prof_g, prof_tpk=prof_k,
                old_edge=dict(cell=rows["G_g0.5_k3"]["delta"]["d"], region_mean=float(np.mean(old)), region_n=len(old), region_pos=sum(1 for x in old if x > 0)))


def _prof(ds):
    return dict(mean=float(np.mean(ds)), n=len(ds), pos=sum(1 for x in ds if x > 0))


# ---- движок и таблицы (srv) -------------------------------------------------------------------------------------------------------
def cmd_variants(a):
    cs = cells()
    json.dump(dict(cells=cs, m=len(family()), min_lot_usd={"%g" % k: min_lot_usd(k) for k in KAPPAS}),
              open(t.q("res", "variants50.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    t.say("клеток %d, в семействе m = %d; наименьшая ступень, $: %s" % (len(cs), len(family()), {k: round(min_lot_usd(k), 2) for k in KAPPAS}))


def cmd_cells(a):
    st, r5, _, _ = t._srv()
    import tk604_run as r4
    if r4.sha256(r4.BIN) not in r5.BIN_SHA_OK:
        raise SystemExit("G-bin: sha256 бинаря не из BIN_SHA_OK")
    u = r5.Uni("h775")
    for side in SIDES:
        v = [("base", t.variant_line(r5, u, side, {}))] + [(c["cid"], t.variant_line(r5, u, side, c["patch"])) for c in cells()]
        t.run_variants(u, side, v, "gn")
    t.say("cells готово")


def cmd_tables(a):
    st, r5, p8, r34 = t._srv()
    u = r5.Uni("h775")
    ncoins = len(u.coins())
    bad = []
    for side in SIDES:
        bser, bk, bp = t.load_cell(st, p8, u, side, {w.name: (u.base(w.name, side), side) for w in st.TRADE_WINDOWS})
        gser, gk, gp = t.load_cell(st, p8, u, side, t.load_dirs(st, side, "base"))
        gb = p8.keys_of(gser) == p8.keys_of(bser) and len(gp) == len(bp) and bool(np.allclose(gp, bp, atol=1e-6))
        if not gb:
            bad.append("G-base %s: строка базы в пакете ≠ готовой базе h775" % side)
        bkeys = list(p8.keys_of(bser))
        bmap = dict(zip(bkeys, bp))
        out = dict(side=side, base=p8.cell_stats(bser, bk, bp), gbase=gb, cells={})
        sig = {}
        for c in cells():
            ser, kinds, pnl = t.load_cell(st, p8, u, side, t.load_dirs(st, side, c["cid"]))
            r = t.stat_row(st, p8, r34, ser, kinds, pnl, bser, ncoins)
            keys = list(p8.keys_of(ser))
            ks = set(keys)
            common = [(k, x) for k, x in zip(keys, pnl) if k in bmap]
            r["same"] = dict(only_cell=len(ks - set(bkeys)), only_base=len(set(bkeys) - ks), common=len(common))
            r["tp_changed"] = int(sum(1 for k, x in common if abs(x - bmap[k]) > 1e-6))
            r["block"], r["ax"], r["patch"] = c["block"], c["ax"], c["patch"]
            if c["block"] == "G":
                r["min_lot_usd"] = min_lot_usd(c["ax"]["tpk"])
            if c["block"] in ("G", "N") and r["tp_changed"] == 0:
                bad.append("G-tp %s %s: P&L общих сделок равен базе" % (side, c["cid"]))
            out["cells"][c["cid"]] = r
            sig.setdefault((tuple(keys), tuple(np.round(pnl, 6))), []).append(c["cid"])
        out["gdup"] = [v for v in sig.values() if len(v) > 1]
        out["gwin_forced"] = {cid: r["kinds_n"].get("forced", 0) for cid, r in out["cells"].items() if r["kinds_n"].get("forced", 0)}
        out["gwin_forced_base"] = int(sum(1 for k in bk if k == "forced"))
        json.dump(out, open(t.q("res", "cells50-%s.json" % side), "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=float)
        t.say("== %s: база %d сделок; G-base %s; клеток %d; G-dup групп %d; G-same: наибольшее отличных сделок %d" % (
            side, out["base"]["n"], "ok" if gb else "ОШИБКА", len(out["cells"]), len(out["gdup"]),
            max(r["same"]["only_cell"] + r["same"]["only_base"] for r in out["cells"].values())))
    if bad:
        t.say("СТОП: " + "; ".join(bad))
        sys.exit(1)
    t.say("tables готово")


def cmd_verdict(a):
    res = {}
    for side in SIDES:
        tab = json.load(open(t.q("res", "cells50-%s.json" % side), encoding="utf-8"))
        v = verdict_side(side, tab["cells"])
        res[side] = v
        t.say("== %s: m = %d, N шума = %d; %s; поворот: %s" % (side, v["m"], v["trials_noise"], v["answer"], v["turn"]["where"]))
    json.dump(res, open(t.q("res", "verdict50.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=float)


COMMANDS = {"variants": cmd_variants, "cells": cmd_cells, "tables": cmd_tables, "verdict": cmd_verdict}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=list(COMMANDS))
    COMMANDS[ap.parse_args().cmd](None)


if __name__ == "__main__":
    main()
