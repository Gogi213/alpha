#!/usr/bin/env python3
"""П-42 §13.25.1 (TK-644): этапы 2–3 токсичного потока на h775 — замороженные модели TK-600 (рука X, 55 признаков) и TK-598 (рука f12) как фильтр «верхняя треть» баз `L_base` / `S_base`.
  python tk644_run.py feat --window wa|wb|wc --side L|S [--procs 2]  — DERIV14 (`tk594_feat.py --mode deriv`) + TOX13 (`tk600_feat.py tox`) на кандидатах баз, сборка X с готовыми f12 / FLOW16 TK-634. P&L не читает.
  python tk644_run.py counts    — ŝ обеих рук (среднее 5 фолдовых моделей), пороги q по сделкам базы решающего окна 01.07–14.09, таблицы `--ftab`, ворота G-eq / G-K / G-M / G-P, покрытие, состав. P&L не читает.
  python tk644_run.py cells     — движок: 22 клетки на сторону × 3 окна (`fa=<клетка> fo=lo fv=0.5`). Читает P&L только после PASS (вызывается из tk644_pnl.sh).
  python tk644_run.py g8        — G8: no_key = 0, dropped = пересчёту по `--ftab`.
  python tk644_run.py select    — таблицы, Δ к базе на решающем окне, правило §13.25.1 п. 7, Холм m = 11, шумовая планка → res/select644.json."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
for _p in (os.path.join(HERE, "..", "..", ".claude", "skills", "alpha-research", "scripts"), os.path.join(HERE, "sbscripts")):
    if os.path.isdir(_p):
        sys.path.insert(0, _p)

ROOT = "/data/p42-tk644"
FEAT634 = "/data/p42-tk634/feat/h775"
MODELS = {"X": "/data/p42-tk600/fit-X/models", "F": "/data/p42-tk598/fit/models"}
OOF = {"X": "/data/p42-tk600/oof", "F": "/data/p42-tk600/oof"}
TK600_FEAT = "/data/p42-tk600/feat"
TK600_T0 = {"F-1": "2026-09-01", "jul-1": "2026-06-17", "aug-1": "2026-07-18"}
DERIV_ENV = dict(TK594_METRICS="/data/p42-tk600/metrics-all", TK594_PREMIUM="/data/p42-tk600/premium-all", TK594_FUNDING_M="/data/p42-tk600/fund-all")
SIDE_NAME = {"L": "long", "S": "short"}
DEC_LAST = 75                      # решающее окно: сутки входа 0…75 от 01.07 = 01.07–14.09 (п. 3)
MONTHS_DEC = (("jul", 0, 30), ("aug", 31, 61), ("sep1_14", 62, 75))
IN_TRAIN = (76, 135)               # 15.09–13.11 — окно обучения моделей (описательно)
MIN_MONTH = 20                     # допуск: ≥ 20 сделок у клетки в июле и в августе (п. 6)
N_VIEWED = 44                      # п. 8: 11 целей × 2 руки на F (TK-600 этап 1) + 22 клетки здесь
HOLM_ALPHA = 0.05
FV_PASS = 0.5
D0_MS = 1782864000000           # 2026-07-01 00:00 UTC


def q(*a):
    p = os.path.join(ROOT, *a)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    return p


def say(*a):
    print(*a, flush=True)


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def write_json(p, obj):
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o))


class GuardError(SystemExit):
    pass


def targets(side):
    import tk598_lib as L
    return list(L.TARGETS[SIDE_NAME[side]])


def cell_ids(side):
    return ["%s_%s" % (arm, t) for arm in ("X", "F") for t in targets(side)]


# ---- признаки (без P&L) ------------------------------------------------------------------------------------------------------

def cmd_feat(a):
    import pandas as pd
    import tk594_feat as F4
    import tk600_feat as F6
    import tk604_stats as st
    import tk625_run as r5
    u = r5.Uni("h775")
    w = next(x for x in st.TRADE_WINDOWS if x.name == a.window)
    src = os.path.join(FEAT634, "%s-%s" % (w.name, a.side))
    eps_p, flow_p = os.path.join(src, "eps-%s.parquet" % a.side), os.path.join(src, "tk594-flow-%s.parquet" % a.side)
    work = q("feat", "%s-%s" % (w.name, a.side), "x")
    work = os.path.dirname(work)
    env = dict(os.environ, TK594_TRADES=os.path.join(u.wdir(w.name), "aggtrades"), PYTHONUTF8="1", **DERIV_ENV)
    der_p, tox_p = os.path.join(work, "deriv-%s.parquet" % a.side), os.path.join(work, "tox-%s.parquet" % a.side)
    for out, cmd in ((der_p, [sys.executable, os.path.join(HERE, "tk594_feat.py"), eps_p, der_p, "--mode", "deriv", "--base-date", w.t0, "--procs", str(a.procs)]),
                     (tox_p, [sys.executable, os.path.join(HERE, "tk600_feat.py"), "tox", eps_p, tox_p, "--base-date", w.t0, "--procs", str(a.procs)])):
        if not os.path.exists(out):
            say(" ".join(cmd))
            subprocess.run(cmd, env=env, check=True)
    eps = pd.read_parquet(eps_p)
    flow, der, tox = pd.read_parquet(flow_p), pd.read_parquet(der_p), pd.read_parquet(tox_p)
    tab = eps[["ep_id", "sym", "sig_ms"] + F6.F12]
    for part, cols in ((flow, F4.FLOW), (der, F4.DERIV), (tox, F6.TOX)):
        tab = tab.merge(part[["ep_id"] + cols], on="ep_id", how="left", validate="one_to_one")
    if tab.duplicated(["sym", "sig_ms"]).any():
        raise GuardError("G-K: дубль ключа (sym, sig_ms)")
    tab = tab[["sym", "sig_ms"] + F6.X_FEATS].sort_values(["sym", "sig_ms"]).reset_index(drop=True)
    out = q("feat", "X-%s-%s.parquet" % (w.name, a.side))
    tab.to_parquet(out)
    info = dict(window=w.name, side=a.side, rows=int(len(tab)), t0=w.t0, sha256=sha256(out), nan_share={c: round(float(tab[c].isna().mean()), 4) for c in F6.X_FEATS})
    write_json(out + ".info.json", info)
    say(json.dumps(dict(window=w.name, side=a.side, rows=len(tab)), ensure_ascii=False))


# ---- оценки и пороги ---------------------------------------------------------------------------------------------------------

def load_models(arm, side):
    from catboost import CatBoostClassifier, CatBoostRanker, CatBoostRegressor
    from tk598_fit import SPECS
    cls = {"rmse": CatBoostRegressor, "logloss": CatBoostClassifier, "wlogloss": CatBoostClassifier, "rank": CatBoostRanker}
    out, shas = {}, {}
    for t in targets(side):
        for sub_, kind in SPECS[t]:
            ms = []
            for k in range(5):
                fn = "%s_%s_f%d.cbm" % (SIDE_NAME[side], sub_, k)
                p = os.path.join(MODELS[arm], fn)
                shas[fn] = sha256(p)
                m = cls[kind]()
                m.load_model(p)
                ms.append(m)
            out[sub_] = (kind, ms)
    return out, shas


def score_all(models, side, X):
    from tk598_fit import SPECS, predict

    def one(sub_):
        kind, ms = models[sub_]
        return np.mean([predict(m, kind, X) for m in ms], axis=0)
    return {t: (one("mfe") - one("mae")) if t == "T5" else one(SPECS[t][0][0]) for t in targets(side)}


def load_x(wname, side):
    import pandas as pd
    return pd.read_parquet(os.path.join(ROOT, "feat", "X-%s-%s.parquet" % (wname, side)))


def cmd_counts(a):
    import pandas as pd
    import retitr_ftab as rfx
    import tk598_lib as L
    import tk600_feat as F6
    import tk604_feat as tf
    import tk604_stats as st
    import tk625_run as r5
    import tk634_run as r34
    u = r5.Uni("h775")
    res = dict(gates={}, sides={})
    shas_all = {}
    for side in ("L", "S"):
        tabs = {w.name: load_x(w.name, side) for w in st.TRADE_WINDOWS}
        # G-K: 1:1 с потоком кандидатов
        for w in st.TRADE_WINDOWS:
            dsym, drel = tf.read_sigsx_keys(os.path.join(u.base(w.name, side), r5.ENG[side] + "-sigsx.csv"))
            dk = sorted(zip(dsym, [int(x) for x in drel]))
            tk = list(zip(tabs[w.name].sym, [int(x) for x in tabs[w.name].sig_ms]))
            if dk != tk:
                raise GuardError("G-K %s-%s: ключи таблицы X ≠ ключам потока кандидатов (%d / %d)" % (w.name, side, len(tk), len(dk)))
        # G-P: порядок столбцов
        if list(tabs["wa"].columns[2:]) != list(F6.X_FEATS):
            raise GuardError("G-P: порядок столбцов X ≠ X_FEATS")
        cov = {w: {c: round(float(t[c].notna().mean()), 4) for c in F6.X_FEATS} for w, t in tabs.items()}
        empty = [(w, c, v) for w, d in cov.items() for c, v in d.items() if v < 0.5]
        if empty:
            raise GuardError("покрытие < 50 %%: %s" % empty[:6])
        # ŝ обеих рук
        S = {}
        for arm in ("X", "F"):
            models, shas = load_models(arm, side)
            shas_all.update({"%s/%s" % (arm, k): v for k, v in shas.items()})
            feats = list(F6.X_FEATS) if arm == "X" else list(F6.F12)
            for wname, t in tabs.items():
                sc = score_all(models, side, t[feats].to_numpy(np.float64))
                for tg, v in sc.items():
                    S[(arm, tg, wname)] = v
        # сделки базы (ключи, без P&L) и их ŝ
        W, Sy, R = r34.trade_rows(u, side)
        ser = r5.load_series(u, side, "base")
        t0 = {w.name: w.t0_ms for w in st.TRADE_WINDOWS}
        if list(ser.sym_name) != list(Sy) or not np.array_equal(ser.key_ms, R + np.array([t0[x] for x in W], dtype=np.int64)):
            raise GuardError("порядок сделок базы ≠ load_series")
        day = np.asarray(ser.day)
        idx = {wn: {(s_, int(r_)): i for i, (s_, r_) in enumerate(zip(t.sym, t.sig_ms))} for wn, t in tabs.items()}
        pos = np.array([idx[W[i]].get((Sy[i], int(R[i])), -1) for i in range(len(W))])
        if (pos < 0).any():
            raise GuardError("%d сделок базы без строки X" % int((pos < 0).sum()))
        dec = day <= DEC_LAST
        sd = dict(n_base=int(len(W)), n_base_dec=int(dec.sum()), n_base_months=[int(((day >= lo) & (day <= hi)).sum()) for _, lo, hi in MONTHS_DEC], cells={})
        excl = {}
        cols = {w.name: {} for w in st.TRADE_WINDOWS}
        for arm in ("X", "F"):
            oof_path = os.path.join(OOF[arm], "oof-%s-%s-u1.parquet" % ("X" if arm == "X" else "f12", SIDE_NAME[side]))
            oof = pd.read_parquet(oof_path) if os.path.exists(oof_path) else None
            for tg in targets(side):
                lower = tg == "T4"
                st_ = np.array([S[(arm, tg, W[i])][pos[i]] for i in range(len(W))])
                qd = float(np.quantile(st_[dec][~np.isnan(st_[dec])], 1 / 3 if lower else 2 / 3))
                q136 = float(np.quantile(st_[~np.isnan(st_)], 1 / 3 if lower else 2 / 3))
                qo = L.oof_threshold(oof["s_%s" % tg].to_numpy(float), lower) if oof is not None and "s_%s" % tg in oof else None
                cid = "%s_%s" % (arm, tg)
                passes = (lambda v, qq: (v <= qq) if lower else (v >= qq))
                cand_dec = []
                for w in st.TRADE_WINDOWS:
                    v = S[(arm, tg, w.name)]
                    cols[w.name][cid] = np.where(passes(v, qd), 1.0, 0.0)
                    rel_day = (np.asarray(tabs[w.name].sig_ms, dtype=np.int64) + w.t0_ms - D0_MS) // 86_400_000
                    lo_i, hi_i = (w.lod - st.D0).days, (w.hid - st.D0).days      # зачётные сутки окна, без перекрытия окон
                    cand_dec.append(v[(rel_day >= max(0, lo_i)) & (rel_day <= min(DEC_LAST, hi_i))])
                cd = np.concatenate(cand_dec) if cand_dec else np.zeros(0)
                ex = ~passes(st_, qd)
                excl[cid] = set(np.nonzero(ex)[0].tolist())
                sd["cells"][cid] = dict(q_dec=qd, q136=q136, q_oof_u1=qo, lower=lower,
                                        share_cand_pass_all=float(np.mean(np.concatenate([cols[w.name][cid] for w in st.TRADE_WINDOWS]))),
                                        share_cand_dec_above=dict(q_dec=float(np.mean(passes(cd, qd))) if len(cd) else None, q136=float(np.mean(passes(cd, q136))) if len(cd) else None,
                                                                  q_oof=float(np.mean(passes(cd, qo))) if (len(cd) and qo is not None) else None),
                                        excl_base=int(ex.sum()), excl_base_dec=int((ex & dec).sum()),
                                        kept_base_months=[int((~ex & (day >= lo) & (day <= hi)).sum()) for _, lo, hi in MONTHS_DEC])
        # дубли и Jaccard X ↔ F
        seen = {}
        for cid in cell_ids(side):
            k = frozenset(excl[cid])
            sd["cells"][cid]["dup_of"] = seen.get(k)
            seen.setdefault(k, cid)
        for tg in targets(side):
            a_, b_ = excl["X_" + tg], excl["F_" + tg]
            sd["cells"]["X_" + tg]["jaccard_F"] = len(a_ & b_) / len(a_ | b_) if (a_ | b_) else float("nan")
        # таблицы --ftab
        for w in st.TRADE_WINDOWS:
            t = tabs[w.name]
            path = q("ftab", "%s-%s.csv" % (w.name, side))
            r34.write_cell_table(path, list(t.sym), np.asarray(t.sig_ms, dtype=np.int64), {c: cols[w.name][c] for c in cell_ids(side)}, {})
            sd.setdefault("ftab_sha256", {})[w.name] = sha256(path)
        sd["coverage"] = cov
        res["sides"][side] = sd
        res["gates"]["G-eq-" + side] = geq(tabs, side)
        say("== %s: база %d (решающее %d, месяцы %s)" % (side, sd["n_base"], sd["n_base_dec"], sd["n_base_months"]))
        for cid, c in sd["cells"].items():
            say("  %s q_dec %.5g q136 %.5g q_oof %s исключено %d (реш. %d) остаётся по мес. %s дубль %s J_F %s" % (
                cid, c["q_dec"], c["q136"], "—" if c["q_oof_u1"] is None else "%.5g" % c["q_oof_u1"], c["excl_base"], c["excl_base_dec"], c["kept_base_months"], c["dup_of"],
                "%.3f" % c["jaccard_F"] if "jaccard_F" in c else "—"))
    res["gates"]["G-M"] = shas_all
    write_json(q("res", "counts644.json"), res)
    say("G-eq:", json.dumps({k: {kk: vv for kk, vv in v.items() if kk != "bad"} for k, v in res["gates"].items() if k.startswith("G-eq")}, ensure_ascii=False))


def geq(tabs, side):
    """G-eq: строки кандидатов, чей ключ (sym, абсолютные мс) есть в таблицах X TK-600, — значения 55 признаков равны (отн. 1e-9; NaN = NaN)."""
    import pandas as pd
    import tk594_feat as F4
    import tk600_feat as F6
    import tk604_stats as st
    ref = []
    for run, t0 in TK600_T0.items():
        for v in ("u0", "u1"):
            p = os.path.join(TK600_FEAT, "feat-X-%s-%s_%s.parquet" % (run, side, v))
            if os.path.exists(p):
                d = pd.read_parquet(p, columns=["sym", "sig_ms"] + F6.X_FEATS)
                d["abs"] = d["sig_ms"].astype(np.int64) + F4.base_ms(t0)
                ref.append(d.drop(columns="sig_ms"))
    ref = pd.concat(ref).drop_duplicates(["sym", "abs"])
    t0 = {w.name: w.t0 for w in st.TRADE_WINDOWS}
    mine = []
    for wn, t in tabs.items():
        d = t.copy()
        d["abs"] = d["sig_ms"].astype(np.int64) + F4.base_ms(t0[wn])
        mine.append(d.drop(columns="sig_ms"))
    mine = pd.concat(mine).drop_duplicates(["sym", "abs"])
    m = mine.merge(ref, on=["sym", "abs"], suffixes=("", "_r"))
    bad = {}
    for c in F6.X_FEATS:
        x, y = m[c].to_numpy(float), m[c + "_r"].to_numpy(float)
        both = ~np.isnan(x) & ~np.isnan(y)
        ne = (np.isnan(x) != np.isnan(y)) | (both & (np.abs(x - y) > 1e-9 * np.maximum(1.0, np.abs(y))))
        if ne.any():
            bad[c] = int(ne.sum())
    return dict(rows=int(len(m)), defined=bool(len(m) >= 30), bad=bad, ok=not bad)


# ---- движок (после PASS) ------------------------------------------------------------------------------------------------------

def variants(u, side):
    import tk625_run as r5
    return [(cid, "%s%s fa=%s fo=lo fv=%s" % (r5.BASE[side], u.extra, cid, FV_PASS)) for cid in cell_ids(side)]


def cell_dir(wname, side, cid):
    return os.path.join(ROOT, "cells", "%s-%s" % (wname, side), cid)


def cmd_cells(a):
    import tk604_run as r4
    import tk604_stats as st
    import tk625_run as r5
    u = r5.Uni("h775")
    for w in st.TRADE_WINDOWS:
        for side in ("L", "S"):
            out = os.path.join(ROOT, "cells", "%s-%s" % (w.name, side))
            if os.path.exists(out + "/DONE"):
                continue
            ftab = os.path.join(ROOT, "ftab", "%s-%s.csv" % (w.name, side))
            r5.tick(u, w, variants(u, side), out, {}, q("log", "cells-%s-%s.log" % (w.name, side)), ftab=ftab)
            with open(out + "/DONE", "w", encoding="utf-8") as f:
                f.write("%d %s\n" % (len(variants(u, side)), r4.sha256(ftab)))
            say("cells %s-%s готово" % (w.name, side))


def cmd_g8(a):
    import tk604_feat as tf
    import tk604_run as r4
    import tk604_stats as st
    import tk625_run as r5
    import tk634_run as r34
    u = r5.Uni("h775")
    bad, res = [], {}
    for w in st.TRADE_WINDOWS:
        for side in ("L", "S"):
            ftab = os.path.join(ROOT, "ftab", "%s-%s.csv" % (w.name, side))
            dsym, drel = tf.read_sigsx_keys(os.path.join(u.base(w.name, side), r5.ENG[side] + "-sigsx.csv"))
            dkeys = [(s_, int(r_)) for s_, r_ in zip(dsym, drel)]
            for cid in cell_ids(side):
                tab = r34.read_table_col(ftab, cid)
                miss = sum(1 for k in dkeys if k not in tab)
                exp_u = sum(1 for v in tab.values() if v == v and v < FV_PASS)
                exp_m = sum(1 for k in dkeys if k in tab and tab[k] < FV_PASS)
                m = json.load(open(os.path.join(cell_dir(w.name, side, cid), "meta.json"), encoding="utf-8"))[r5.ENG[side]]
                fc = r4.filter_counters(m)
                res["%s/%s/%s" % (w.name, side, cid)] = dict(counters=fc, expected_unique=exp_u, expected_rows=exp_m, dump_without_row=miss)
                if miss or fc is None or fc["no_key"] != 0 or fc["dropped"] not in (exp_u, exp_m):
                    bad.append("%s/%s/%s: %s, ожидалось %d / %d, без строки %d" % (w.name, side, cid, fc, exp_u, exp_m, miss))
    write_json(q("gates", "g8.json"), dict(res=res, bad=bad))
    say("G8", "FAIL " + "; ".join(bad[:8]) if bad else "ok: %d прогонов" % len(res))
    if bad:
        sys.exit(1)


# ---- выбор (после PASS) -------------------------------------------------------------------------------------------------------

def daily(ser, lo, hi):
    d = np.asarray(ser.day)
    v = np.asarray(ser.val, dtype=float)
    m = (d >= lo) & (d <= hi)
    n = hi - lo + 1
    return np.bincount(d[m] - lo, weights=v[m], minlength=n), np.bincount(d[m] - lo, minlength=n).astype(float)


def mean_of(S, N):
    return float(S.sum() / N.sum()) if N.sum() > 0 else float("nan")


def drop_best(ser_c, ser_b, lo, hi, by):
    """Δ без 2 лучших групп (суток / монет) по вкладу в Δ: вклад g = Σ_g P_кл / n_кл − Σ_g P_баз / n_баз."""
    def grp(ser):
        d = np.asarray(ser.day)
        m = (d >= lo) & (d <= hi)
        keys = d[m] if by == "day" else np.asarray(ser.sym_name, dtype=object)[m]
        return keys, np.asarray(ser.val, dtype=float)[m]
    kc, vc = grp(ser_c)
    kb, vb = grp(ser_b)
    nc, nb = len(vc), len(vb)
    contrib = {}
    for k, v in zip(kc, vc):
        contrib[k] = contrib.get(k, 0.0) + v / nc
    for k, v in zip(kb, vb):
        contrib[k] = contrib.get(k, 0.0) - v / nb
    top = set(sorted(contrib, key=lambda k: -contrib[k])[:2])
    mc = np.array([k not in top for k in kc], dtype=bool)
    mb = np.array([k not in top for k in kb], dtype=bool)
    if mc.sum() == 0 or mb.sum() == 0:
        return float("nan")
    return float(vc[mc].mean() - vb[mb].mean())


def holm(ps, alpha=HOLM_ALPHA):
    order = sorted(range(len(ps)), key=lambda i: ps[i])
    rej = [False] * len(ps)
    m = len(ps)
    for r, i in enumerate(order):
        if ps[i] <= alpha / (m - r):
            rej[i] = True
        else:
            break
    return rej


def sharpe_year(S):
    sd = S.std(ddof=1)
    return float(S.mean() / sd * math.sqrt(365.0)) if sd > 0 else float("nan")


def cmd_select(a):
    import selection_bias as sb
    import tk604_stats as st
    import tk625_run as r5
    import tk638_post as p8
    u = r5.Uni("h775")
    lo, hi = 0, DEC_LAST
    out = dict(protocol="§13.25.1", window_dec="01.07–14.09 (сутки 0…75)", sides={})
    allp, allkey = [], []
    for side in ("L", "S"):
        bser, bk, bp = p8.load_cell(u, side, {w.name: (u.base(w.name, side), side) for w in st.TRADE_WINDOWS})
        Sb, Nb = daily(bser, lo, hi)
        sd = dict(base=dict(all=p8.cell_stats(bser, bk, bp), dec=dict(n=int(Nb.sum()), sum=float(Sb.sum()), mean=mean_of(Sb, Nb), sharpe=sharpe_year(Sb))), cells={})
        sers = {}
        for cid in cell_ids(side):
            ser, kinds, pnl = p8.load_cell(u, side, {w.name: (cell_dir(w.name, side, cid), side) for w in st.TRADE_WINDOWS})
            sers[cid] = ser
            Sc, Nc = daily(ser, lo, hi)
            ds = st.delta_stat(Sc, Nc, Sb, Nb)
            months = []
            for name, mlo, mhi in MONTHS_DEC:
                Scm, Ncm = daily(ser, mlo, mhi)
                Sbm, Nbm = daily(bser, mlo, mhi)
                months.append(dict(month=name, n=int(Ncm.sum()), sum=float(Scm.sum()), mean=mean_of(Scm, Ncm), delta=mean_of(Scm, Ncm) - mean_of(Sbm, Nbm), n_base=int(Nbm.sum()),
                                   sum_base=float(Sbm.sum()), sharpe=sharpe_year(Scm)))
            Sti, Nti = daily(ser, *IN_TRAIN)
            Sbi, Nbi = daily(bser, *IN_TRAIN)
            c = dict(all=p8.cell_stats(ser, kinds, pnl), dec=dict(n=int(Nc.sum()), sum=float(Sc.sum()), mean=mean_of(Sc, Nc), sharpe=sharpe_year(Sc)),
                     delta=dict(d=ds.d, se=ds.se, p=ds.p, lower5=ds.lower, upper95=ds.d + 1.645 * ds.se, dsum=ds.dsum, se_sum=ds.se_sum), months=months,
                     in_train=dict(n=int(Nti.sum()), sum=float(Sti.sum()), mean=mean_of(Sti, Nti), delta=mean_of(Sti, Nti) - mean_of(Sbi, Nbi)),
                     drop2_days=drop_best(ser, bser, lo, hi, "day"), drop2_coins=drop_best(ser, bser, lo, hi, "coin"))
            c["admitted"] = bool(months[0]["n"] >= MIN_MONTH and months[1]["n"] >= MIN_MONTH)
            sd["cells"][cid] = c
        for t in targets(side):
            Sx, Nx = daily(sers["X_" + t], lo, hi)
            Sf, Nf = daily(sers["F_" + t], lo, hi)
            dxf = st.delta_stat(Sx, Nx, Sf, Nf)
            sd["cells"]["X_" + t]["x_minus_f"] = dict(d=dxf.d, se=dxf.se, p=dxf.p)
        # бутстреп-планка по Δ: одни индексы суток для всех 22 клеток, Δ* центрирован, максимум по клеткам
        n = hi - lo + 1
        idx = st.block_idx(n, np.random.default_rng(644 + (side == "S")), st.NBOOT)
        bS, bN = Sb[idx].sum(axis=1), Nb[idx].sum(axis=1)
        mx = np.full(len(idx), -np.inf)
        for cid in cell_ids(side):
            Sc, Nc = daily(sers[cid], lo, hi)
            cS, cN = Sc[idx].sum(axis=1), Nc[idx].sum(axis=1)
            with np.errstate(invalid="ignore", divide="ignore"):
                bd = cS / cN - bS / bN
            mx = np.maximum(mx, np.nan_to_num(bd - sd["cells"][cid]["delta"]["d"], nan=-np.inf))
        sd["noise_delta"] = dict(mean_max=float(np.mean(mx[np.isfinite(mx)])), p95_max=float(np.percentile(mx[np.isfinite(mx)], 95)))
        xs = ["X_" + t for t in targets(side)]
        best = max(xs, key=lambda c: sd["cells"][c]["dec"]["sharpe"] if sd["cells"][c]["dec"]["sharpe"] == sd["cells"][c]["dec"]["sharpe"] else -9)
        shr = sd["cells"][best]["dec"]["sharpe"]
        se = math.sqrt(365.0 / n * (1 + shr * shr / 730.0))
        sd["noise_sharpe"] = dict(cell=best, **sb.significance(shr, se, N_VIEWED))
        for cid in xs:
            allp.append(sd["cells"][cid]["delta"]["p"] if sd["cells"][cid]["admitted"] else 1.0)
            allkey.append((side, cid))
        out["sides"][side] = sd
    rej = holm(allp)
    for (side, cid), r, p in zip(allkey, rej, allp):
        c = out["sides"][side]["cells"][cid]
        m = c["months"]
        c["gates"] = dict(a=bool(r), b=bool(m[0]["delta"] > 0 and m[1]["delta"] > 0), c=bool(c["drop2_days"] > 0 and c["drop2_coins"] > 0), d=c["admitted"], e=bool(c["x_minus_f"]["d"] > 0))
        c["holm_p_used"] = p
    for side in ("L", "S"):
        sd = out["sides"][side]
        xs = [sd["cells"]["X_" + t] for t in targets(side)]
        adm = [c for c in xs if c["admitted"]]
        if any(all(c["gates"].values()) for c in xs):
            ans = "работает"
        elif adm and (all(c["delta"]["d"] < 0 for c in adm) or all(c["delta"]["upper95"] < 0 for c in adm)):
            ans = "не работает"
        else:
            ans = "не ясно"
        sd["answer"] = ans
        sd["gate_counts"] = {g: sum(1 for c in xs if c["gates"][g]) for g in "abcde"}
        say("== %s: %s; ворота %s; планка Δ %.3f / %.3f; Sharpe %s %.2f планка %.2f" % (side, ans, sd["gate_counts"], sd["noise_delta"]["mean_max"], sd["noise_delta"]["p95_max"],
                                                                                    sd["noise_sharpe"]["cell"], sd["noise_sharpe"]["sharpe"], sd["noise_sharpe"]["noise_bar"]))
        for cid, c in sd["cells"].items():
            say("  %s n %d Σ %.1f $/сд %.3f Δ %+.3f ± %.3f p %.4f мес %s %s" % (cid, c["dec"]["n"], c["dec"]["sum"], c["dec"]["mean"], c["delta"]["d"], c["delta"]["se"], c["delta"]["p"],
                                                                         [(mm["n"], round(mm["delta"], 3)) for mm in c["months"]], c.get("gates", "")))
    write_json(q("res", "select644.json"), out)


COMMANDS = {"feat": cmd_feat, "counts": cmd_counts, "cells": cmd_cells, "g8": cmd_g8, "select": cmd_select}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=list(COMMANDS))
    ap.add_argument("--window", choices=("wa", "wb", "wc"))
    ap.add_argument("--side", choices=("L", "S"))
    ap.add_argument("--procs", type=int, default=2)
    a = ap.parse_args()
    COMMANDS[a.cmd](a)


if __name__ == "__main__":
    main()
