#!/usr/bin/env python3
"""П-42 §13.48 (TK-648): глубина книги (`bookDepth`) как признак на h775, окно ω = 14.09–13.11 без 08.10 (60 сут.).
  python3 tk648_run.py counts   — БЕЗ P&L: пороги (квантили по кандидатам ω), клетки, исключённые кандидаты и сделки базы, Jaccard соседей, покрытие, таблицы `--ftab`
  python3 tk648_run.py cells    — движок `breakout tick` с `--ftab` на окнах wb / wc (wa ω не задевает — там клетка = база)          [P&L: только при PASS-файле]
  python3 tk648_run.py g8       — G8: no_key = 0, dropped = пересчёту; G-pre: сделки клетки с сигналом до 14.09 = базе
  python3 tk648_run.py select   — ΔΣ к базе на ω (пара по суткам, бутстреп блоками 4), Холм m = 6 на сторону, MDE, шумовая планка, правило ответа → res/select48.json
P&L читают только cells / g8 / select и только при файле /data/p42-tk648/PASS (SHA протокола после PASS критика)."""
from __future__ import annotations

import argparse
import json
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import lib  # noqa: E402
import retitr_ftab as rfx  # noqa: E402
import tk604_feat as tf  # noqa: E402
import tk604_run as r4  # noqa: E402
import tk604_stats as st  # noqa: E402
import tk620_rr as rr  # noqa: E402
import tk625_run as r5  # noqa: E402
import tk634_run as r34  # noqa: E402
import tk638_post as p8  # noqa: E402
import tk638_run as t8  # noqa: E402
import tk648_feat as ft  # noqa: E402

ROOT = ft.ROOT
SIDES = ft.SIDES
ENG, BASE = r5.ENG, r5.BASE
FV_PASS = 0.5
SHARES = (0.10, 0.25, 0.40)                       # доля исключаемых кандидатов: пробная 0,25, соседи 0,10 / 0,40
TRIAL_SHARE = 0.25
# признак → (короткое имя, хвост, исключаемый по механизму): lo — исключить нижний хвост (торгуем при значении ≥ порога), hi — верхний
MECH = {"bd_imb02": ("IMB02", "lo"), "bd_imb1": ("IMB1", "lo"), "bd_imb2": ("IMB2", "lo"),
        "bd_thin5": ("THIN5", "hi"), "bd_thin30": ("THIN30", "hi"), "bd_depth_turn": ("DTURN", "hi")}
MIN_EXCL = 15                                     # допуск: исключено ≥ 15 сделок базы в ω
MIN_MONTH = 8                                     # допуск: ≥ 8 сделок клетки и базы в каждом месяце ω (сен 14–30, окт без 08.10, 01–13.11)
MIN_TRADES = 70                                   # пометка «мало сделок»
ALPHA, M_HOLM = 0.05, 6
NB = st.NBOOT
GuardError = st.GuardError
say = ft.say
U = r5.Uni("h775")


def q(*a):
    return ft.q(*a)


# ---- ω ---------------------------------------------------------------------------------------------------------------------------

def omega_idx():
    return np.array([(d - st.D0).days for d in ft.omega_days()], dtype=np.int64)


OM = omega_idx()
OM_MONTH = st.MONTH_OF_DAY[OM]                    # 2 = сен, 3 = окт, 4 = ноя
MONTHS = (2, 3, 4)
MONTH_RU = {2: "сен 14–30", 3: "окт без 08.10", 4: "01–13.11"}


def guard_pass():
    p = os.path.join(ROOT, "PASS")
    if not os.path.exists(p):
        raise GuardError("P&L не читается: нет %s (PASS критика по протоколу §13.48)" % p)
    return open(p, encoding="utf-8").read().strip()


# ---- таблицы ---------------------------------------------------------------------------------------------------------------------

def load_window(w, side):
    sym, ms, _ = ft.table(w.name, side)
    fs, fm, raw = rfx.read_ftab(os.path.join(ROOT, "feat", "%s-%s.csv" % (w.name, side)))
    if list(fs) != list(sym) or not np.array_equal(np.asarray(fm, dtype=np.int64), ms):
        raise GuardError("%s-%s: ключи признаков ≠ таблица кандидатов TK-634" % (w.name, side))
    days = np.array([(ft.utc_day(s) - st.D0).days for s in ft.abs_s(w, ms)], dtype=np.int64)
    t = dict(sym=list(sym), ms=ms, day=days, om=np.isin(days, OM), scored=t8.in_scored_range(w, ms))
    for k in ft.FEATS + ft.EXTRA:
        t[k] = np.asarray(raw[k], dtype=float)
    return t


def cell_defs(thr):
    cells = []
    for f, (short, mech) in MECH.items():
        for tail in ("lo", "hi"):
            for sh in SHARES:
                cid = "%s_%s%02d" % (short, tail, round(sh * 100))
                cells.append(dict(cid=cid, feat=f, tail=tail, share=sh, thr=thr[f]["%s%02d" % (tail, round(sh * 100))],
                                  trial=bool(tail == mech and sh == TRIAL_SHARE), mech=bool(tail == mech)))
    return cells


def excluded(c, v):
    v = np.asarray(v, dtype=float)
    ok = np.isfinite(v)
    with np.errstate(invalid="ignore"):
        bad = (v < c["thr"]) if c["tail"] == "lo" else (v > c["thr"])
    return ok & bad


def ftab_path(w, side):
    return q("ftab48", "%s-%s.csv" % (w.name, side))


def variants(side):
    thr = json.load(open(q("res", "thr48.json"), encoding="utf-8"))[side]
    return [(c["cid"], "%s%s fa=%s fo=lo fv=%s" % (BASE[side], U.extra, c["cid"], FV_PASS)) for c in cell_defs(thr)]


def trade_values(side, tabs):
    """Значения признаков на сделках базы в ω (окна wb / wc, сутки входа в ω)."""
    W, S, R = r34.trade_rows(U, side)
    out = {k: [] for k in ft.FEATS + ft.EXTRA}
    keys = []
    for w in ft.WINDOWS:
        t = tabs[w.name]
        idx = {(s_, int(r_)): i for i, (s_, r_) in enumerate(zip(t["sym"], t["ms"]))}
        base = st.read_trades_csv(os.path.join(U.base(w.name, side), ENG[side] + "-trades.csv"))
        d = st.abs_day(w, base.entry_ms)
        lo_i, hi_i = (w.lod - st.D0).days, (w.hid - st.D0).days
        for i in np.nonzero((d >= lo_i) & (d <= hi_i) & np.isin(d, OM))[0]:
            j = idx.get((base.sym[i], int(base.sig_ms[i])))
            if j is None:
                raise GuardError("%s-%s: сделка базы без строки таблицы кандидатов" % (w.name, side))
            keys.append((w.name, base.sym[i], int(base.sig_ms[i]), int(d[i])))
            for k in out:
                out[k].append(t[k][j])
    return keys, {k: np.asarray(v, dtype=float) for k, v in out.items()}


# ---- counts (без P&L) --------------------------------------------------------------------------------------------------------------

def cmd_counts(a):
    cls = ft.classes()
    thr_all, res_all = {}, {}
    for side in SIDES:
        tabs = {w.name: load_window(w, side) for w in ft.WINDOWS}
        ref = {f: np.concatenate([tabs[w.name][f][tabs[w.name]["om"] & tabs[w.name]["scored"]] for w in ft.WINDOWS]) for f in ft.FEATS}
        n_ref = {f: int(len(ref[f])) for f in ft.FEATS}
        thr = {}
        for f in ft.FEATS:
            v = ref[f][np.isfinite(ref[f])]
            thr[f] = {}
            for sh in SHARES:
                thr[f]["lo%02d" % round(sh * 100)] = float(np.quantile(v, sh))
                thr[f]["hi%02d" % round(sh * 100)] = float(np.quantile(v, 1 - sh))
            thr[f]["n_defined"] = int(len(v))
        thr_all[side] = thr
        cells = cell_defs(thr)
        keys, tv = trade_values(side, tabs)
        n_t = len(keys)
        mon = st.MONTH_OF_DAY[np.array([k[3] for k in keys], dtype=np.int64)]
        res = dict(side=side, n_trades_omega=n_t, n_cand_omega=n_ref, trades_by_month={MONTH_RU[m]: int((mon == m).sum()) for m in MONTHS}, coverage={}, cells=[])
        # покрытие: признак определён на кандидатах ω и на сделках базы ω — по месяцам, по классу, по режиму метки
        for f in ft.FEATS + ("bd_age_s",):
            cov = dict(cand=float(np.isfinite(ref.get(f, np.concatenate([tabs[w.name][f][tabs[w.name]["om"] & tabs[w.name]["scored"]] for w in ft.WINDOWS]))).mean()),
                       trades=float(np.isfinite(tv[f]).mean()) if n_t else float("nan"),
                       trades_by_month={MONTH_RU[m]: float(np.isfinite(tv[f][mon == m]).mean()) for m in MONTHS})
            cls_t = np.array([cls.get(k[1], "?") for k in keys])
            cov["trades_by_class"] = {c: [int((cls_t == c).sum()), float(np.isfinite(tv[f][cls_t == c]).mean()) if (cls_t == c).any() else float("nan")] for c in ("crypto", "tradfi")}
            res["coverage"][f] = cov
        md = tv["bd_mode"]
        res["mode_trades"] = dict(A=int((md == 1).sum()), B=int((md == 0).sum()), none=int((~np.isfinite(md)).sum()))
        age = tv["bd_age_s"]
        res["age_s"] = {mm: [float(x) for x in np.nanquantile(age[md == v], [0.05, 0.5, 0.95])] if (md == v).any() else None for mm, v in (("A", 1), ("B", 0))}
        res["feat_by_mode"] = {f: {mm: [float(x) for x in np.nanquantile(tv[f][md == v], [0.25, 0.5, 0.75])] if np.isfinite(tv[f][md == v]).any() else None for mm, v in (("A", 1), ("B", 0))} for f in ft.FEATS}
        say("== %s: сделок базы в ω %d (по месяцам %s); режим метки A / B / нет снимка: %s" % (side, n_t, res["trades_by_month"], res["mode_trades"]))
        for f in ft.FEATS:
            c = res["coverage"][f]
            say("   %-13s определён: кандидаты ω %.1f %%, сделки ω %.1f %% (%s), класс crypto/tradfi %s" % (
                f, 100 * c["cand"], 100 * c["trades"], ", ".join("%s %.0f %%" % (k, 100 * v) for k, v in c["trades_by_month"].items()),
                {k: "%d сд. %.0f %%" % (v[0], 100 * v[1]) for k, v in c["trades_by_class"].items()}))
        masks = {}
        for c in cells:
            ex_t = excluded(c, tv[c["feat"]])
            masks[c["cid"]] = ex_t
            ex_c = sum(int((excluded(c, tabs[w.name][c["feat"]]) & tabs[w.name]["om"] & tabs[w.name]["scored"]).sum()) for w in ft.WINDOWS)
            n_c = sum(int((tabs[w.name]["om"] & tabs[w.name]["scored"]).sum()) for w in ft.WINDOWS)
            by_m = {MONTH_RU[m]: int((~ex_t & (mon == m)).sum()) for m in MONTHS}
            adm = bool(int(ex_t.sum()) >= MIN_EXCL and all(v >= MIN_MONTH for v in by_m.values()) and all(int((mon == m).sum()) >= MIN_MONTH for m in MONTHS))
            res["cells"].append(dict(cid=c["cid"], feat=c["feat"], tail=c["tail"], share=c["share"], thr=c["thr"], trial=c["trial"], mech=c["mech"],
                                     cand_excluded=ex_c, cand_rows=n_c, trades_excluded=int(ex_t.sum()), trades_remain=int((~ex_t).sum()), remain_by_month=by_m, admitted=adm))
        for c in res["cells"]:                     # Jaccard множеств исключённых сделок с соседями (та же сторона хвоста, соседняя доля)
            nb = [x for x in res["cells"] if x["feat"] == c["feat"] and x["tail"] == c["tail"] and x["cid"] != c["cid"]]
            c["jaccard_nb"] = {x["cid"]: jac(masks[c["cid"]], masks[x["cid"]]) for x in nb}
        for c in res["cells"]:
            say("   %-10s %s порог %+.5g: исключено кандидатов %d из %d, сделок базы %d из %d, остаётся %d %s%s%s" % (
                c["cid"], "ПРОБНАЯ" if c["trial"] else ("сосед  " if c["mech"] else "обратн."), c["thr"], c["cand_excluded"], c["cand_rows"], c["trades_excluded"], n_t,
                c["trades_remain"], c["remain_by_month"], "" if c["admitted"] else " [не допущена]", "  J " + ", ".join("%s %.2f" % kv for kv in c["jaccard_nb"].items()) if c["trial"] else ""))
        for w in ft.WINDOWS:
            t = tabs[w.name]
            cols = {c["cid"]: np.where(excluded(c, t[c["feat"]]) & t["om"], 0.0, 1.0) for c in cells}
            path = ftab_path(w, side)
            with open(path, "w", encoding="utf-8", newline="\n") as fo:
                fo.write("sym,sig_ms," + ",".join(cols) + "\n")
                for i in range(len(t["sym"])):
                    fo.write("%s,%d,%s\n" % (t["sym"][i], int(t["ms"][i]), ",".join("%.1f" % cols[k][i] for k in cols)))
            res.setdefault("ftab", {})["%s-%s" % (w.name, side)] = dict(rows=len(t["sym"]), sha256=st.sha256_file(path))
        res_all[side] = res
        json.dump(res, open(q("res", "counts48-%s.json" % side), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(thr_all, open(q("res", "thr48.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    say("counts готово (P&L не читался) — запись в протокол §13.48 до PASS")


def jac(a, b):
    u = int((a | b).sum())
    return float((a & b).sum() / u) if u else float("nan")


# ---- движок (после PASS) ---------------------------------------------------------------------------------------------------------------

def cell_dir(wname, side, cid):
    return os.path.join(ROOT, "cells", "%s-%s" % (wname, side), cid)


def cmd_cells(a):
    guard_pass()
    for w in ft.WINDOWS:
        for side in SIDES:
            out = os.path.join(ROOT, "cells", "%s-%s" % (w.name, side))
            if os.path.exists(out + "/DONE"):
                continue
            v = variants(side)
            r5.tick(U, w, v, out, {}, q("log", "cells-%s-%s.log" % (w.name, side)), ftab=ftab_path(w, side))
            with open(out + "/DONE", "w", encoding="utf-8") as f:
                f.write("%d %s\n" % (len(v), r4.sha256(ftab_path(w, side))))
            say("cells %s-%s: %d вариантов" % (w.name, side, len(v)))


def cmd_g8(a):
    guard_pass()
    bad, res = [], {}
    for w in ft.WINDOWS:
        for side in SIDES:
            ftab = ftab_path(w, side)
            dsym, drel = tf.read_sigsx_keys(os.path.join(U.base(w.name, side), ENG[side] + "-sigsx.csv"))
            dkeys = [(s_, int(r_)) for s_, r_ in zip(dsym, drel)]
            base = st.read_trades_csv(os.path.join(U.base(w.name, side), ENG[side] + "-trades.csv"))
            pre_cut = int((ft.dt.datetime(2026, 9, 14, tzinfo=ft.dt.timezone.utc).timestamp() * 1000) - w.t0_ms)
            bpre = sorted((base.sym[i], int(base.sig_ms[i]), round(float(base.gross[i] - base.fees[i]), 6)) for i in range(len(base.sym)) if base.sig_ms[i] < pre_cut)
            for cid, _ in variants(side):
                tab = r34.read_table_col(ftab, cid)
                miss = sum(1 for k in dkeys if k not in tab)
                exp_u = sum(1 for v in tab.values() if v == v and v < FV_PASS)
                exp_m = sum(1 for k in dkeys if k in tab and tab[k] < FV_PASS)
                m = json.load(open(os.path.join(cell_dir(w.name, side, cid), "meta.json"), encoding="utf-8"))[ENG[side]]
                fc = r4.filter_counters(m)
                ct = st.read_trades_csv(os.path.join(cell_dir(w.name, side, cid), ENG[side] + "-trades.csv"))
                cpre = sorted((ct.sym[i], int(ct.sig_ms[i]), round(float(ct.gross[i] - ct.fees[i]), 6)) for i in range(len(ct.sym)) if ct.sig_ms[i] < pre_cut)
                res["%s/%s/%s" % (w.name, side, cid)] = dict(counters=fc, expected_unique=exp_u, expected_rows=exp_m, dump_without_row=miss, pre_equal=cpre == bpre, pre_n=len(bpre))
                if miss or fc is None or fc["no_key"] != 0 or fc["dropped"] not in (exp_u, exp_m):
                    bad.append("%s/%s/%s G8: %s, ожидалось %d / %d, без строки %d" % (w.name, side, cid, fc, exp_u, exp_m, miss))
                if cpre != bpre:
                    bad.append("%s/%s/%s G-pre: сделки до 14.09 ≠ базе (%d против %d)" % (w.name, side, cid, len(cpre), len(bpre)))
    r4.write_json(q("gates", "g8.json"), dict(res=res, bad=bad))
    say("G8 / G-pre", "FAIL " + "; ".join(bad[:8]) if bad else "ok во всех %d прогонах" % len(res))
    return 1 if bad else 0


# ---- статистика на ω ------------------------------------------------------------------------------------------------------------------

def zq(p):
    lo, hi = -10.0, 10.0
    for _ in range(200):
        m = (lo + hi) / 2
        if 0.5 * (1 + math.erf(m / math.sqrt(2))) < p:
            lo = m
        else:
            hi = m
    return (lo + hi) / 2


Z_HOLM, Z_1, Z_POW = zq(1 - ALPHA / (2 * M_HOLM)), zq(1 - ALPHA / 2), zq(0.80)


def day_arrays(ser):
    S = np.bincount(ser.day, weights=ser.val, minlength=st.NDAYS)[OM]
    N = np.bincount(ser.day, minlength=st.NDAYS).astype(float)[OM]
    return S, N


def in_om(ser):
    return np.isin(ser.day, OM)


def sharpe_year(Sd):
    s = float(np.std(Sd, ddof=1))
    return float(np.mean(Sd) / s * math.sqrt(365)) if s > 0 else float("nan")


def summary(ser, kinds, pnl):
    m = in_om(ser)
    S, N = day_arrays(ser)
    cum = np.concatenate([[0.0], np.cumsum(S)])
    mon = st.MONTH_OF_DAY[ser.day[m]]
    v = ser.val[m]
    kk = [k for k, x in zip(kinds, m) if x]
    return dict(n=int(m.sum()), sum=float(v.sum()), mean=float(v.mean()) if m.any() else float("nan"), sharpe=sharpe_year(S),
                months={MONTH_RU[k]: (int((mon == k).sum()), float(v[mon == k].sum())) for k in MONTHS},
                sharpe_months={MONTH_RU[k]: sharpe_year(S[OM_MONTH == k]) for k in MONTHS},
                maxdd=float((np.maximum.accumulate(cum) - cum).max()), kpi=lib.drawdown_kpi(S),
                rr=rr.rr_stats([dict(pnl=p, kind=k) for p, k in zip(pnl[m], kk)]), few=bool(int(m.sum()) < MIN_TRADES))


def delta(cs, bs):
    Sc, Nc = day_arrays(cs)
    Sr, Nr = day_arrays(bs)
    idx = st.block_idx(len(OM), np.random.default_rng(st.seed_of(Sc, Nc, Sr, Nr)), NB)
    D = Sc - Sr
    dsum = float(D.sum())
    bsum = D[idx].sum(axis=1)
    se = float(bsum.std(ddof=1))
    cen = bsum - bsum.mean()
    p2 = (1 + int((np.abs(cen) >= abs(dsum)).sum())) / (1 + len(cen))
    lo, hi = np.percentile(bsum, [2.5, 97.5])
    with np.errstate(divide="ignore", invalid="ignore"):
        bpt = Sc[idx].sum(axis=1) / Nc[idx].sum(axis=1) - Sr[idx].sum(axis=1) / Nr[idx].sum(axis=1)
    dpt = float(Sc.sum() / Nc.sum() - Sr.sum() / Nr.sum()) if Nc.sum() > 0 else float("nan")
    return dict(dsum=dsum, se_sum=se, lo95=float(lo), hi95=float(hi), p2=float(p2), z=dsum / se if se > 0 else float("nan"),
                dmean=dpt, se_mean=float(np.nanstd(bpt, ddof=1)), mde_sum_holm=(Z_HOLM + Z_POW) * se, mde_sum_1=(Z_1 + Z_POW) * se,
                mde_mean_holm=(Z_HOLM + Z_POW) * float(np.nanstd(bpt, ddof=1)),
                months={MONTH_RU[k]: float(D[OM_MONTH == k].sum()) for k in MONTHS},
                dsharpe=sharpe_year(Sc) - sharpe_year(Sr))


def robust(cs, bs, dsum):
    """Знак ΔΣ на ω без двух суток с наибольшим вкладом того же знака и (отдельно) без монеты с наибольшим вкладом того же знака."""
    Sc, _ = day_arrays(cs)
    Sr, _ = day_arrays(bs)
    Dd = Sc - Sr
    sg = 1.0 if dsum >= 0 else -1.0
    top = np.sort(sg * Dd)[::-1][:2]
    no_days = dsum - sg * float(top[top > 0].sum())
    coins = {}
    for ser, sgn in ((cs, 1.0), (bs, -1.0)):
        m = in_om(ser)
        for s_, v in zip(np.asarray(ser.sym_name, dtype=object)[m], ser.val[m]):
            coins[s_] = coins.get(s_, 0.0) + sgn * float(v)
    tc = max(coins.items(), key=lambda kv: sg * kv[1]) if coins else ("", 0.0)
    no_coin = dsum - (tc[1] if sg * tc[1] > 0 else 0.0)
    return dict(no_top2_days=no_days, top_coin=tc[0], top_coin_delta=tc[1], no_top_coin=no_coin, holds=bool(sg * no_days > 0 and sg * no_coin > 0))


def holm(ps, alpha=ALPHA):
    order = sorted(range(len(ps)), key=lambda i: ps[i])
    rej, m = [False] * len(ps), len(ps)
    for r_, i in enumerate(order):
        if ps[i] <= alpha / (m - r_):
            rej[i] = True
        else:
            break
    return rej


def emax_abs_z(n, reps=200_000, seed=648):
    z = np.abs(np.random.default_rng(seed).standard_normal((reps, n)))
    return float(z.max(axis=1).mean()), float(np.percentile(z.max(axis=1), 95))


def verdict(d, rej, rb, nb_ok, adm):
    """Правило п. 7 (до счёта) для пробной клетки."""
    if not adm:
        return "не допущена"
    months_pos = sum(1 for v in d["months"].values() if v > 0)
    months_neg = sum(1 for v in d["months"].values() if v < 0)
    if rej and d["dsum"] > 0 and months_pos >= 2 and rb["holds"] and nb_ok:
        return "работает: фильтр лучше базы (найдено на этих данных; кандидат вперёд)"
    if rej and d["dsum"] < 0 and months_neg >= 2 and rb["holds"]:
        return "вредит: фильтр хуже базы (измерено)"
    if rej:
        return "значимо, но не устойчиво (месяцы / сутки / монета / соседи)"
    return "не различимо: |ΔΣ| < MDE %.0f $ за 60 сут. не исключено" % d["mde_sum_holm"]


def load(side, cid=None):
    dirs = {"wa": (U.base("wa", side), side)}
    for w in ft.WINDOWS:
        dirs[w.name] = (U.base(w.name, side), side) if cid is None else (cell_dir(w.name, side, cid), side)
    return p8.load_cell(U, side, dirs)


def cmd_select(a):
    tag = guard_pass()
    cnt = {s: json.load(open(q("res", "counts48-%s.json" % s), encoding="utf-8")) for s in SIDES}
    out = dict(pass_tag=tag, z_holm=Z_HOLM, z_pow=Z_POW, m_holm=M_HOLM, omega_days=int(len(OM)), noise={}, sides={})
    n_all = sum(len(cnt[s]["cells"]) for s in SIDES)
    for n in (M_HOLM, 2 * M_HOLM, n_all):
        out["noise"][str(n)] = dict(zip(("emax_abs_z", "p95_max_abs_z"), emax_abs_z(n)))
    for side in SIDES:
        bser, bk, bp = load(side)
        r = dict(base=summary(bser, bk, bp), cells={})
        for c in cnt[side]["cells"]:
            cs, ck, cp = load(side, c["cid"])
            d = delta(cs, bser)
            row = dict(c, stats=summary(cs, ck, cp), delta=d, robust=robust(cs, bser, d["dsum"]))
            r["cells"][c["cid"]] = row
        trial = [cid for cid, x in r["cells"].items() if x["trial"]]
        ps = [r["cells"][cid]["delta"]["p2"] if r["cells"][cid]["admitted"] else 1.0 for cid in trial]
        rej = holm(ps)
        for cid, rj in zip(trial, rej):
            x = r["cells"][cid]
            nbs = [y for y in r["cells"].values() if y["feat"] == x["feat"] and y["tail"] == x["tail"] and y["cid"] != cid]
            nb_ok = all(y["delta"]["dsum"] > 0 for y in nbs)
            x["holm_reject"] = bool(rj)
            x["neighbours_positive"] = nb_ok
            x["verdict"] = verdict(x["delta"], rj, x["robust"], nb_ok, x["admitted"])
        vs = [r["cells"][cid]["verdict"] for cid in trial]
        if any(v.startswith("работает") for v in vs):
            ans = "работает"
        elif all(r["cells"][cid]["delta"]["dsum"] < 0 for cid in trial if r["cells"][cid]["admitted"]) and any(r["cells"][cid]["admitted"] for cid in trial):
            ans = "не работает"
        else:
            ans = "не ясно"
        r["answer"] = ans
        for cid, x in r["cells"].items():           # шум по Sharpe: ожидаемый максимум из 72 клеток и дефлированный Sharpe (60 сут.)
            sh = x["stats"]["sharpe"]
            se = math.sqrt(365.0 / len(OM) * (1 + sh * sh / 730.0)) if sh == sh else float("nan")
            x["noise_sharpe72"] = lib.sb.significance(sh, se, n_all) if sh == sh else None
        r["noise_flags"] = {cid: bool(abs(x["delta"]["z"]) > out["noise"][str(n_all)]["emax_abs_z"]) for cid, x in r["cells"].items()}
        out["sides"][side] = r
        say("== %s: база ω %d сделок, Σ %+.1f $, Sharpe %.2f; ответ: %s" % (side, r["base"]["n"], r["base"]["sum"], r["base"]["sharpe"], ans))
        for cid in trial:
            x = r["cells"][cid]
            d = x["delta"]
            say("   %-9s ΔΣ %+.1f $ [%+.1f; %+.1f], p %.4f, Холм %s, MDE %.0f $, месяцы %s, устойчиво %s, соседи + %s → %s" % (
                cid, d["dsum"], d["lo95"], d["hi95"], d["p2"], x["holm_reject"], d["mde_sum_holm"], {k: round(v, 1) for k, v in d["months"].items()}, x["robust"]["holds"], x["neighbours_positive"], x["verdict"]))
    # контекст (критик 20.11): совместный Холм m = 73 (61 прежняя пробная + 12 новых). Прежние p все > 0,0008 > 0,05/62, поэтому ступени
    # Холма ниже 0,05/62 проходят только новые клетки: новые p по возрастанию сравниваются с 0,05/73, 0,05/72, … (точно, пока порог < мин. прежнего p).
    allt = [(s, c) for s in SIDES for c, x in out["sides"][s]["cells"].items() if x["trial"]]
    ps = [out["sides"][s]["cells"][c]["delta"]["p2"] for s, c in allt]
    for (s, c), rj in zip(allt, holm_offset(ps, 73)):
        out["sides"][s]["cells"][c]["holm73_reject"] = rj
    both = holm(ps)
    for (s, c), rj in zip(allt, both):
        out["sides"][s]["cells"][c]["holm12_both_reject"] = rj
    for s, c in allt:
        x = out["sides"][s]["cells"][c]
        say("   %s %s: Холм 6 %s, Холм 12 %s, Холм 73 %s, |z| %.2f против планки 72 %.2f" % (s, c, x["holm_reject"], x["holm12_both_reject"], x["holm73_reject"], abs(x["delta"]["z"]), out["noise"][str(n_all)]["emax_abs_z"]))
    r4.write_json(q("res", "select48.json"), out)


def holm_offset(ps, m, alpha=ALPHA):
    """Шаги Холма для p этих клеток внутри семейства размера m (клетки идут первыми ступенями)."""
    order = sorted(range(len(ps)), key=lambda i: ps[i])
    rej = [False] * len(ps)
    for r_, i in enumerate(order):
        if ps[i] <= alpha / (m - r_):
            rej[i] = True
        else:
            break
    return rej


COMMANDS = {"counts": cmd_counts, "cells": cmd_cells, "g8": cmd_g8, "select": cmd_select}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=list(COMMANDS))
    a = ap.parse_args()
    return COMMANDS[a.cmd](a) or 0


if __name__ == "__main__":
    sys.exit(main())
