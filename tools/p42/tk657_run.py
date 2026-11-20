#!/usr/bin/env python3
"""П-42 §13.57 (TK-657): шорт — рычаги сверх `S_base` §13.30 («больше сделок» A, «лучше сделки» B без выходов) на h775, парная Δ $/сделку.
  python3 tk657_run.py variants   — клетки → res/variants57.json (без движка и без P&L)
  python3 tk657_run.py cells      — движок `breakout tick`: строка базы + 20 клеток, окна wa / wb / wc, шорт (читает P&L: только после PASS)
  python3 tk657_run.py tables     — G-base, G-key, G-dup, G-win; статистика клеток, парная Δ к базе, разложение по сделкам → res/cells57-S.json
  python3 tk657_run.py verdict    — правило п. 7 (а)–(ж) (A1 / B) и §13.57.1 (A2) → res/verdict57.json
Механика ячеек, склейка, статистика, Холм, плато, шум — из `tk643_run` (§13.43) без изменений. Доллары — ставка 500 $; Sharpe — годовой; без бп, без медиан."""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk643_run as t  # noqa: E402

ROOT = os.environ.get("TK657_ROOT", "/data/p42-tk657")
t.ROOT = ROOT                                   # run_variants / q / cell_dir пишут в каталог этой задачи
SIDE = "S"
# п. 5 / п. 7 (е): ось → (семейство, упорядоченные значения, значение базы — умолчание движка `params.rs`)
AXES = {
    "reentry": ("A", (0, 1, 2), 0),
    "t1h": ("A", (0.1, 0.25, 0.5), 0.5),
    "tf": ("A", (30, 60, 240), 60),
    "n_bounce": ("A", (1, 2, 3), 2),
    "l_days": ("A", (3, 7, 14), 7),
    "d": ("A", (0.3, 0.5, 1), 0.5),
    "k": ("A", (1, 2, 3), 2),
    "n_tp": ("B", (4, 8, 12), 8),
    "t_low": ("B", (0.5, 1, 2), 1),
    "a": ("B", (1, 2, 3), 2),
}
# порядок и имена клеток — как `docs/research/freeze/tk618-cells.json` (§13.30)
CELLS = (("A_re1", "reentry", 1), ("A_re2", "reentry", 2), ("A_t1h_0.25", "t1h", 0.25), ("A_t1h_0.1", "t1h", 0.1),
         ("A_tf_30", "tf", 30), ("A_tf_240", "tf", 240), ("A_n_1", "n_bounce", 1), ("A_n_3", "n_bounce", 3),
         ("A_L_14", "l_days", 14), ("A_L_3", "l_days", 3), ("A_d_0.3", "d", 0.3), ("A_d_1", "d", 1),
         ("A_k_1", "k", 1), ("A_k_3", "k", 3),
         ("B_ntp_4", "n_tp", 4), ("B_ntp_12", "n_tp", 12), ("B_tlow_0.5", "t_low", 0.5), ("B_tlow_2", "t_low", 2),
         ("B_a_1", "a", 1), ("B_a_3", "a", 3))
PRIOR_TRIALS = 34                               # п. 7 (ж): 34 клетки §13.30 на 566
MAX_INERT = 2                                   # п. 9 G-key: «не действует» больше 2 клеток — остановка


def cells():
    out = []
    for cid, ax, v in CELLS:
        fam, vals, base = AXES[ax]
        assert v in vals and v != base, cid
        out.append(dict(cid=cid, block=fam, ax={ax: v}, patch={ax: v}))
    return out


def trials():
    return len(cells()) + PRIOR_TRIALS


def ax_index(cs):
    return {(next(iter(c["ax"])), next(iter(c["ax"].values()))): c["cid"] for c in cs}


def neighbours(c, idx):
    """п. 7 (е): {ось: [cid]} — ближайшее значение в каждую сторону; база (Δ ≡ 0) пропускается — берётся следующее."""
    name, v = next(iter(c["ax"].items()))
    _, vals, base = AXES[name]
    vals = list(vals)
    i = vals.index(v)
    res = []
    for seq in (vals[:i][::-1], vals[i + 1:]):
        for x in seq:
            if x == base:
                continue
            cid = idx.get((name, x))
            if cid is not None:
                res.append(cid)
            break
    return {name: res} if res else {}


def verdict(rows):
    fam = cells()
    ids = [c["cid"] for c in fam]
    rej = dict(zip(ids, t.holm([rows[c]["delta"]["p"] for c in ids])))
    dmap = {c: rows[c]["delta"]["d"] for c in ids}
    idx = ax_index(fam)
    nt = trials()
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
        nz = t.noise_bar(r["sharpe"], nt)
        g["g"] = bool(r["sharpe"] > nz["noise_bar"] and nz["survives"])
        res[cid] = dict(gates=g, passed=all(g.values()), neighbours=nb, noise=nz)
    passed = [c for c in ids if res[c]["passed"]]
    return dict(side=SIDE, m=len(ids), trials_noise=nt, passed=passed, cells=res,
                answer=("кандидат вперёд (найдено на этих данных): " + ", ".join(passed)) if passed else "нет: база остаётся")


A2_NONINF = -0.10                               # §13.57.1 п. 2 (в): нижняя граница 5 % Δ $/сделку


def sum_stat(st, ser, bser, ncoins):
    """§13.57.1 п. 2: парная по суткам ΔΣ портфеля (бутстреп как `tk642_run.delta`), одностороннее p по сдвинутым к 0 суммам; месяцы; без 2 суток / 2 монет."""
    Sc, Nc = st.day_sums(ser)
    Sr, Nr = st.day_sums(bser)
    idx = st.block_idx(st.NDAYS, np.random.default_rng(st.seed_of(Sc, Nc, Sr, Nr)))
    D = Sc - Sr
    dsum = float(D.sum())
    bsum = D[idx].sum(axis=1)
    cen = bsum - bsum.mean()
    p = (1 + int((cen >= dsum).sum())) / (1 + len(cen))
    lo, hi = np.percentile(bsum, [2.5, 97.5])
    allday = np.ones(st.NDAYS, dtype=bool)
    (a, _), (e, _) = st._coin_arrays(ser, allday, ncoins), st._coin_arrays(bser, allday, ncoins)
    return dict(dsum=dsum, se_sum=float(bsum.std(ddof=1)), lo95=float(lo), hi95=float(hi), p=float(p),
                months=[float(D[st.MONTH_OF_DAY == k].sum()) for k in range(len(st.MONTH_LEN))],
                robust_days=dsum - float(np.sort(D)[-2:].sum()), robust_coins=dsum - float(np.sort(a - e)[-2:].sum()))


def verdict_a2(rows):
    """§13.57.1 п. 2 (а)–(ж) для 14 клеток A; rows[cid] — строка `stat_row` + `a2` (`sum_stat`)."""
    fam = [c for c in cells() if c["block"] == "A"]
    ids = [c["cid"] for c in fam]
    rej = dict(zip(ids, t.holm([rows[c]["a2"]["p"] for c in ids])))
    dmap = {c: rows[c]["a2"]["dsum"] for c in ids}
    idx = ax_index(fam)
    nt = trials()
    res = {}
    for c in fam:
        cid, r = c["cid"], rows[c["cid"]]
        s2 = r["a2"]
        g = {}
        g["a"] = r["n"] >= t.MIN_N and all(mo[0] >= mn for mo, mn in zip(r["months"], t.MIN_MONTH))
        g["b"] = bool(rej[cid]) and s2["dsum"] > 0
        g["c"] = r["delta"]["lower"] >= A2_NONINF
        g["d"] = sum(1 for x in s2["months"] if x > 0) >= t.NEED_MONTHS
        g["e"] = s2["robust_days"] > 0 and s2["robust_coins"] > 0
        nb = neighbours(c, idx)
        g["f"] = s2["dsum"] > 0 and t.plateau_ok(s2["dsum"], nb, dmap)
        nz = t.noise_bar(r["sharpe"], nt)
        g["g"] = bool(r["sharpe"] > nz["noise_bar"] and nz["survives"])
        res[cid] = dict(gates=g, passed=all(g.values()), neighbours=nb, noise=nz)
    passed = [c for c in ids if res[c]["passed"]]
    return dict(side=SIDE, m=len(ids), trials_noise=nt, passed=passed, cells=res,
                answer=("кандидат A2 вперёд (найдено на этих данных): " + ", ".join(passed)) if passed else "нет: база остаётся")


def decompose(keys, pnl, bkeys, bpnl):
    """п. 6: разложение по ключу (монета, sig_ms): добавлено / убрано / общие (ΔΣ по общим)."""
    bmap = dict(zip(bkeys, bpnl))
    cmap = dict(zip(keys, pnl))
    if len(bmap) != len(bkeys) or len(cmap) != len(keys):
        raise SystemExit("разложение: ключ сделки повторяется")
    add = [k for k in keys if k not in bmap]
    rem = [k for k in bkeys if k not in cmap]
    com = [k for k in keys if k in bmap]
    return dict(added_n=len(add), added_sum=float(sum(cmap[k] for k in add)),
                removed_n=len(rem), removed_sum=float(sum(bmap[k] for k in rem)),
                common_n=len(com), common_dsum=float(sum(cmap[k] - bmap[k] for k in com)),
                common_changed=int(sum(1 for k in com if abs(cmap[k] - bmap[k]) > 1e-6)))


def inert(dec):
    return dec["added_n"] == 0 and dec["removed_n"] == 0 and dec["common_changed"] == 0


# ---- движок и таблицы (srv) -------------------------------------------------------------------------------------------------------
def cmd_variants(a):
    cs = cells()
    json.dump(dict(cells=cs, m=len(cs), trials_noise=trials()), open(t.q("res", "variants57.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    t.say("клеток: %d (A %d, B %d); N шума %d" % (len(cs), sum(1 for c in cs if c["block"] == "A"), sum(1 for c in cs if c["block"] == "B"), trials()))


def cmd_cells(a):
    st, r5, _, _ = t._srv()
    import tk604_run as r4
    if r4.sha256(r4.BIN) not in r5.BIN_SHA_OK:
        raise SystemExit("G-bin: sha256 бинаря не из BIN_SHA_OK")
    u = r5.Uni("h775")
    v = [("base", t.variant_line(r5, u, SIDE, {}))] + [(c["cid"], t.variant_line(r5, u, SIDE, c["patch"])) for c in cells()]
    t.run_variants(u, SIDE, v, "ab")
    t.say("cells готово")


def cmd_tables(a):
    st, r5, p8, r34 = t._srv()
    u = r5.Uni("h775")
    ncoins = len(u.coins())
    bad = []
    bser, bk, bp = t.load_cell(st, p8, u, SIDE, {w.name: (u.base(w.name, SIDE), SIDE) for w in st.TRADE_WINDOWS})
    gser, gk, gp = t.load_cell(st, p8, u, SIDE, t.load_dirs(st, SIDE, "base"))
    gb = p8.keys_of(gser) == p8.keys_of(bser) and len(gp) == len(bp) and bool(np.allclose(gp, bp, atol=1e-6))
    if not gb:
        bad.append("G-base: строка базы в пакете ≠ готовой базе h775")
    bkeys = list(p8.keys_of(bser))
    out = dict(side=SIDE, base=p8.cell_stats(bser, bk, bp), gbase=gb, cells={}, gwin_forced_base=int(sum(1 for k in bk if k == "forced")))
    sig, inert_ids = {}, []
    for c in cells():
        ser, kinds, pnl = t.load_cell(st, p8, u, SIDE, t.load_dirs(st, SIDE, c["cid"]))
        r = t.stat_row(st, p8, r34, ser, kinds, pnl, bser, ncoins)
        keys = list(p8.keys_of(ser))
        dec = decompose(keys, pnl, bkeys, bp)
        r["block"], r["ax"], r["patch"], r["decomp"] = c["block"], c["ax"], c["patch"], dec
        r["inert"] = inert(dec)
        if c["block"] == "A":
            r["a2"] = sum_stat(st, ser, bser, ncoins)
        if r["inert"]:
            inert_ids.append(c["cid"])
        out["cells"][c["cid"]] = r
        sig.setdefault((tuple(keys), tuple(np.round(pnl, 6))), []).append(c["cid"])
    if len(inert_ids) > MAX_INERT:
        bad.append("G-key: «не действует» %d клеток > %d: %s" % (len(inert_ids), MAX_INERT, inert_ids))
    out["inert"] = inert_ids
    out["gdup"] = [v for v in sig.values() if len(v) > 1]
    out["gwin_forced_cells"] = {cid: r["kinds_n"].get("forced", 0) for cid, r in out["cells"].items() if r["kinds_n"].get("forced", 0)}
    json.dump(out, open(t.q("res", "cells57-S.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=float)
    t.say("== S: база %d сделок, Σ %+.2f $; G-base %s; клеток %d; не действует %s; G-dup групп %d" % (
        out["base"]["n"], out["base"]["sum"], "ok" if gb else "ОШИБКА", len(out["cells"]), inert_ids, len(out["gdup"])))
    if bad:
        t.say("СТОП: " + "; ".join(bad))
        sys.exit(1)
    t.say("tables готово")


def cmd_verdict(a):
    tab = json.load(open(t.q("res", "cells57-S.json"), encoding="utf-8"))
    out = {}
    for name, v in (("A1B", verdict(tab["cells"])), ("A2", verdict_a2(tab["cells"]))):
        out[name] = v
        t.say("== S %s: m = %d, N шума = %d; %s" % (name, v["m"], v["trials_noise"], v["answer"]))
        for g in "abcdefg":
            t.say("   ворота (%s) прошли %d из %d" % (g, sum(1 for c in v["cells"].values() if c["gates"][g]), v["m"]))
    json.dump(out, open(t.q("res", "verdict57.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=float)


COMMANDS = {"variants": cmd_variants, "cells": cmd_cells, "tables": cmd_tables, "verdict": cmd_verdict}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=list(COMMANDS))
    COMMANDS[ap.parse_args().cmd](None)


if __name__ == "__main__":
    main()
