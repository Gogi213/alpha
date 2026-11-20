#!/usr/bin/env python3
"""П-42 §13.43 (TK-643): стоп и выходы целиком на h775 — совместные сетки A (стоп), B (выходы после касания), C (тейки / безубыток / трейл), описательный D.
  python3 tk643_run.py variants             — клетки блоков A–C по сторонам → res/variants43.json (без движка и без P&L)
  python3 tk643_run.py cells                — движок `breakout tick`: строка базы (G-base) + блоки A–C, окна wa / wb / wc × стороны (читает P&L: только после PASS)
  python3 tk643_run.py tables               — G-base, G-off, G-dup, G-win; статистика клеток и парная Δ к базе → res/cells43-<сторона>.json
  python3 tk643_run.py verdict              — правило п. 7 (Холм по A–C стороны, месяцы, устойчивость, сумма, плато, шум) → res/verdict43.json
  python3 tk643_run.py cross                — описательный блок D (два лучших по Δ из каждого блока + база), движок и статистика → res/cross43-<сторона>.json
Доллары — ставка 500 $; Sharpe — годовой по суточному ряду Σ $ за 136 суток; без бп, без медиан."""
from __future__ import annotations

import argparse
import itertools
import json
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", ".claude", "skills", "alpha-research", "scripts"))
sys.path.insert(0, HERE)                       # на srv `selection_bias.py` лежит рядом (копия при выкладке)

ROOT = os.environ.get("TK643_ROOT", "/data/p42-tk643")
SIDES = ("L", "S")
MIN_N = 70                                     # п. 7 (а)
MIN_MONTH = (20, 20, 20, 20, 8)                # п. 7 (а): в месяц; 01–13.11 — 8
NEED_MONTHS = 4                                # п. 7 (в): Δ > 0 в ≥ 4 из 5
PRIOR_TRIALS = 80                              # п. 7 (ж): прежние клетки стопа / выходов на 566
T_DAYS = 136
CHUNK = 72                                     # вариантов на вызов движка

# ---- сетки п. 5 ------------------------------------------------------------------------------------------------------------------
S_VALS = (1.5, 2, 3, 4, 5, 6, 8, 12)
FORMS = (("none", {}), ("ls0.5", {"ls": 0.5}), ("ls1", {"ls": 1}), ("c3", {"sn": 3, "sg": 0}), ("c5", {"sn": 5, "sg": 0}),
         ("lin", {"sn": 3, "sg": 1, "sb": 1}), ("geo", {"sn": 3, "sg": 2, "sr": 1.5, "bm": 1}), ("step", {"sn": 3, "sg": 3, "sd": 0.5, "bm": 1}))
RT = [("off", {"rf": 0})] + [("rf%g_rs%g" % (f, t), {"rf": f, "rs": t}) for f in (0.1, 0.2, 0.3) for t in (2, 20)]
TX = [("off", {"tt": 0})] + [("tt%g_tb%g" % (t, b), {"tt": t, "tb": b}) for t, b in ((5, 0.1), (20, 0.1), (5, 0.3), (20, 0.3))]
C_AXES = (("bm", (0, 1, 2, 3)), ("rr", (1, 2, 3)), ("tj", (0, 0.25)), ("t_high", (3, 5, 8)), ("q", (25, 50, 75)))
ORDERED_C = ("rr", "tj", "t_high", "q")        # bm — категориальная
# эффективные значения базы по осям (умолчания движка `params.rs`: s 5, rr 2, t_high 5, q 50, tj 0, bm 0; tt / rf 0 = выкл.)
BASE_AX = {"L": dict(s=5, form="none", rt="rf0.2_rs20", tx="off", bm=0, rr=2, tj=0, t_high=5, q=50),
           "S": dict(s=5, form="none", rt="rf0.2_rs2", tx="tt20_tb0.1", bm=1, rr=2, tj=0, t_high=5, q=50)}


def fmt(v):
    return "%g" % v


def patch_str(p):
    return " ".join("%s=%s" % (k, fmt(v)) for k, v in p.items())


def block_cells(side):
    """{блок: [клетка]}; клетка = dict(cid, block, ax (значения осей блока), patch (ключи движка)). Клетка, равная базе по осям блока, не клетка."""
    b = BASE_AX[side]
    out = {"A": [], "B": [], "C": []}
    for s in S_VALS:
        for fn, fp in FORMS:
            if s == b["s"] and fn == b["form"]:
                continue
            p = {"s": s}
            p.update(fp)
            out["A"].append(dict(cid="A_s%g_%s" % (s, fn), block="A", ax=dict(s=s, form=fn), patch=p))
    for rn, rp in RT:
        for tn, tp in TX:
            if rn == b["rt"] and tn == b["tx"]:
                continue
            p = dict(rp)
            p.update(tp)
            out["B"].append(dict(cid="B_%s_%s" % (rn, tn), block="B", ax=dict(rt=rn, tx=tn), patch=p))
    for vals in itertools.product(*[v for _, v in C_AXES]):
        ax = {k: v for (k, _), v in zip(C_AXES, vals)}
        if all(ax[k] == b[k] for k in ax):
            continue
        out["C"].append(dict(cid="C_" + "_".join("%s%g" % (k, v) for k, v in ax.items()), block="C", ax=ax, patch=dict(ax)))
    return out


# ---- соседи для плато п. 7 (е) ----------------------------------------------------------------------------------------------------
def _rt_parse(n):
    if n == "off":
        return None
    f, t = n.split("_")
    return float(f[2:]), float(t[2:])


def _tx_parse(n):
    if n == "off":
        return None
    t, b = n.split("_")
    return float(t[2:]), float(b[2:])


def _steps(vals, v):
    """Соседние значения упорядоченной оси: [(направление, [значения дальше в этом направлении по порядку])]."""
    vals = list(vals)
    i = vals.index(v)
    return [(-1, vals[:i][::-1]), (+1, vals[i + 1:])]


def neighbours(side, c, by_ax):
    """Соседи клетки по упорядоченным осям её блока: {ось: [cid соседа]}. Сосед — ближайшее значение в каждую сторону при прочих осях клетки;
    если сосед = база стороны (Δ ≡ 0), берётся следующее значение в ту же сторону (текущее значение базы пропускается, как §13.24); нет — направление выпадает."""
    b = BASE_AX[side]
    blk = c["block"]
    out = {}

    def find(ax):
        return by_ax.get((blk, tuple(sorted(ax.items()))))

    def is_base(ax):
        return all(ax[k] == b[k] for k in ax)

    def walk(name, vals, cur, make):
        res = []
        for _, seq in _steps(vals, cur):
            for v in seq:
                ax = make(v)
                if ax is None:
                    break
                if is_base(ax):
                    continue
                cid = find(ax)
                if cid is not None:
                    res.append(cid)
                break
        if res:
            out[name] = res

    ax0 = dict(c["ax"])
    if blk == "A":
        walk("s", S_VALS, ax0["s"], lambda v: dict(ax0, s=v))
    elif blk == "B":
        rt, tx = _rt_parse(ax0["rt"]), _tx_parse(ax0["tx"])
        if rt is not None:
            f, t = rt
            walk("rf", (0.1, 0.2, 0.3), f, lambda v: dict(ax0, rt="rf%g_rs%g" % (v, t)))
            walk("rs", (2, 20), t, lambda v: dict(ax0, rt="rf%g_rs%g" % (f, v)))
        if tx is not None:
            t, x = tx
            walk("tt", (5, 20), t, lambda v: dict(ax0, tx="tt%g_tb%g" % (v, x)))
            walk("tb", (0.1, 0.3), x, lambda v: dict(ax0, tx="tt%g_tb%g" % (t, v)))
    else:
        for k in ORDERED_C:
            vals = dict(C_AXES)[k]
            walk(k, vals, ax0[k], lambda v, k=k: dict(ax0, **{k: v}))
    return out


def effective_on(side, patch):
    """(rt включён, tx включён) у клетки: значение патча, иначе базы стороны."""
    b = BASE_AX[side]
    rf = patch.get("rf", 0 if b["rt"] == "off" else 1)
    tt = patch.get("tt", 0 if b["tx"] == "off" else 1)
    return rf > 0, tt > 0


def ax_index(cells):
    return {(c["block"], tuple(sorted(c["ax"].items()))): c["cid"] for blk in cells.values() for c in blk}


def plateau_ok(d_cell, nb, dmap):
    """п. 7 (е): у каждой упорядоченной оси с соседями — все соседи с Δ ≥ 0,5·Δ клетки; нужна хотя бы одна ось с соседом."""
    if not nb:
        return False
    for ids in nb.values():
        for cid in ids:
            dn = dmap.get(cid)
            if dn is None or not (dn >= 0.5 * d_cell):
                return False
    return True


# ---- правило п. 7 ----------------------------------------------------------------------------------------------------------------
def holm(ps, alpha=0.05):
    m = len(ps)
    rej = [False] * m
    for k, i in enumerate(sorted(range(m), key=lambda j: ps[j]), start=1):
        if ps[i] <= alpha / (m - k + 1):
            rej[i] = True
        else:
            break
    return rej


def noise_bar(sharpe, trials):
    import selection_bias as sb
    se = math.sqrt(365.0 / T_DAYS * (1 + sharpe * sharpe / 730.0))
    return sb.significance(sharpe, se, trials)


def verdict_side(side, rows, cells):
    """rows: {cid: строка статистики (n, months, delta{d, p, dsum, se_sum, s_c}, dmonths, robust_days, robust_coins, sharpe)}; клетки A–C стороны."""
    ids = [c["cid"] for blk in ("A", "B", "C") for c in cells[blk]]
    m = len(ids)
    rej = dict(zip(ids, holm([rows[c]["delta"]["p"] for c in ids])))
    dmap = {c: rows[c]["delta"]["d"] for c in ids}
    idx = ax_index(cells)
    allc = {c["cid"]: c for blk in cells.values() for c in blk}
    trials = m + PRIOR_TRIALS
    res = {}
    for cid in ids:
        r = rows[cid]
        dl = r["delta"]
        g = {}
        g["a"] = r["n"] >= MIN_N and all(mo[0] >= mn for mo, mn in zip(r["months"], MIN_MONTH))
        g["b"] = bool(rej[cid])
        g["c"] = sum(1 for x in r["dmonths"] if x is not None and x > 0) >= NEED_MONTHS
        g["d"] = r["robust_days"] is not None and r["robust_coins"] is not None and r["robust_days"] > 0 and r["robust_coins"] > 0
        g["e"] = dl["s_c"] > 0 and dl["dsum"] >= -1.0 * dl["se_sum"]
        nb = neighbours(side, allc[cid], idx)
        g["f"] = dl["d"] > 0 and plateau_ok(dl["d"], nb, dmap)
        nz = noise_bar(r["sharpe"], trials)
        g["g"] = bool(r["sharpe"] > nz["noise_bar"] and nz["survives"])
        res[cid] = dict(gates=g, passed=all(g.values()), neighbours=nb, noise=nz)
    passed = [c for c in ids if res[c]["passed"]]
    return dict(side=side, m=m, trials_noise=trials, passed=passed, answer=("кандидат вперёд (найдено на этих данных): " + ", ".join(passed)) if passed else "нет: база стороны остаётся", cells=res)


# ---- блок D (описательный) -------------------------------------------------------------------------------------------------------
def top2(cells_blk, rows):
    ok = [c for c in cells_blk if rows[c["cid"]]["n"] >= MIN_N and all(mo[0] >= mn for mo, mn in zip(rows[c["cid"]]["months"], MIN_MONTH))]
    ok.sort(key=lambda c: -rows[c["cid"]]["delta"]["d"])
    return ok[:2]


def cross_cells(cells, rows):
    """Сочетания {база, 1-я, 2-я} по блокам A × B × C без базы и без сочетаний с одним небазовым блоком (это клетки блоков); патчи A → B → C."""
    opts = [[None] + top2(cells[b], rows) for b in ("A", "B", "C")]
    out, seen = [], set()
    for combo in itertools.product(*opts):
        if sum(1 for x in combo if x is not None) < 2:
            continue
        p = {}
        for x in combo:
            if x is not None:
                p.update(x["patch"])
        key = tuple(sorted(p.items()))
        if key in seen:
            continue
        seen.add(key)
        out.append(dict(cid="D_" + "+".join(x["cid"] for x in combo if x is not None), block="D", parts=[x["cid"] for x in combo if x is not None], patch=p))
    return out


# ---- движок и статистика (srv) ----------------------------------------------------------------------------------------------------
def _srv():
    import tk604_stats as st
    import tk625_run as r5
    import tk638_post as p8
    import tk634_run as r34
    return st, r5, p8, r34


def q(*a):
    d = os.path.join(ROOT, *a)
    os.makedirs(os.path.dirname(d), exist_ok=True)
    return d


def say(*a):
    print(*a, flush=True)


def cell_dir(w, side, cid):
    return os.path.join(ROOT, "cells", "%s-%s" % (w, side), cid)


def variant_line(r5, u, side, patch):
    return "%s%s%s" % (r5.BASE[side], u.extra, (" " + patch_str(patch)) if patch else "")


def run_variants(u, side, variants, tag):
    st, r5, _, _ = _srv()
    for w in st.TRADE_WINDOWS:
        for k in range(0, len(variants), CHUNK):
            part = variants[k:k + CHUNK]
            out = os.path.join(ROOT, "cells", "%s-%s" % (w.name, side))
            done = os.path.join(ROOT, "done", "%s-%s-%s-%d" % (tag, w.name, side, k))
            if os.path.exists(done):
                continue
            r5.tick(u, w, part, out, {}, q("log", "%s-%s-%s-%d.log" % (tag, w.name, side, k)))
            os.makedirs(os.path.dirname(done), exist_ok=True)
            open(done, "w").write("%d\n" % len(part))
            say("%s %s-%s [%d:%d] готово" % (tag, w.name, side, k, k + len(part)))


def cmd_variants(a):
    res = {}
    for side in SIDES:
        c = block_cells(side)
        res[side] = c
        say("%s: A %d, B %d, C %d, всего m = %d" % (side, len(c["A"]), len(c["B"]), len(c["C"]), sum(len(v) for v in c.values())))
    json.dump(res, open(q("res", "variants43.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def cmd_cells(a):
    st, r5, _, _ = _srv()
    import tk604_run as r4
    if r4.sha256(r4.BIN) not in r5.BIN_SHA_OK:
        raise SystemExit("G-bin: sha256 бинаря не из BIN_SHA_OK")
    u = r5.Uni("h775")
    for side in SIDES:
        c = block_cells(side)
        v = [("base", variant_line(r5, u, side, {}))] + [(x["cid"], variant_line(r5, u, side, x["patch"])) for b in ("A", "B", "C") for x in c[b]]
        run_variants(u, side, v, "abc")
    say("cells готово")


def stat_row(st, p8, r34, ser, kinds, pnl, bser, ncoins):
    r = p8.cell_stats(ser, kinds, pnl)
    Sc, Nc = st.day_sums(ser)
    Sr, Nr = st.day_sums(bser)
    d = st.delta_stat(Sc, Nc, Sr, Nr)
    om = np.arange(st.NDAYS)
    mt = st.month_table(Sc, Nc, Sr, Nr, om)
    ok, dd, dc = st.robust(ser, bser, om, ncoins)
    r["delta"] = dict(d=d.d, se=d.se, z=d.z, lower=d.lower, p=d.p, dsum=d.dsum, se_sum=d.se_sum, s_c=d.s_c, s_r=d.s_r, n_c=d.n_c, n_r=d.n_r)
    r["dmonths"] = [None if math.isnan(mt[k][0]) else mt[k][0] for k in sorted(mt)]
    r["robust_days"], r["robust_coins"] = dd, dc
    kc = {}
    for k in kinds:
        kc[k] = kc.get(k, 0) + 1
    r["kinds_n"] = kc
    return r


def load_dirs(st, side, cid):
    return {w.name: (cell_dir(w.name, side, cid), side) for w in st.TRADE_WINDOWS}


def cmd_tables(a):
    st, r5, p8, r34 = _srv()
    u = r5.Uni("h775")
    ncoins = len(u.coins())
    bad = []
    for side in SIDES:
        c = block_cells(side)
        bser, bk, bp = p8.load_cell(u, side, {w.name: (u.base(w.name, side), side) for w in st.TRADE_WINDOWS})
        gser, gk, gp = p8.load_cell(u, side, load_dirs(st, side, "base"))
        gb = p8.keys_of(gser) == p8.keys_of(bser) and len(gp) == len(bp) and bool(np.allclose(gp, bp, atol=1e-6))
        if not gb:
            bad.append("G-base %s: строка базы в пакете ≠ готовой базе h775" % side)
        out = dict(side=side, base=p8.cell_stats(bser, bk, bp), gbase=gb, cells={}, gwin_forced_base=int(sum(1 for k in bk if k == "forced")))
        sig = {}
        for blk in ("A", "B", "C"):
            for x in c[blk]:
                ser, kinds, pnl = p8.load_cell(u, side, load_dirs(st, side, x["cid"]))
                r = stat_row(st, p8, r34, ser, kinds, pnl, bser, ncoins)
                r["block"], r["ax"], r["patch"] = blk, x["ax"], x["patch"]
                out["cells"][x["cid"]] = r
                sig.setdefault((tuple(p8.keys_of(ser)), tuple(np.round(pnl, 6))), []).append(x["cid"])
        # G-off: rf=0 → нет `rt`; tt=0 → нет `tx`; при включённых — хотя бы в одной клетке есть
        has_rt = has_tx = False
        for cid, r in out["cells"].items():
            rf_on, tt_on = effective_on(side, r["patch"])
            if not rf_on and r["kinds_n"].get("rt", 0):
                bad.append("G-off %s %s: rf=0, но выходов rt %d" % (side, cid, r["kinds_n"]["rt"]))
            if not tt_on and r["kinds_n"].get("tx", 0):
                bad.append("G-off %s %s: tt=0, но выходов tx %d" % (side, cid, r["kinds_n"]["tx"]))
            has_rt |= rf_on and r["kinds_n"].get("rt", 0) > 0
            has_tx |= tt_on and r["kinds_n"].get("tx", 0) > 0
        if not (has_rt and has_tx):
            bad.append("G-off %s: при включённых rt / tx нет ни одного такого выхода (rt %s, tx %s)" % (side, has_rt, has_tx))
        out["gdup"] = [v for v in sig.values() if len(v) > 1]
        out["gwin_forced_cells"] = {cid: r["kinds_n"].get("forced", 0) for cid, r in out["cells"].items() if r["kinds_n"].get("forced", 0)}
        json.dump(out, open(q("res", "cells43-%s.json" % side), "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=float)
        say("== %s: база %d сделок, Σ %+.2f $; G-base %s; клеток %d; G-dup групп %d; forced у базы %d" % (
            side, out["base"]["n"], out["base"]["sum"], "ok" if gb else "ОШИБКА", len(out["cells"]), len(out["gdup"]), out["gwin_forced_base"]))
    if bad:
        say("СТОП: " + "; ".join(bad))
        sys.exit(1)
    say("tables готово")


def cmd_verdict(a):
    res = {}
    for side in SIDES:
        tab = json.load(open(q("res", "cells43-%s.json" % side), encoding="utf-8"))
        v = verdict_side(side, tab["cells"], block_cells(side))
        res[side] = v
        say("== %s: m = %d, N шума = %d; %s" % (side, v["m"], v["trials_noise"], v["answer"]))
        for g in "abcdefg":
            say("   ворота (%s) прошли %d из %d" % (g, sum(1 for c in v["cells"].values() if c["gates"][g]), v["m"]))
    json.dump(res, open(q("res", "verdict43.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=float)


def cmd_cross(a):
    st, r5, p8, r34 = _srv()
    u = r5.Uni("h775")
    ncoins = len(u.coins())
    for side in SIDES:
        tab = json.load(open(q("res", "cells43-%s.json" % side), encoding="utf-8"))
        cells = block_cells(side)
        d = cross_cells(cells, tab["cells"])
        run_variants(u, side, [(x["cid"], variant_line(r5, u, side, x["patch"])) for x in d], "d")
        bser, _, _ = p8.load_cell(u, side, {w.name: (u.base(w.name, side), side) for w in st.TRADE_WINDOWS})
        out = dict(side=side, note="описательно: выбрано по P&L тех же 136 суток, 0 испытаний, вне Холма (§13.43 п. 5)", cells={})
        for x in d:
            ser, kinds, pnl = p8.load_cell(u, side, load_dirs(st, side, x["cid"]))
            r = stat_row(st, p8, r34, ser, kinds, pnl, bser, ncoins)
            r["parts"], r["patch"] = x["parts"], x["patch"]
            out["cells"][x["cid"]] = r
        json.dump(out, open(q("res", "cross43-%s.json" % side), "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=float)
        say("== %s: клеток D %d" % (side, len(d)))


COMMANDS = {"variants": cmd_variants, "cells": cmd_cells, "tables": cmd_tables, "verdict": cmd_verdict, "cross": cmd_cross}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=list(COMMANDS))
    a = ap.parse_args()
    COMMANDS[a.cmd](a)


if __name__ == "__main__":
    main()
