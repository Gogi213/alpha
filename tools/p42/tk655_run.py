#!/usr/bin/env python3
"""П-42 §13.55 (TK-655): вход на h775 — зона входа `e` (блок E), второй вход `e2` (блок D), вход по подтверждению E(k) (блок K, лонг, повтор по ленте).
  python3 tk655_run.py variants   — клетки → res/variants55.json (без движка и без P&L)
  python3 tk655_run.py cells      — движок `breakout tick`: строка базы + E + D, окна wa / wb / wc × стороны (читает P&L: только после PASS)
  python3 tk655_run.py replay     — блок K: повтор сделок `L_base` h775 по ленте, k ∈ {0} ∪ KS (`tk617_entry_replay.sim_long` без изменений) → res/replay55.json
  python3 tk655_run.py tables     — G-base, G-key, G-k0, G-tape, G-dup, G-win; статистика клеток и парная Δ к эталону → res/cells55-<сторона>.json
  python3 tk655_run.py verdict    — правило п. 7 (а)–(ж) → res/verdict55.json
Механика ячеек, склейка, статистика, Холм, шум — из `tk643_run` (§13.43) / `tk650_run` (§13.50) без изменений. Доллары — ставка 500 $; Sharpe — годовой; без бп, без медиан."""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk643_run as t  # noqa: E402

ROOT = os.environ.get("TK655_ROOT", "/data/p42-tk655")
t.ROOT = ROOT                                   # run_variants / q / cell_dir пишут в каталог этой задачи
SIDES = t.SIDES
E_VALS = (0.05, 0.1, 0.15, 0.2, 0.3, 0.5)       # п. 5 блок E
E2_VALS = (0, 0.025, 0.05, 0.1)                 # п. 5 блок D
K_VALS = (0, 5, 10, 15, 20, 25)                 # п. 5 блок K; 0 — эталон блока, не клетка
BASE = {"L": dict(e=0.3, e2=0, k=0), "S": dict(e=0.15, e2=0.05, k=0)}
PRIOR_TRIALS = 20                               # п. 7 (ж): лонг 19, шорт 18 — вверх до 20
K0_TOL, K0_SHARE, K0_SUM = 0.01, 0.99, 1.0      # п. 9 G-k0
CODES = {0: "stop", 1: "be", 2: "trail", 3: "rt", 4: "forced"}     # `tk617_entry_replay.CODES`
EXPIRY = 3_600_000                              # приказ E(k) — 60 мин (`tk617_entry_replay.EXPIRY`)
PROCS = 2


def cells(side):
    """[клетка]; клетка = dict(cid, block, ax, patch). База не клетка."""
    b = BASE[side]
    out = []
    for v in E_VALS:
        if v != b["e"]:
            out.append(dict(cid="E_e%g" % v, block="E", ax=dict(e=v), patch={"e": v}))
    for v in E2_VALS:
        if v != b["e2"]:
            out.append(dict(cid="D_e2_%g" % v, block="D", ax=dict(e2=v), patch={"e2": v}))
    if side == "L":
        for k in K_VALS:
            if k != b["k"]:
                out.append(dict(cid="K_k%d" % k, block="K", ax=dict(k=k), patch={}))
    return out


AXES = {"E": ("e", E_VALS), "D": ("e2", E2_VALS), "K": ("k", K_VALS)}


def neighbours(side, c, idx):
    """п. 7 (е): {ось: [cid]} — ближайшее значение в каждую сторону; база стороны (Δ ≡ 0) пропускается — берётся следующее."""
    name, vals = AXES[c["block"]]
    vals = list(vals)
    i = vals.index(c["ax"][name])
    res = []
    for seq in (vals[:i][::-1], vals[i + 1:]):
        for v in seq:
            if v == BASE[side][name]:
                continue
            cid = idx.get((c["block"], v))
            if cid is not None:
                res.append(cid)
            break
    return {name: res} if res else {}


def ax_index(cs):
    return {(c["block"], c["ax"][AXES[c["block"]][0]]): c["cid"] for c in cs}


def trials(side):
    return len(cells(side)) + PRIOR_TRIALS


def verdict_side(side, rows):
    fam = cells(side)
    ids = [c["cid"] for c in fam]
    rej = dict(zip(ids, t.holm([rows[c]["delta"]["p"] for c in ids])))
    dmap = {c: rows[c]["delta"]["d"] for c in ids}
    idx = ax_index(fam)
    nt = trials(side)
    res = {}
    for c in fam:
        cid, r = c["cid"], rows[c["cid"]]
        dl = r["delta"]
        g = {}
        g["a"] = r["n"] >= t.MIN_N and all(mo[0] >= mn for mo, mn in zip(r["months"], t.MIN_MONTH))
        g["b"] = bool(rej[cid])
        g["c"] = sum(1 for x in r["dmonths"] if x is not None and x > 0) >= t.NEED_MONTHS
        g["d"] = r["robust_days"] is not None and r["robust_coins"] is not None and r["robust_days"] > 0 and r["robust_coins"] > 0
        if c["block"] == "K":      # §13.55.1: у K сделки только выпадают — охрана суммы строже, ΔΣ > 0
            g["e"] = dl["s_c"] > 0 and dl["dsum"] > 0
        else:
            g["e"] = dl["s_c"] > 0 and dl["dsum"] >= -1.0 * dl["se_sum"]
        nb = neighbours(side, c, idx)
        g["f"] = dl["d"] > 0 and t.plateau_ok(dl["d"], nb, dmap)
        nz = t.noise_bar(r["sharpe"], nt)
        g["g"] = bool(r["sharpe"] > nz["noise_bar"] and nz["survives"])
        res[cid] = dict(gates=g, passed=all(g.values()), neighbours=nb, noise=nz)
    passed = [c for c in ids if res[c]["passed"]]
    pe = [c for c in passed if not c.startswith("K_")]
    pk = [c for c in passed if c.startswith("K_")]
    ans = []
    if pe:
        ans.append("кандидат вперёд (найдено на этих данных): " + ", ".join(pe))
    if pk:
        ans.append("кандидат для ключа движка E(k) (повтор, портфель не моделировался): " + ", ".join(pk))
    return dict(side=side, m=len(ids), trials_noise=nt, passed=passed, cells=res,
                answer="; ".join(ans) if ans else "нет: база стороны остаётся")


# ---- блок K: повтор по ленте ------------------------------------------------------------------------------------------------------
def k0_gate(eng_val, rep_val):
    """G-k0: |Δ| ≤ 0,01 $ у ≥ 99 % сделок и |Σ Δ| ≤ 1 $. rep_val — NaN, если у k = 0 сделки нет. → (ok, n_bad, sum_diff, индексы несовпавших)."""
    e, r = np.asarray(eng_val, dtype=float), np.asarray(rep_val, dtype=float)
    diff = np.where(np.isnan(r), np.inf, np.abs(r - e))
    bad = np.nonzero(diff > K0_TOL)[0]
    sd = float(np.where(np.isnan(r), 0.0, r).sum() - e.sum())          # нет сделки у повтора — её P&L 0
    ok = len(e) > 0 and (len(e) - len(bad)) >= K0_SHARE * len(e) and abs(sd) <= K0_SUM
    return ok, int(len(bad)), sd, [int(i) for i in bad]


def k_series(st, bser, codes, vals):
    """Ряд клетки K: сделки базы с исполненным входом (код ≥ 0), P&L — повтора; сутки, монета, ключ — как у сделки базы (п. 4)."""
    codes = np.asarray(codes)
    m = codes >= 0
    idx = np.nonzero(m)[0]
    ser = st.make_series(bser.day[idx], bser.sym[idx], np.asarray(vals, dtype=float)[idx], bser.key_ms[idx],
                         [bser.sym_name[i] for i in idx], None if bser.t_out is None else bser.t_out[idx],
                         None if bser.t_in is None else bser.t_in[idx], None if bser.fund is None else bser.fund[idx])
    return ser, idx


def zachet_rows(st, u, side):
    """Сделки базы стороны по окнам в порядке `st.splice` (зачётный срез по дате входа) + цена уровня и NATR из `sigsx` того же прогона."""
    import csv
    eng = {"L": "long", "S": "short"}[side]
    out = []
    for w in st.TRADE_WINDOWS:
        d = u.base(w.name, side)
        tb = st.read_trades_csv(os.path.join(d, eng + "-trades.csv"))
        lv = [int(r["level"]) for r in csv.DictReader(open(os.path.join(d, eng + "-trades.csv"), encoding="utf-8", newline=""))]
        sx = {}
        for r in csv.DictReader(open(os.path.join(d, eng + "-sigsx.csv"), encoding="utf-8", newline="")):
            key = (r["sym"], int(r["level"]), int(r["sig_ms"]))
            if key in sx:
                raise SystemExit("sigsx %s %s: ключ %r повторяется" % (w.name, side, key))
            sx[key] = (int(r["natr_e8"]), int(r["level_px_e8"]))
        day = st.abs_day(w, tb.entry_ms)
        lo_i, hi_i = (w.lod - st.D0).days, (w.hid - st.D0).days
        for i in np.nonzero((day >= lo_i) & (day <= hi_i))[0]:
            key = (tb.sym[i], lv[i], int(tb.sig_ms[i]))
            if key not in sx:
                raise SystemExit("sigsx %s %s: нет кандидата сделки %r" % (w.name, side, key))
            natr_e8, lp_e8 = sx[key]
            out.append(dict(win=w.name, sym=tb.sym[i], level=lv[i], sig_ms=int(tb.sig_ms[i]), t_sig=int(tb.sig_ms[i]) + w.t0_ms,
                            t_out=int(tb.exit_ms[i]) + w.t0_ms, natr=natr_e8 / 1e8, lp=abs(lp_e8) / 1e8, kind=tb.kind[i],
                            val=float(tb.gross[i] - tb.fees[i])))
    return out


def tape_days(t_sig, t_out, w_hi, last_ms):
    """Сутки ленты сделки: от суток (сигнал − 1 мин) до суток выхода + 2, не позже конца окна и 13.11 (G-tape)."""
    f = lambda ms: datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone.utc).date()     # noqa: E731
    d0, d1 = f(t_sig - 60_000), f(t_out) + datetime.timedelta(days=2)
    d1 = min(d1, w_hi, f(last_ms - 1))
    return [d0 + datetime.timedelta(days=k) for k in range((d1 - d0).days + 1)]


def _load_day(tree, win, sym, day):
    import zipfile

    import pandas as pd
    pth = "%s/%s/aggtrades/%s/%s-aggTrades-%s.zip" % (tree, win, sym, sym, day.isoformat())
    if not os.path.exists(pth):
        return None
    with zipfile.ZipFile(pth) as z:
        with z.open(z.namelist()[0]) as f:
            df = pd.read_csv(f, usecols=["price", "transact_time", "is_buyer_maker"], dtype={"price": "float64", "transact_time": "int64"})
    return df["transact_time"].values, df["price"].values, (df["is_buyer_maker"].astype(str).str.lower() == "true").values


def _replay_sym(job):
    import tk604_stats as st
    import tk617_entry_replay as rp
    tree, sym, evs = job
    his = {w.name: w.hid for w in st.TRADE_WINDOWS}
    out, cache = [], {}
    for e in evs:
        ts, ps, bs = [], [], []
        for d in tape_days(e["t_sig"], e["t_out"], his[e["win"]], st.LAST_MS):
            key = (e["win"], d)
            if key not in cache:
                cache[key] = _load_day(tree, e["win"], sym, d)
            x = cache[key]
            if x is not None:
                ts.append(x[0])
                ps.append(x[1])
                bs.append(x[2])
        if not ts:
            out.append((e["i"], None, 0))
            continue
        tt = np.concatenate(ts).astype(np.int64)
        tmax = int(tt.max())
        if tmax >= st.LAST_MS:
            raise SystemExit("G-tape: %s сделка ленты %d ≥ 14.11" % (sym, tmax))
        p = np.concatenate(ps).astype(np.float64)
        bm = np.concatenate(bs).astype(np.bool_)
        res = {str(k): [float(v) for v in rp.sim_long(tt, p, bm, e["t_sig"] + 4, e["natr"], e["lp"], k, EXPIRY)] for k in K_VALS}
        out.append((e["i"], res, tmax))
    return out


def cmd_replay(a):
    st, r5, _, _ = t._srv()
    from multiprocessing import Pool
    u = r5.Uni("h775")
    rows = zachet_rows(st, u, "L")
    by = {}
    for i, r in enumerate(rows):
        by.setdefault(r["sym"], []).append(dict(r, i=i))
    jobs = [(u.tree, s, sorted(v, key=lambda e: e["t_sig"])) for s, v in sorted(by.items(), key=lambda kv: -len(kv[1]))]
    res, tmax = {}, 0
    with Pool(PROCS) as pool:
        for n, lst in enumerate(pool.imap_unordered(_replay_sym, jobs, chunksize=1)):
            for i, r, tm in lst:
                res[i] = r
                tmax = max(tmax, tm)
            if n % 50 == 0:
                t.say("replay %d / %d" % (n, len(jobs)))
    for i, r in enumerate(rows):
        r["rep"] = res.get(i)
    json.dump(dict(rows=rows, tape_max_ms=tmax), open(t.q("res", "replay55.json"), "w", encoding="utf-8"), ensure_ascii=False)
    t.say("replay готово: сделок %d, без ленты %d, последняя сделка ленты %s UTC" % (
        len(rows), sum(1 for r in rows if r["rep"] is None), datetime.datetime.fromtimestamp(tmax / 1000, datetime.timezone.utc).isoformat()))


def rep_arrays(rows, k):
    """(коды, P&L gross − fees, виды) повтора для k; нет ленты — код −9."""
    codes, vals, kinds = [], [], []
    for r in rows:
        x = None if r["rep"] is None else r["rep"][str(k)]                  # ключи k после json — строки
        if x is None:
            codes.append(-9)
            vals.append(np.nan)
            kinds.append("none")
            continue
        code = int(x[0])
        codes.append(code)
        vals.append(x[3] - x[4] if code >= 0 else np.nan)
        kinds.append(CODES.get(code, "none"))
    return np.array(codes), np.array(vals, dtype=float), kinds


# ---- движок и таблицы (srv) -------------------------------------------------------------------------------------------------------
def cmd_variants(a):
    res = {s: cells(s) for s in SIDES}
    json.dump(dict(cells=res, m={s: len(res[s]) for s in SIDES}, trials_noise={s: trials(s) for s in SIDES}),
              open(t.q("res", "variants55.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    t.say("клеток: L %d (E %d, D %d, K %d), S %d (E %d, D %d); N шума L %d, S %d" % (
        len(res["L"]), *[sum(1 for c in res["L"] if c["block"] == b) for b in "EDK"], len(res["S"]),
        *[sum(1 for c in res["S"] if c["block"] == b) for b in "ED"], trials("L"), trials("S")))


def cmd_cells(a):
    st, r5, _, _ = t._srv()
    import tk604_run as r4
    if r4.sha256(r4.BIN) not in r5.BIN_SHA_OK:
        raise SystemExit("G-bin: sha256 бинаря не из BIN_SHA_OK")
    u = r5.Uni("h775")
    for side in SIDES:
        v = [("base", t.variant_line(r5, u, side, {}))] + [(c["cid"], t.variant_line(r5, u, side, c["patch"])) for c in cells(side) if c["block"] != "K"]
        t.run_variants(u, side, v, "ed")
    t.say("cells готово")


def _extra(r, c, extra):
    r["block"], r["ax"], r["patch"] = c["block"], c["ax"], c["patch"]
    r.update(extra)
    return r


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
        for c in cells(side):
            if c["block"] == "K":
                continue
            ser, kinds, pnl = t.load_cell(st, p8, u, side, t.load_dirs(st, side, c["cid"]))
            r = t.stat_row(st, p8, r34, ser, kinds, pnl, bser, ncoins)
            keys = list(p8.keys_of(ser))
            ks = set(keys)
            common = [(k, x) for k, x in zip(keys, pnl) if k in bmap]
            same = dict(only_cell=len(ks - set(bkeys)), only_base=len(set(bkeys) - ks), common=len(common))
            changed = int(sum(1 for k, x in common if abs(x - bmap[k]) > 1e-6))
            if same["only_cell"] + same["only_base"] + changed == 0:
                bad.append("G-key %s %s: сделки и P&L равны базе" % (side, c["cid"]))
            out["cells"][c["cid"]] = _extra(r, c, dict(same=same, pnl_changed=changed))
            sig.setdefault((tuple(keys), tuple(np.round(pnl, 6))), []).append(c["cid"])
        if side == "L":
            rep = json.load(open(t.q("res", "replay55.json"), encoding="utf-8"))
            rows = rep["rows"]
            if [(r["sym"], r["t_sig"]) for r in rows] != bkeys:
                bad.append("K: порядок / ключи сделок повтора ≠ ряду базы")
            if rep["tape_max_ms"] >= st.LAST_MS:
                bad.append("G-tape: лента позже 13.11")
            c0, v0, k0 = rep_arrays(rows, 0)
            ok, nbad, sd, bad_i = k0_gate(bp, v0)
            kind_mis = int(sum(1 for i in range(len(bk)) if c0[i] >= 0 and k0[i] != bk[i]))
            out["gk0"] = dict(ok=ok, n=len(bp), n_bad=nbad, sum_diff=sd, kind_mismatch=kind_mis,
                              bad=[dict(sym=rows[i]["sym"], sig_ms=rows[i]["sig_ms"], eng=float(bp[i]), rep=None if np.isnan(v0[i]) else float(v0[i])) for i in bad_i[:50]])
            t.say("G-k0: %d из %d совпали (|Δ| ≤ %.2f $), Σ Δ %+.4f $, вид выхода не совпал у %d — %s" % (
                len(bp) - nbad, len(bp), K0_TOL, sd, kind_mis, "ok" if ok else "ОШИБКА"))
            if not ok:
                bad.append("G-k0: повтор k = 0 ≠ движку")
            else:
                rser, ridx = k_series(st, bser, c0, v0)
                rkinds = [k0[i] for i in ridx]
                out["k_ref"] = p8.cell_stats(rser, rkinds, rser.val)
                for c in cells(side):
                    if c["block"] != "K":
                        continue
                    ck, cv, ckd = rep_arrays(rows, c["ax"]["k"])
                    ser, idx = k_series(st, bser, ck, cv)
                    kinds = [ckd[i] for i in idx]
                    r = t.stat_row(st, p8, r34, ser, kinds, ser.val, rser, ncoins)
                    miss = np.nonzero(ck < 0)[0]
                    extra = dict(not_filled=int(sum(1 for x in ck if x == -3)), beyond=int(sum(1 for x in ck if x == -2)),
                                 no_tape=int(sum(1 for x in ck if x in (-1, -9))), skipped_base_sum=float(np.asarray(bp)[miss].sum()),
                                 skipped_base_n=int(len(miss)))
                    out["cells"][c["cid"]] = _extra(r, c, extra)
        out["gdup"] = [v for v in sig.values() if len(v) > 1]
        out["gwin_forced"] = {cid: r["kinds_n"].get("forced", 0) for cid, r in out["cells"].items() if r["kinds_n"].get("forced", 0)}
        out["gwin_forced_base"] = int(sum(1 for k in bk if k == "forced"))
        json.dump(out, open(t.q("res", "cells55-%s.json" % side), "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=float)
        t.say("== %s: база %d сделок; G-base %s; клеток %d; G-dup групп %d" % (side, out["base"]["n"], "ok" if gb else "ОШИБКА", len(out["cells"]), len(out["gdup"])))
    if bad:
        t.say("СТОП: " + "; ".join(bad))
        sys.exit(1)
    t.say("tables готово")


def cmd_verdict(a):
    res = {}
    for side in SIDES:
        tab = json.load(open(t.q("res", "cells55-%s.json" % side), encoding="utf-8"))
        v = verdict_side(side, tab["cells"])
        res[side] = v
        t.say("== %s: m = %d, N шума = %d; %s" % (side, v["m"], v["trials_noise"], v["answer"]))
    json.dump(res, open(t.q("res", "verdict55.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=float)


COMMANDS = {"variants": cmd_variants, "cells": cmd_cells, "replay": cmd_replay, "tables": cmd_tables, "verdict": cmd_verdict}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=list(COMMANDS))
    COMMANDS[ap.parse_args().cmd](None)


if __name__ == "__main__":
    main()
