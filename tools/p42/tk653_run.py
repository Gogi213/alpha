#!/usr/bin/env python3
"""П-42 §13.53 (TK-653): CatBoost на f12 как фильтр «верхняя треть» поверх баз заморозки TK-626 (`L_base`, `S_base`) на h775,
скользящая проверка вперёд по месяцам (модель месяца m обучена только на сутках до m), замороженные модели с sha256.
Запуск на srv юнитом (`tk653_pnl.sh`), шаги по порядку:
  python3 tk653_run.py sig      — движок: двойники баз в режиме сигналов (`SIGMODE`: без лимитов портфеля, `busy=0`) × wa / wb / wc → строки обучения
  python3 tk653_run.py rows     — строки обучения стороны: сделки двойника в зачётных сутках окна + f12 кандидата (TK-644 `X-<окно>-<S>.parquet`); метка net_nf = gross − fees
  python3 tk653_run.py fit      — 4 модели на сторону (Aug, Sep, Oct, Nov): обучение на сутках < начала месяца − 1 (очистка), клетка сетки 12 — внутренним CV по 15-суточным блокам;
                                  порог q_m = квантиль 2/3 оценок модели m на сделках базы обучающей части; модели, sha256, q → fit/fit-<S>.json, freeze.json
  python3 tk653_run.py ftab     — таблицы `--ftab`: кандидат месяца m ≥ август — 1,0 если ŝ_m ≥ q_m, иначе 0,0; июль — 1,0; PASS — всё 1,0
  python3 tk653_run.py cells    — движок замороженным бинарём: `<база> fa=PASS|F653 fo=lo fv=0.5` × окно × сторона
  python3 tk653_run.py gate     — G1 бинарь = заморозке; G2 PASS = базам TK-642 байт в байт; G8 no_key = 0, dropped = пересчёту
  python3 tk653_run.py counts   — без P&L: сделок фильтра и базы по блокам проверки, доля прошедших, ворота n ≥ 30 (запасное слияние блоков — п. 6)
  python3 tk653_run.py tables   — P&L (после PASS критика и заморозки моделей): Δ $/сделку и ΔΣ по блокам и по всему окну проверки, Холм m = 2, шум, вердикт
"""
from __future__ import annotations

import argparse
import filecmp
import json
import math
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import retitr_cells as rc  # noqa: E402
import tk598_lib as L598  # noqa: E402
import tk604_feat as tf  # noqa: E402
import tk604_run as r4  # noqa: E402
import tk604_stats as st  # noqa: E402
import tk625_run as r5  # noqa: E402
import tk634_run as r34  # noqa: E402
import tk638_post as p8  # noqa: E402
import tk642_run as r42  # noqa: E402

ROOT = "/data/p42-tk653"
X644 = "/data/p42-tk644/feat"               # f12 (+ ещё 43 признака) на всех кандидатах баз h775 (TK-644), ключ (sym, sig_ms от T0 окна)
BASE642 = "/data/p42-tk642/cells"
FROZEN_BIN, FROZEN_SHA = r42.FROZEN_BIN, r42.FROZEN_SHA
ENG = st.ENGINE_SIDE
BASE = {"L": "L_base", "S": "S_base"}
FEATS = list(L598.FEATS)                    # f12, порядок TK-598 / TK-610
DAY = 86_400_000
D0_MS = 1782864000000                       # 2026-07-01 00:00 UTC
SEED = 653
FV = "0.5"
CELL = "F653"
# месяцы проверки: (имя, первые сутки, последние сутки) от 01.07; модель месяца обучается на сутках < первых − 1
TEST_MONTHS = (("aug", 31, 61), ("sep", 62, 91), ("oct", 92, 122), ("nov", 123, 135))
# блоки оценки (п. 6): по числу сделок БАЗЫ (≥ 90 → ожидаемо ≥ 30 при трети), записаны до счёта
BLOCKS = {"L": (("aug", 31, 61), ("sep", 62, 91), ("oct_nov", 92, 135)), "S": (("aug_sep", 31, 91), ("oct_nov", 92, 135))}
TEST_ALL = ("aug_nov", 31, 135)
MIN_N = 30
INNER_BLOCK = 15                            # сутки внутреннего CV
GRID = L598.GRID                            # 12 клеток depth × min_data_in_leaf × l2
ITER_CAP, OD_WAIT, MIN_ITERS = 1000, 50, 10
CB_BASE = dict(learning_rate=0.05, grow_policy="Depthwise", random_seed=SEED, thread_count=1, verbose=0, allow_writing_files=False)
M_HOLM = 2
N_RANDOM = 2000
FILES = ("trades", "daily", "coinday", "entryrej")
N_VIEWED = 28                               # счётчик f12-CatBoost: TK-592 2 + TK-598 11 + TK-610 2 + TK-644 F 11 + здесь 2
say = r4.say
G: dict = {}


def q(*a):
    d = os.path.join(ROOT, *a)
    os.makedirs(os.path.dirname(d), exist_ok=True)
    return d


def sig_keys(side):
    return rc.merge_keys(r42.frozen_keys()[BASE[side]], rc.SIGMODE)


def run_engine(w, variants, out, ftab=None, threads=2):
    u = r5.Uni("h775")
    if r4.sha256(FROZEN_BIN) != FROZEN_SHA:
        raise st.GuardError("G1: бинарь ≠ замороженному")
    st.check_dates(w.t0, w.hi)
    os.makedirs(out, exist_ok=True)
    vf = out + ".variants.txt"
    with open(vf, "w", encoding="utf-8") as f:
        f.write("".join("%s %s\n" % v for v in variants))
    d = u.wdir(w.name)
    cmd = [FROZEN_BIN, "breakout", "tick", "--cache", d + "/cache", "--trades", d + "/aggtrades", "--variants", vf,
           "--symbols", open(u.coins_file, encoding="utf-8").read().strip(), "--out", out, "--threads", str(threads)] + r5.TICK_STORE
    if ftab:
        cmd += ["--ftab", ftab]
    r4.run(cmd, {"P42_T0": w.t0}, q("log", "%s.log" % os.path.relpath(out, ROOT).replace("/", "-")))


# ---- двойники в режиме сигналов (строки обучения) ---------------------------------------------------------------------------

def cmd_sig(a):
    for w in st.TRADE_WINDOWS:
        out = os.path.join(ROOT, "sig", w.name)
        if os.path.exists(out + "/DONE"):
            continue
        run_engine(w, [("%s_sig" % s, sig_keys(s)) for s in st.SIDES], out, threads=a.threads)
        open(out + "/DONE", "w").close()
    say("sig готово")


def feat_map(side):
    """(монета, абсолютные мс сигнала) → f12 по трём окнам TK-644; совпавшие ключи окон обязаны совпадать по значениям."""
    import pandas as pd
    parts = []
    for w in st.TRADE_WINDOWS:
        t = pd.read_parquet(os.path.join(X644, "X-%s-%s.parquet" % (w.name, side)), columns=["sym", "sig_ms"] + FEATS)
        t["abs"] = t["sig_ms"].astype(np.int64) + w.t0_ms
        parts.append(t.drop(columns="sig_ms"))
    t = pd.concat(parts, ignore_index=True)
    d = t[t.duplicated(["sym", "abs"], keep=False)]
    if len(d):
        g = d.groupby(["sym", "abs"])[FEATS].nunique(dropna=False)
        if (g > 1).any().any():
            raise st.GuardError("f12: один кандидат в двух окнах с разными значениями (%d)" % int((g > 1).any(axis=1).sum()))
    return t.drop_duplicates(["sym", "abs"]).set_index(["sym", "abs"])


def cmd_rows(a):
    import pandas as pd
    res = {}
    for side in st.SIDES:
        fm = feat_map(side)
        rows = []
        for w in st.TRADE_WINDOWS:
            t = st.read_trades_csv(os.path.join(ROOT, "sig", w.name, "%s_sig" % side, ENG[side] + "-trades.csv"))
            day = st.abs_day(w, t.entry_ms)
            lo_i, hi_i = (w.lod - st.D0).days, (w.hid - st.D0).days
            m = (day >= lo_i) & (day <= hi_i)
            rows.append(pd.DataFrame(dict(sym=np.array(t.sym, dtype=object)[m], abs=t.sig_ms[m] + w.t0_ms, entry=t.entry_ms[m] + w.t0_ms - D0_MS,
                                          exit=t.exit_ms[m] + w.t0_ms - D0_MS, day=day[m], net_nf=(t.gross - t.fees)[m], win=w.name)))
        r = pd.concat(rows, ignore_index=True)
        st.check_abs_ms(r["abs"].to_numpy(np.int64), "время сигнала строки обучения")
        j = r.join(fm, on=["sym", "abs"], how="left")
        miss = j[FEATS].isna().all(axis=1)
        j = j[~miss].reset_index(drop=True)
        j.to_parquet(q("rows", "rows-%s.parquet" % side))
        mon = [int(((j["day"] >= lo) & (j["day"] <= hi)).sum()) for lo, hi in ((0, 30), (31, 61), (62, 91), (92, 122), (123, 135))]
        res[side] = dict(rows=int(len(j)), no_feature_row=int(miss.sum()), months_jul_nov=mon)
        say("rows %s: %d строк (без строки f12 — %d), по месяцам %s" % (side, len(j), int(miss.sum()), mon))
    r4.write_json(q("res", "rows.json"), res)


# ---- модели -----------------------------------------------------------------------------------------------------------------

def make_model(cell, iterations, early):
    from catboost import CatBoostRegressor
    depth, mdl, l2 = cell
    p = dict(CB_BASE, depth=depth, min_data_in_leaf=mdl, l2_leaf_reg=l2, iterations=iterations)
    if early:
        p.update(od_type="Iter", od_wait=OD_WAIT)
    return CatBoostRegressor(loss_function="RMSE", **p)


def train_idx(day, entry, exit_, first):
    """Строки обучения модели месяца с первыми сутками `first`: сигнал не позже first − 2, выход раньше (first − 1)·DAY (очистка 1 сутки)."""
    return np.nonzero((np.asarray(day) <= first - 2) & (np.asarray(exit_) < (first - 1) * DAY))[0]


def inner_blocks(first):
    return [(d0, min(d0 + INNER_BLOCK - 1, first - 2)) for d0 in range(0, first - 1, INNER_BLOCK)]


def cv_task(args):
    cell, blk = args
    U, X, tr_all = G["U"], G["X"], G["tr"]
    sub = U.iloc[tr_all]
    tr = tr_all[L598.purge_train(sub["day"].to_numpy(), sub["entry"].to_numpy(), sub["exit"].to_numpy(), blk)]
    dd = U["day"].to_numpy()[tr_all]
    va = tr_all[(dd >= blk[0]) & (dd <= blk[1])]
    y = U["net_nf"].to_numpy(float)
    if len(tr) < 50 or len(va) < 10:
        return cell, blk, None, None, len(tr), len(va)
    lo, hi = L598.clip_bounds(y[tr])
    from catboost import Pool
    m = make_model(cell, ITER_CAP, True)
    m.fit(Pool(X[tr], label=np.clip(y[tr], lo, hi)), eval_set=Pool(X[va], label=np.clip(y[va], lo, hi)), use_best_model=True)
    loss = float(np.sqrt(np.mean((m.predict(X[va]) - np.clip(y[va], lo, hi)) ** 2)))
    return cell, blk, loss, int(m.get_best_iteration()) + 1, len(tr), len(va)


def pmap(fn, tasks, procs):
    if procs <= 1:
        return [fn(t) for t in tasks]
    import multiprocessing
    with ProcessPoolExecutor(procs, mp_context=multiprocessing.get_context("fork")) as ex:
        return list(ex.map(fn, tasks, chunksize=2))


def base_trades_x(side):
    """Сделки базы (ряд `load_cell` TK-642) с f12 по ключу (монета, абсолютные мс сигнала)."""
    bs, _, _ = p8.load_cell(r5.Uni("h775"), side, {w.name: (os.path.join(BASE642, w.name, BASE[side]), side) for w in st.TRADE_WINDOWS})
    fm = feat_map(side)
    idx = [fm.index.get_loc((s, int(k))) if (s, int(k)) in fm.index else -1 for s, k in zip(bs.sym_name, bs.key_ms)]
    idx = np.array(idx)
    if (idx < 0).any():
        raise st.GuardError("%s: %d сделок базы без строки f12" % (side, int((idx < 0).sum())))
    return bs, fm[FEATS].to_numpy(np.float64)[idx]


def cmd_fit(a):
    import catboost
    import pandas as pd
    os.makedirs(q("fit", "models", "x"), exist_ok=True)
    freeze = dict(protocol="П-42 §13.53", catboost=catboost.__version__, seed=SEED, feats=FEATS, models={})
    for side in st.SIDES:
        U = pd.read_parquet(q("rows", "rows-%s.parquet" % side))
        X = U[FEATS].to_numpy(np.float64)
        bs, bX = base_trades_x(side)
        bday = np.asarray(bs.day)
        out = dict(side=side, months={})
        for name, first, last in TEST_MONTHS:
            tr = train_idx(U["day"], U["entry"], U["exit"], first)
            G.update(U=U, X=X, tr=tr)
            blocks = inner_blocks(first)
            cv = {}
            for cell, blk, loss, bi, ntr, nva in pmap(cv_task, [(c, b) for c in GRID for b in blocks], a.procs):
                if loss is not None:
                    cv.setdefault(cell, []).append((blk, loss, bi, ntr, nva))
            table = [dict(cell=list(c), loss_mean=float(np.mean([r[1] for r in v])), best_iter=[r[2] for r in v], n_blocks=len(v)) for c, v in cv.items()]
            nb = max(t_["n_blocks"] for t_ in table)
            table = [t_ for t_ in table if t_["n_blocks"] == nb]
            best = min(table, key=lambda t_: t_["loss_mean"])
            cell = tuple(best["cell"])
            n_it = max(MIN_ITERS, int(round(float(np.mean(best["best_iter"])))))
            y = U["net_nf"].to_numpy(float)
            lo, hi = L598.clip_bounds(y[tr])
            from catboost import Pool
            mdl = make_model(cell, n_it, False)
            mdl.fit(Pool(X[tr], label=np.clip(y[tr], lo, hi)))
            p = q("fit", "models", "%s_%s.cbm" % (side, name))
            mdl.save_model(p)
            btr = (bday <= first - 2)
            sb = mdl.predict(bX[btr])
            qm = float(np.quantile(sb, 2 / 3))
            out["months"][name] = dict(first_day=first, n_train=int(len(tr)), inner_blocks=blocks, cv=table, chosen=list(cell), iterations=n_it, clip=[lo, hi],
                                       q=qm, n_base_train=int(btr.sum()), file=os.path.basename(p), sha256=r4.sha256(p))
            freeze["models"]["%s_%s" % (side, name)] = dict(file=os.path.basename(p), sha256=out["months"][name]["sha256"], q=qm, cell=list(cell), iterations=n_it)
            say("fit %s %s: строк %d, клетка %s, итераций %d, q %.4f (сделок базы %d)" % (side, name, len(tr), cell, n_it, qm, int(btr.sum())))
        r4.write_json(q("fit", "fit-%s.json" % side), out)
    freeze["t_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    r4.write_json(q("fit", "freeze.json"), freeze)


def month_of(day):
    out = np.full(len(day), -1)
    for k, (_, lo, hi) in enumerate(TEST_MONTHS):
        out[(day >= lo) & (day <= hi)] = k
    return out


def load_models(side):
    from catboost import CatBoostRegressor
    fz = json.load(open(q("fit", "freeze.json"), encoding="utf-8"))
    ms = []
    for name, _, _ in TEST_MONTHS:
        e = fz["models"]["%s_%s" % (side, name)]
        p = q("fit", "models", e["file"])
        if r4.sha256(p) != e["sha256"]:
            raise st.GuardError("модель %s: sha256 ≠ заморозке" % e["file"])
        m = CatBoostRegressor()
        m.load_model(p)
        ms.append((m, e["q"]))
    return ms


def ftab_path(wname, side):
    return q("ftab", "%s-%s.csv" % (wname, side))


def cmd_ftab(a):
    import pandas as pd
    res = {}
    for side in st.SIDES:
        ms = load_models(side)
        for w in st.TRADE_WINDOWS:
            t = pd.read_parquet(os.path.join(X644, "X-%s-%s.parquet" % (w.name, side)), columns=["sym", "sig_ms"] + FEATS)
            dsym, drel = tf.read_sigsx_keys(os.path.join(r5.Uni("h775").base(w.name, side), ENG[side] + "-sigsx.csv"))
            if sorted(zip(dsym, [int(x) for x in drel])) != list(zip(t.sym, [int(x) for x in t.sig_ms])):
                raise st.GuardError("G-K %s-%s: ключи X ≠ потоку кандидатов" % (w.name, side))
            absms = t["sig_ms"].to_numpy(np.int64) + w.t0_ms
            st.check_abs_ms(absms, "время сигнала кандидата")
            day = (absms - D0_MS) // DAY
            mo = month_of(day)
            X = t[FEATS].to_numpy(np.float64)
            keep = np.ones(len(t))
            for k, (m, qm) in enumerate(ms):
                sel = mo == k
                if sel.any():
                    keep[sel] = (m.predict(X[sel]) >= qm).astype(float)
            with open(ftab_path(w.name, side), "w", encoding="utf-8", newline="\n") as f:
                f.write("sym,sig_ms,PASS,%s\n" % CELL)
                for s_, r_, v in zip(t.sym, t.sig_ms, keep):
                    f.write("%s,%d,1.0,%.1f\n" % (s_, int(r_), v))
            res["%s-%s" % (w.name, side)] = dict(cand=int(len(t)), by_month={TEST_MONTHS[k][0]: [int((mo == k).sum()), int(((mo == k) & (keep > 0.5)).sum())] for k in range(len(TEST_MONTHS))})
            say("ftab %s-%s: %s" % (w.name, side, res["%s-%s" % (w.name, side)]))
    r4.write_json(q("res", "ftab.json"), res)


def cell_dir(wname, side, vid):
    return os.path.join(ROOT, "cells", "%s-%s" % (wname, side), vid)


def variants(side):
    b = BASE[side]
    k = r42.frozen_keys()[b]
    return [("%s__PASS" % b, "%s fa=PASS fo=lo fv=%s" % (k, FV)), ("%s__%s" % (b, CELL), "%s fa=%s fo=lo fv=%s" % (k, CELL, FV))]


def cmd_cells(a):
    for w in st.TRADE_WINDOWS:
        for side in st.SIDES:
            out = os.path.join(ROOT, "cells", "%s-%s" % (w.name, side))
            if os.path.exists(out + "/DONE"):
                continue
            run_engine(w, variants(side), out, ftab=ftab_path(w.name, side), threads=a.threads)
            with open(out + "/DONE", "w", encoding="utf-8") as f:
                f.write("%s\n" % r4.sha256(ftab_path(w.name, side)))
    say("cells готово")


def cmd_gate(a):
    bad, g8, n = [], {}, 0
    if r4.sha256(FROZEN_BIN) != FROZEN_SHA:
        bad.append("G1: бинарь ≠ замороженному")
    for w in st.TRADE_WINDOWS:
        for side in st.SIDES:
            eng, b = ENG[side], BASE[side]
            for f in FILES:
                n += 1
                if not filecmp.cmp(os.path.join(cell_dir(w.name, side, b + "__PASS"), "%s-%s.csv" % (eng, f)), os.path.join(BASE642, w.name, b, "%s-%s.csv" % (eng, f)), shallow=False):
                    bad.append("G2: %s/%s__PASS/%s-%s ≠ база TK-642" % (w.name, b, eng, f))
            dsym, drel = tf.read_sigsx_keys(os.path.join(r5.Uni("h775").base(w.name, side), eng + "-sigsx.csv"))
            dkeys = list(zip(dsym.tolist(), [int(r) for r in drel]))
            for vid, _ in variants(side):
                tab = r34.read_table_col(ftab_path(w.name, side), vid.split("__")[1])
                miss = sum(1 for k in dkeys if k not in tab)
                exp_u = sum(1 for v in tab.values() if v < 0.5)
                exp_m = sum(1 for k in dkeys if tab.get(k, 1.0) < 0.5)
                fc = r4.filter_counters(json.load(open(os.path.join(cell_dir(w.name, side, vid), "meta.json"), encoding="utf-8"))[eng])
                g8["%s-%s/%s" % (w.name, side, vid)] = dict(counters=fc, expected_unique=exp_u, expected_rows=exp_m, dump_without_row=miss)
                if miss or fc is None or fc["no_key"] != 0 or fc["dropped"] not in (exp_u, exp_m):
                    bad.append("G8: %s-%s/%s: %s, ожидалось %d / %d, без строки %d" % (w.name, side, vid, fc, exp_u, exp_m, miss))
    r4.write_json(q("gates", "gates.json"), dict(g2_files=n, g8=g8, bad=bad))
    say("G1/G2/G8", "FAIL " + "; ".join(bad[:8]) if bad else "ok: %d файлов PASS = базам TK-642; G8 ok в %d прогонах" % (n, len(g8)))
    if bad:
        sys.exit(1)


def load(side, vid):
    if vid == "BASE":
        dirs = {w.name: (os.path.join(BASE642, w.name, BASE[side]), side) for w in st.TRADE_WINDOWS}
    else:
        dirs = {w.name: (cell_dir(w.name, side, "%s__%s" % (BASE[side], vid)), side) for w in st.TRADE_WINDOWS}
    return p8.load_cell(r5.Uni("h775"), side, dirs)


def n_in(ser, lo, hi):
    d = np.asarray(ser.day)
    return int(((d >= lo) & (d <= hi)).sum())


def blocks_used(side, cs):
    """Блоки п. 6: если в каком-то блоке сделок фильтра < 30 — один блок 01.08–13.11; если и там < 30 — сторона не определена."""
    bl = BLOCKS[side]
    if all(n_in(cs, lo, hi) >= MIN_N for _, lo, hi in bl):
        return list(bl), "blocks"
    if n_in(cs, TEST_ALL[1], TEST_ALL[2]) >= MIN_N:
        return [TEST_ALL], "merged"
    return [], "undefined"


def cmd_counts(a):
    """Без P&L: сделок фильтра и базы по месяцам и блокам; пересечение по ключу; доля сделок базы, прошедших фильтр."""
    res = {}
    for side in st.SIDES:
        bs, _, _ = load(side, "BASE")
        cs, _, _ = load(side, CELL)
        bk = set(zip(bs.sym_name, [int(x) for x in bs.key_ms]))
        ck = set(zip(cs.sym_name, [int(x) for x in cs.key_ms]))
        bl, mode = blocks_used(side, cs)
        res[side] = dict(months={n: [n_in(bs, lo, hi), n_in(cs, lo, hi)] for n, lo, hi in TEST_MONTHS}, blocks={n: [n_in(bs, lo, hi), n_in(cs, lo, hi)] for n, lo, hi in BLOCKS[side]},
                         test_all=[n_in(bs, *TEST_ALL[1:]), n_in(cs, *TEST_ALL[1:])], in_base=len(ck & bk), only_cell=len(ck - bk), mode=mode, blocks_used=[b[0] for b in bl])
        say("counts %s: %s" % (side, res[side]))
    r4.write_json(q("res", "counts.json"), res)


# ---- P&L (после PASS) ------------------------------------------------------------------------------------------------------

def daily(ser, lo, hi):
    d, v = np.asarray(ser.day), np.asarray(ser.val, dtype=float)
    m = (d >= lo) & (d <= hi)
    return np.bincount(d[m] - lo, weights=v[m], minlength=hi - lo + 1), np.bincount(d[m] - lo, minlength=hi - lo + 1).astype(float)


def sharpe_year(S):
    sd = S.std(ddof=1)
    return float(S.mean() / sd * math.sqrt(365.0)) if sd > 0 else float("nan")


def sub(ser, lo, hi):
    d = np.asarray(ser.day)
    m = (d >= lo) & (d <= hi)
    return np.asarray(ser.val, dtype=float)[m], d[m], np.asarray(ser.sym_name, dtype=object)[m]


def drop2(cs, bs, lo, hi, by):
    """Δ $/сделку без двух групп (суток / монет) с наибольшим вкладом в Δ (как `tk644_run.drop_best`)."""
    vc, dc, sc = sub(cs, lo, hi)
    vb, db, sb = sub(bs, lo, hi)
    kc, kb = (dc, db) if by == "day" else (sc, sb)
    contrib = {}
    for k, v in zip(kc, vc):
        contrib[k] = contrib.get(k, 0.0) + v / len(vc)
    for k, v in zip(kb, vb):
        contrib[k] = contrib.get(k, 0.0) - v / len(vb)
    top = set(sorted(contrib, key=lambda k: -contrib[k])[:2])
    mc, mb = np.array([k not in top for k in kc]), np.array([k not in top for k in kb])
    return float(vc[mc].mean() - vb[mb].mean()) if mc.sum() and mb.sum() else float("nan")


def block_stats(cs, bs, lo, hi):
    Sc, Nc = daily(cs, lo, hi)
    Sb, Nb = daily(bs, lo, hi)
    ds = st.delta_stat(Sc, Nc, Sb, Nb)
    return dict(lo=lo, hi=hi, n=int(Nc.sum()), n_base=int(Nb.sum()), sum=float(Sc.sum()), sum_base=float(Sb.sum()),
                mean=float(Sc.sum() / Nc.sum()), mean_base=float(Sb.sum() / Nb.sum()), sharpe=sharpe_year(Sc), sharpe_base=sharpe_year(Sb),
                d=ds.d, se=ds.se, p1=ds.p, lower5=ds.lower, upper95=ds.d + 1.645 * ds.se, dsum=ds.dsum, se_sum=ds.se_sum,
                mde=(r42.zq(1 - 0.05 / M_HOLM) + r42.Z_POW) * ds.se,
                drop2_days=drop2(cs, bs, lo, hi, "day"), drop2_coins=drop2(cs, bs, lo, hi, "coin"))


def random_third(bs, blocks, n_cells, rng):
    """Шум «случайная треть» (модель, на допущении: фильтр = случайный отбор сделок базы без перестановки портфеля): Δ $/сделку случайного подмножества
    той же численности, что у фильтра, в каждом блоке; максимум по блокам."""
    out = np.full(N_RANDOM, -np.inf)
    for lo, hi, k in blocks:
        v, _, _ = sub(bs, lo, hi)
        if k <= 0 or k > len(v):
            continue
        mu = v.mean()
        dd = np.array([v[rng.choice(len(v), k, replace=False)].mean() - mu for _ in range(N_RANDOM)])
        out = np.maximum(out, dd)
    return out


def verdict(c, rej):
    bl = c["blocks"]
    if c["mode"] == "undefined":
        return "не определено: сделок фильтра < 30 в окне проверки"
    if rej and c["all"]["d"] > 0 and all(b["d"] > 0 for b in bl) and c["all"]["drop2_days"] > 0 and c["all"]["drop2_coins"] > 0:
        return "(а) фильтр отбирает сделки лучше базы — найдено на этих данных"
    if c["all"]["upper95"] < 0:
        return "(б) фильтр хуже базы на сделку"
    if rej:
        return "(в) значимо, не устойчиво (блоки / сутки / монеты)"
    return "(г) не различимо: |Δ| < MDE %.3f $/сделку не исключено" % c["all"]["mde"]


def cmd_tables(a):
    res = dict(protocol="§13.53", m_holm=M_HOLM, n_viewed=N_VIEWED, noise={}, sides={})
    for n in (M_HOLM, N_VIEWED):
        res["noise"]["emax_abs_z_%d" % n] = r42.emax_abs_z(n)
    rng = np.random.default_rng(SEED)
    rnd_blocks = []
    for side in st.SIDES:
        bs, bk, bp = load(side, "BASE")
        cs, ck, cp = load(side, CELL)
        bl, mode = blocks_used(side, cs)
        c = dict(mode=mode, base=p8.cell_stats(bs, bk, bp), cell=p8.cell_stats(cs, ck, cp), blocks=[], months=[])
        for name, lo, hi in bl:
            b = block_stats(cs, bs, lo, hi)
            b["name"] = name
            c["blocks"].append(b)
            rnd_blocks.append((bs, lo, hi, b["n"]))
        c["all"] = block_stats(cs, bs, TEST_ALL[1], TEST_ALL[2]) if mode != "undefined" else None
        for name, lo, hi in TEST_MONTHS:
            Sc, Nc = daily(cs, lo, hi)
            Sb, Nb = daily(bs, lo, hi)
            c["months"].append(dict(month=name, n=int(Nc.sum()), sum=float(Sc.sum()), n_base=int(Nb.sum()), sum_base=float(Sb.sum()),
                                    d=(Sc.sum() / Nc.sum() - Sb.sum() / Nb.sum()) if Nc.sum() and Nb.sum() else float("nan")))
        res["sides"][side] = c
    # шум: случайная треть по всем клеткам (стороны × блоки), максимум
    mx = np.full(N_RANDOM, -np.inf)
    for bs, lo, hi, k in rnd_blocks:
        mx = np.maximum(mx, random_third(bs, [(lo, hi, k)], 1, rng))
    res["noise"]["random_third_max_d"] = dict(mean=float(mx[np.isfinite(mx)].mean()), p95=float(np.percentile(mx[np.isfinite(mx)], 95)), cells=len(rnd_blocks))
    sides = [s for s in st.SIDES if res["sides"][s]["mode"] != "undefined"]
    ps = [res["sides"][s]["all"]["p1"] for s in sides]
    rej = dict(zip(sides, st.holm_reject(ps))) if ps else {}
    for s in st.SIDES:
        c = res["sides"][s]
        c["holm_reject"] = bool(rej.get(s, False))
        c["verdict"] = verdict(c, c["holm_reject"])
        say("== %s (%s): база n %d Σ %+.1f; фильтр n %d Σ %+.1f; %s" % (s, c["mode"], c["base"]["n"], c["base"]["sum"], c["cell"]["n"], c["cell"]["sum"], c["verdict"]))
        for b in c["blocks"] + ([c["all"]] if c["all"] else []):
            say("  %s..%s n %d/%d Σ %+.1f/%+.1f $/сд %+.3f/%+.3f Δ %+.3f ± %.3f p1 %.4f ΔΣ %+.1f ± %.1f Sh %.2f/%.2f MDE %.3f −2сут %+.3f −2мон %+.3f" % (
                b["lo"], b["hi"], b["n"], b["n_base"], b["sum"], b["sum_base"], b["mean"], b["mean_base"], b["d"], b["se"], b["p1"], b["dsum"], b["se_sum"],
                b["sharpe"], b["sharpe_base"], b["mde"], b["drop2_days"], b["drop2_coins"]))
    say("шум: %s" % res["noise"])
    r4.write_json(q("res", "tables.json"), res)


COMMANDS = {"sig": cmd_sig, "rows": cmd_rows, "fit": cmd_fit, "ftab": cmd_ftab, "cells": cmd_cells, "gate": cmd_gate, "counts": cmd_counts, "tables": cmd_tables}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=list(COMMANDS))
    ap.add_argument("--threads", type=int, default=2)
    ap.add_argument("--procs", type=int, default=2)
    a = ap.parse_args()
    COMMANDS[a.cmd](a)


if __name__ == "__main__":
    main()
