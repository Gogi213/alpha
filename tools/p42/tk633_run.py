#!/usr/bin/env python3
"""П-42 §13.33 (TK-633): «лонг-история» и «шорт-история» монеты для сделок обеих сторон + обратный фильтр лонга. Драйвер на srv поверх готового кода и событий TK-625 (файлы `tk625_*.py` не меняются).
Подкоманды (порядок — п. 10 протокола): counts, g3x, step1, cellsall, g8, select. P&L читают только step1 / cellsall / g8 / select — после PASS критика; `counts` и `g3x` читают ключи сделок баз (монета, время сигнала, сутки входа).
Признаки на кандидате: «свои» (сторона события = сторона сделки; §13.32, значения те же) и «чужие» (сторона события противоположна стороне сделки; столбцы `xok…`). Таблица `--ftab` — оба набора.
Клетки: X (сделки стороны × чужая история, «торговать только при x ≥ c»), I (лонг × своя история, обратный «только при x = 0»: `fo=hi fv=0`). Холм — совместный с 9 пробными клетками §13.32 (их p — `/data/p42-tk625/res/select.json`).
"""
from __future__ import annotations

import argparse
import csv
import dataclasses
import datetime
import hashlib
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk604_feat as tf  # noqa: E402
import tk604_run as r4  # noqa: E402
import tk604_stats as st  # noqa: E402
import tk625_feat as f5  # noqa: E402
import tk625_run as r5  # noqa: E402  (пути и загрузчики TK-625 — только чтение)
import tk625_stats as s5  # noqa: E402

ROOT = "/data/p42-tk633"
SIDES = st.SIDES
OPP = {"L": "S", "S": "L"}
SIDE_NAME = r5.SIDE_NAME
ENG = r5.ENG
BASE = r5.BASE
say = r4.say
write_json = r4.write_json
sha256 = r4.sha256
GuardError = st.GuardError
M_TRIAL = r5.M_TRIAL
CELL_Q = r5.CELL_Q
I_DS = (7, 14)                                   # п. 4.3: обратный фильтр лонга — D = 30 не берётся (допуск)
OLD_SELECT = "/data/p42-tk625/res/select.json"   # p пробных клеток §13.32 (9 шт.) — как есть
NAMES = f5.COLS + ["x" + c for c in f5.COLS]    # столбцы --ftab: свои ok…, чужие xok…
UNIT = {("L", "X"): "ЛШ", ("S", "X"): "ШЛ", ("L", "I"): "ЛЛ-обр"}
FAIL_BUCKETS = ("0", "1–2", "3–5", "6–10", "11+", "нет значения")
_C = {}


def q(*a):
    d = os.path.join(ROOT, *a)
    os.makedirs(os.path.dirname(d), exist_ok=True)
    return d


def sub(u, name):
    return name if u.name == "o566" else name + "-" + u.name


def code_id():
    """sha256 содержимого tk625_{events,feat,run,stats}.py и tk633_run.py (12 знаков) — метка рядом с каждым числом отчёта."""
    h = hashlib.sha256()
    for n in ("tk625_events.py", "tk625_feat.py", "tk625_run.py", "tk625_stats.py", "tk633_run.py"):
        h.update(open(os.path.join(HERE, n), "rb").read())
    return h.hexdigest()[:12]


def stamp(d):
    d["def"], d["code_id"] = f5.DEF_TAG, code_id()
    return d


# ---- признаки ------------------------------------------------------------------------------------------------------------------

def hist_x(u, side):
    """История ЧУЖОЙ стороны для кандидатов `side` (лонг-сделки — события short, шорт-сделки — события long)."""
    key = ("histx", u.name, side)
    if key not in _C:
        _, cr = r5.events_all(u)
        _C[key] = f5.History(cr.take(cr.side == SIDE_NAME[OPP[side]]), r5.coin_days(u), u.coins())
    return _C[key]


def feats_x(u, side, sym, t_ms):
    return f5.features(hist_x(u, side), sym, t_ms)


def all_cols(u, side, sym, t_ms):
    """({имя столбца: массив}, f_own, f_x): свои `ok…` и чужие `xok…` (NaN — значение не определено)."""
    f_own, f_x = r5.feats_at(u, side, sym, t_ms), feats_x(u, side, sym, t_ms)
    cols = dict(f5.ok_matrix(f_own))
    cols.update({"x" + k: v for k, v in f5.ok_matrix(f_x).items()})
    return cols, f_own, f_x


def write_ftab(path, sym, sig_ms, cols):
    """CSV `sym,sig_ms,<NAMES>` (пусто — нет значения); ключ (sym, sig_ms) уникален; порядок — по (sym, sig_ms)."""
    sym = np.asarray(sym, dtype=object)
    order = sorted(range(len(sym)), key=lambda i: (sym[i], int(sig_ms[i])))
    seen = set()
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("sym,sig_ms," + ",".join(NAMES) + "\n")
        for i in order:
            key = (sym[i], int(sig_ms[i]))
            if key in seen:
                continue
            seen.add(key)
            f.write("%s,%d,%s\n" % (sym[i], int(sig_ms[i]), ",".join(tf.fmt_val(cols[c][i]) for c in NAMES)))
    return len(seen)


def excluded_hi(x, c):
    """Обратный фильтр (`fo=hi`): исключён, если значение определено и x > c; пустое проходит."""
    with np.errstate(invalid="ignore"):
        return (~np.isnan(x)) & (x > c)


def fail_bucket_index(x):
    """Корзины числа неудачных пробоев: 0 / 1–2 / 3–5 / 6–10 / 11+ / нет значения (индексы в `FAIL_BUCKETS`)."""
    x = np.asarray(x, dtype=float)
    b = np.full(len(x), 5, dtype=np.int64)
    ok = ~np.isnan(x)
    b[ok & (x == 0)] = 0
    b[ok & (x >= 1) & (x <= 2)] = 1
    b[ok & (x >= 3) & (x <= 5)] = 2
    b[ok & (x >= 6) & (x <= 10)] = 3
    b[ok & (x >= 11)] = 4
    return b


# ---- клетки --------------------------------------------------------------------------------------------------------------------

def cell_order(side):
    """Порядок клеток стороны: пробные X (D по возрастанию, q по возрастанию), пробные I (только лонг), затем соседи (m = 0,5 и 2) в том же порядке. (kind, m, D, q, trial)."""
    ms_n = [m for m in f5.MS_NAMES if m != M_TRIAL]
    out = [("X", M_TRIAL, d, qq, True) for d in f5.DS for qq in CELL_Q]
    if side == "L":
        out += [("I", M_TRIAL, d, -1.0, True) for d in I_DS]
    out += [("X", m, d, qq, False) for m in ms_n for d in f5.DS for qq in CELL_Q]
    if side == "L":
        out += [("I", m, d, -1.0, False) for m in ms_n for d in I_DS]
    return out


def cell_id(kind, m, d, qq):
    return "X_m%s_d%d_q%02d" % (m, d, int(round(qq * 100))) if kind == "X" else "I_m%s_d%d" % (m, d)


def build_cells(side, x_own, x_cross, c_map=None):
    """Клетки стороны. X: порог c — по распределению чужого столбца на сделках базы (`threshold_for`, без P&L) либо готовый `c_map` {cid: c}; I: c = 0, исключается x > 0 по своему столбцу.
    Дубль — то же множество исключённых сделок базы (первая по порядку остаётся)."""
    out, seen = [], {}
    for kind, m, d, qq, trial in cell_order(side):
        cid = cell_id(kind, m, d, qq)
        if kind == "X":
            col = "x" + f5.col_name(m, d)
            c = f5.threshold_for(x_cross[col], qq) if c_map is None else c_map.get(cid)
            mask = f5.excluded(x_cross[col], c) if c is not None else np.zeros(len(x_cross[col]), dtype=bool)
            fo = "lo"
        else:
            col, c, fo = f5.col_name(m, d), 0, "hi"
            mask = excluded_hi(x_own[col], 0)
        key = np.packbits(mask).tobytes() + len(mask).to_bytes(8, "little")
        dup = seen.get(key) if c is not None else "нет порога"
        if c is not None and dup is None:
            seen[key] = cid
        out.append(dict(cid=cid, kind=kind, m=m, D=d, q=qq, col=col, fo=fo, c=c, trial=trial, dup_of=dup, E=int(mask.sum()), mask=mask))
    return out


def jsonable(o):
    return r5.jsonable(o)


# ---- counts (без P&L) ----------------------------------------------------------------------------------------------------------

def uni_of(a):
    return r5.Uni(getattr(a, "uni", None) or "o566")


def trade_feats(u, side):
    day, sym, key = r5.trade_keys(u, side)
    cols, f_own, f_x = all_cols(u, side, sym, key)
    return day, sym, key, cols, f_own, f_x


def cmd_counts(a):
    """Таблицы `--ftab` (свои + чужие столбцы), покрытие, распределение, пороги, дубли, Jaccard — без P&L. `--cmap` — готовые пороги {сторона: {cid: c}} (вселенная повтора)."""
    u = uni_of(a)
    cmaps = json.load(open(a.cmap, encoding="utf-8")) if getattr(a, "cmap", None) else None
    allev, cr = r5.events_all(u)
    say("%s события: эпизодов %d (пригодных %d), пересечений без дублей %d (long %d, short %d)" % (
        u.name, len(allev), int((allev.ok == 1).sum()), len(cr), int((cr.side == "long").sum()), int((cr.side == "short").sum())))
    evsha = {w.name: sha256(r5.ev_path(u, w.name)) for w in st.HIST_WINDOWS}
    for side in SIDES:
        for w in st.TRADE_WINDOWS:
            s_, r_, a_ = r5.cand_stream(u, side, w.name)
            cols = all_cols(u, side, s_, a_)[0]
            n = write_ftab(q(sub(u, "ftab33"), "%s-%s.csv" % (w.name, side)), s_, r_, cols)
            say("ftab33 %s %s-%s: %d строк (дамп %d)" % (u.name, w.name, side, n, len(s_)))
        day, sym, key, x, f_own, f_x = trade_feats(u, side)
        mon = st.MONTH_OF_DAY[day]
        r = dict(n_trades=int(len(day)), ev_sha256=evsha, coverage={}, dist={}, cells=[], fail_alongside={}, empty={})
        for d in f5.DS:
            rs = r5.history(u, side).reason(sym, key, d)
            r["empty"][d] = {name: int((rs == i).sum()) for i, name in enumerate(f5.REASONS)}
            say("   D=%d: сделок %d, значение определено %d; пусто: %s" % (d, len(rs), int((rs == 0).sum()), {k: v for k, v in r["empty"][d].items() if k != f5.REASONS[0]}))
        for m in f5.MS_NAMES:
            for d in f5.DS:
                for col in (f5.col_name(m, d), "x" + f5.col_name(m, d)):
                    v = x[col]
                    have = ~np.isnan(v)
                    vv = v[have]
                    r["coverage"][col] = [round(float(have[mon == k].mean()), 3) if (mon == k).any() else None for k in range(len(st.MONTH_LEN))]
                    r["dist"][col] = dict(n=int(have.sum()), mean=round(float(vv.mean()), 3) if len(vv) else None, max=int(vv.max()) if len(vv) else None,
                                          buckets={"0": int((vv == 0).sum()), "1": int((vv == 1).sum()), "2-3": int(((vv >= 2) & (vv <= 3)).sum()), "4+": int((vv >= 4).sum())},
                                          F={str(c): round(float((vv < c).mean()), 3) for c in range(1, 8)} if len(vv) else {})
                o, fl, sl, valid = f_own[(m, d)]
                r["fail_alongside"][f5.col_name(m, d)] = dict(mean_fail=round(float(fl[valid].mean()), 3) if valid.any() else None)
        # корзины неудачных пробоев (своя сторона, m = 1): только числа сделок, без P&L
        for d in f5.DS:
            o, fl, sl, valid = f_own[(M_TRIAL, d)]
            bi = fail_bucket_index(np.where(valid, fl.astype(float), np.nan))
            r["fail_alongside"]["fail_d%d_buckets" % d] = {n_: int((bi == i).sum()) for i, n_ in enumerate(FAIL_BUCKETS)}
        cells = build_cells(side, x, x, None if cmaps is None else cmaps[side])
        ids = [c["cid"] for c in cells if c["dup_of"] is None]
        for c in cells:
            vals = x[c["col"]]
            cov = int((~np.isnan(vals)).sum())
            r["cells"].append(dict(cid=c["cid"], kind=c["kind"], m=c["m"], D=c["D"], q=c["q"], col=c["col"], fo=c["fo"], c=c["c"], trial=c["trial"], dup_of=c["dup_of"], E=c["E"], N_cov=cov,
                                   f_cov=round(c["E"] / cov, 3) if cov else None, f_all=round(c["E"] / len(day), 3),
                                   by_month=[int((c["mask"] & (mon == k)).sum()) for k in range(len(st.MONTH_LEN))]))
        jm = np.array([[st.jaccard(a_["mask"], b_["mask"]) for b_ in cells if b_["cid"] in ids] for a_ in cells if a_["cid"] in ids])
        r["jaccard_ids"], r["jaccard"] = ids, np.round(jm, 3).tolist()
        r["m_trial_nodup"] = sum(1 for c in cells if c["trial"] and c["dup_of"] is None and c["c"] is not None)
        write_json(q("res", "counts-%s-%s.json" % (u.name, side)), stamp(r))
        say("== %s %s: сделок базы %d; пробных клеток без дублей %d" % (u.name, side, len(day), r["m_trial_nodup"]))
        for col, cv in r["coverage"].items():
            if col.startswith("x") and col.startswith("xok10"):
                d_ = r["dist"][col]
                say("   %s: покрытие по месяцам %s; со значением %d, среднее %s, макс %s, корзины %s, F(x<c) %s" % (col, cv, d_["n"], d_["mean"], d_["max"], d_["buckets"], d_["F"]))
        for d in f5.DS:
            say("   неудачных пробоев (свои, m=1, D=%d): корзины %s; среднее fail %s" % (d, r["fail_alongside"]["fail_d%d_buckets" % d], r["fail_alongside"][f5.col_name(M_TRIAL, d)]["mean_fail"]))
        for c in r["cells"]:
            say("   клетка %-14s %s c=%s E=%d N_cov=%d f_cov=%s f_all=%s по месяцам %s%s" % (
                c["cid"], "пробная" if c["trial"] else "сосед", c["c"], c["E"], c["N_cov"], c["f_cov"], c["f_all"], c["by_month"], " (дубль %s)" % c["dup_of"] if c["dup_of"] else ""))
    say("counts %s готово — запись в протокол §13.33 п. 11 до чтения P&L" % u.name)


def cmd_geq(a):
    """G-eq: «свои» столбцы новой таблицы = таблицы §13.32 (`ftab[-h775]/<окно>-<сторона>.csv`) строка в строку."""
    u = uni_of(a)
    bad, n = [], 0
    for w in st.TRADE_WINDOWS:
        for side in SIDES:
            old = open(r5.q(u.sub("ftab"), "%s-%s.csv" % (w.name, side)), encoding="utf-8").read().splitlines()
            new = open(q(sub(u, "ftab33"), "%s-%s.csv" % (w.name, side)), encoding="utf-8").read().splitlines()
            k = 2 + len(f5.COLS)
            if len(old) != len(new):
                bad.append("%s-%s: строк %d ≠ %d" % (w.name, side, len(old), len(new)))
                continue
            for i, (x, y) in enumerate(zip(old, new)):
                if x != ",".join(y.split(",")[:k]):
                    bad.append("%s-%s строка %d" % (w.name, side, i))
                    break
            n += len(old) - 1
    write_json(q("gates", "geq33-%s.json" % u.name), dict(rows=n, bad=bad))
    say("G-eq %s" % u.name, "FAIL " + "; ".join(bad[:5]) if bad else "ok: свои столбцы = таблицы §13.32 (%d строк)" % n)
    if bad:
        sys.exit(1)


def cmd_g3x(a):
    """G3x причинность «чужого» признака: (а) вектор = перебор по событиям противоположной стороны; (б) мусор вместо событий, закрывающихся после t, признак не меняет; (в) ≥ 50 кандидатов на сторону, у швов ≥ 10."""
    u = uni_of(a)
    rng = np.random.default_rng(633)
    _, cr = r5.events_all(u)
    bad, res, picks = [], {}, 0
    seams = [datetime.datetime(2026, m, d, tzinfo=datetime.timezone.utc).timestamp() * 1000 for m, d in ((7, 1), (8, 26), (10, 3))]
    for side in SIDES:
        osn = SIDE_NAME[OPP[side]]
        sym, ab, _rel, _w = r5.cand_arrays(u, side)
        idx = []
        for sm in seams:
            near = np.nonzero(np.abs(ab - sm) <= 3 * st.DAY_MS)[0]
            idx += list(rng.choice(near, size=min(10, len(near)), replace=False))
        idx += list(rng.choice(len(ab), size=30, replace=False))
        hv = hist_x(u, side)
        for i in idx:
            t, s = int(ab[i]), sym[i]
            f0 = f5.features(hv, np.array([s], dtype=object), np.array([t]))
            for (m, d), (o, f, st_, v) in f0.items():
                for cls, got in (("o", o), ("f", f), ("s", st_)):
                    want = r5.brute_counts(cr, osn, s, t, d, m, cls)
                    if int(got[0]) != want:
                        bad.append("%s %s t=%d (m=%s D=%d %s): вектор %d ≠ перебор %d" % (side, s, t, m, d, cls, int(got[0]), want))
            gar = r5.garbage_after(cr, t, seed=int(t % 2**31))
            hg = f5.History(gar.take(gar.side == osn), r5.coin_days(u), u.coins())
            f1 = f5.features(hg, np.array([s], dtype=object), np.array([t]))
            for k_, v0 in f0.items():
                if any(int(x[0]) != int(y[0]) for x, y in zip(v0, f1[k_])):
                    bad.append("%s %s t=%d %s: признак изменился при замене закрытых позже t событий" % (side, s, t, k_))
            picks += 1
        res[side] = len(idx)
    write_json(q("gates", "g3x-%s.json" % u.name), dict(res=res, picks=picks, bad=bad[:20]))
    say("G3x %s" % u.name, "FAIL %d: %s" % (len(bad), bad[:5]) if bad else "ok: %d кандидатов (шов 01.07 / 26.08 / 03.10 ≥ 10 на сторону), вектор = перебор, мусор после t признак не меняет" % picks)
    if bad:
        sys.exit(1)


# ---- после PASS критика: ступень 1, клетки, G8, выбор (читают P&L) ---------------------------------------------------------------

def cells_dir(u, w, side, cid):
    return os.path.join(ROOT, sub(u, "cells"), "%s-%s" % (w, side), cid)


def load_series(u, side, kind, cid=None):
    """Склейка сделок стороны: kind 'base' (база вселенной) или 'cell' (клетка `cid`, прогон `cellsall` этого драйвера)."""
    if kind == "base":
        return r5.load_series(u, side, "base")
    tables, daily = {}, {}
    for w in st.TRADE_WINDOWS:
        d = cells_dir(u, w.name, side, cid)
        tables[w.name] = st.read_trades_csv(os.path.join(d, ENG[side] + "-trades.csv"))
        daily[w.name] = st.read_daily_csv(os.path.join(d, ENG[side] + "-daily.csv"))
    return st.splice(tables, u.coins(), daily=daily)


def base_cols(u, side, ser):
    return all_cols(u, side, np.array(ser.sym_name, dtype=object), ser.key_ms)


def cmd_step1(a):
    """Ступень 1 (п. 8): корзины ЧУЖОГО признака (m = 1, D) и числа неудачных пробоев (своя сторона) на сделках баз — сделок, Σ $, $/сделку, Sharpe, по месяцам, 90 % интервал; без фильтра и выбора."""
    u = uni_of(a)
    res = {}
    for side in SIDES:
        ser = load_series(u, side, "base")
        cols, f_own, f_x = base_cols(u, side, ser)
        r = dict(base=s5.slice_stats(ser.day, ser.val), cross={}, fail={})
        for d in f5.DS:
            r["cross"]["xok%s_d%d" % (M_TRIAL, d)] = s5.step1_rows(ser, cols["x" + f5.col_name(M_TRIAL, d)])
            o, fl, sl, valid = f_own[(M_TRIAL, d)]
            bi = fail_bucket_index(np.where(valid, fl.astype(float), np.nan))
            fr = {"все": s5.slice_stats(ser.day, ser.val)}
            for i, name in enumerate(FAIL_BUCKETS):
                m_ = bi == i
                fr[name] = s5.slice_stats(ser.day[m_], ser.val[m_])
            r["fail"]["fail%s_d%d" % (M_TRIAL, d)] = fr
        res[side] = r
        b = r["base"]
        say("== ступень 1 %s %s: сделок %d, Σ %.0f $, %.2f $/сделку, Sharpe %.2f" % (u.name, side, b["n"], b["sum"], b["mean"], b["sharpe"]))
        for grp in ("cross", "fail"):
            for col, rows in r[grp].items():
                for name, v in rows.items():
                    say("   %-10s %-12s: сделок %6d, Σ %9.0f $, %7.2f $/сделку, Sharpe %6.2f, по месяцам %s" % (
                        col, name, v["n"], v["sum"], v["mean"] if v["n"] else float("nan"), v["sharpe"], [(n_, round(s_)) for n_, s_ in v["months"]]))
    write_json(q("res", "step1-%s.json" % u.name), stamp(jsonable(res)))
    say("метка: def %s, код %s; ступень 1 %s готова" % (f5.DEF_TAG, code_id(), u.name))


def cell_info(u, side):
    return json.load(open(q("res", "counts-%s-%s.json" % (u.name, side)), encoding="utf-8"))["cells"]


def cell_variants(u, side):
    return [(c["cid"], "%s%s fa=%s fo=%s fv=%d" % (BASE[side], u.extra, c["col"], c["fo"], c["c"])) for c in cell_info(u, side) if c["dup_of"] is None and c["c"] is not None]


def cmd_cells(a):
    u = uni_of(a)
    w = r4.win(a.window)
    for side in SIDES:
        out = os.path.join(ROOT, sub(u, "cells"), "%s-%s" % (w.name, side))
        if os.path.exists(out + "/DONE"):
            continue
        ft = q(sub(u, "ftab33"), "%s-%s.csv" % (w.name, side))
        r5.tick(u, w, cell_variants(u, side), out, {}, q("log", "cells-%s-%s-%s.log" % (u.name, w.name, side)), ftab=ft)
        with open(out + "/DONE", "w", encoding="utf-8") as f:
            f.write("%s %s %s" % (f5.DEF_TAG, code_id(), sha256(ft)))
    say("cells %s %s готово" % (u.name, w.name))


def cmd_cellsall(a):
    for w in st.TRADE_WINDOWS:
        a.window = w.name
        cmd_cells(a)


def read_table(path):
    """{(монета, мс от T0): [18 значений NAMES, NaN — пусто]} таблицы `--ftab`."""
    out = {}
    with open(path, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            out[(r["sym"], int(r["sig_ms"]))] = [float(r[c]) if r[c] != "" else float("nan") for c in NAMES]
    return out


def dropped_by(v, c, fo):
    return v == v and (v < c if fo == "lo" else v > c)


def cmd_g8(a):
    """G8: `no_key = 0` и `dropped` = независимому пересчёту по таблице (строки со значением x < c при `fo=lo`, x > c при `fo=hi`) — для всех прогонов клеток вселенной."""
    u = uni_of(a)
    bad, res = [], {}
    for w in st.TRADE_WINDOWS:
        for side in SIDES:
            tab = read_table(q(sub(u, "ftab33"), "%s-%s.csv" % (w.name, side)))
            dsym, drel = tf.read_sigsx_keys(os.path.join(u.base(w.name, side), ENG[side] + "-sigsx.csv"))
            dkeys = [(s_, int(r_)) for s_, r_ in zip(dsym, drel)]
            miss = sum(1 for k in dkeys if k not in tab)
            for c in cell_info(u, side):
                if c["dup_of"] is not None or c["c"] is None:
                    continue
                j = NAMES.index(c["col"])
                exp_u = sum(1 for v in tab.values() if dropped_by(v[j], c["c"], c["fo"]))
                exp_m = sum(1 for k in dkeys if k in tab and dropped_by(tab[k][j], c["c"], c["fo"]))
                m = json.load(open(os.path.join(cells_dir(u, w.name, side, c["cid"]), "meta.json"), encoding="utf-8"))[ENG[side]]
                fc = r4.filter_counters(m)
                res["%s/%s/%s" % (w.name, side, c["cid"])] = dict(counters=fc, expected_unique=exp_u, expected_rows=exp_m, dump_without_row=miss)
                if miss or fc is None or fc["no_key"] != 0 or fc["dropped"] not in (exp_u, exp_m):
                    bad.append("%s/%s/%s: %s, ожидалось dropped %d (уникальных) / %d (строк дампа), строк дампа без строки таблицы %d" % (w.name, side, c["cid"], fc, exp_u, exp_m, miss))
    write_json(q("gates", "g8-%s.json" % u.name), dict(res=res, bad=bad))
    say("G8 %s" % u.name, "FAIL " + "; ".join(bad[:8]) if bad else "ok: no_key = 0 и dropped = пересчёту во всех %d прогонах" % len(res))
    if bad:
        sys.exit(1)


def check_inputs(u, side):
    """Охрана: читаются только входы текущего определения — `counts` с меткой `def`, прогоны клеток с `DONE` = `def2 <код> <sha256 --ftab>` для нынешней таблицы."""
    c = json.load(open(q("res", "counts-%s-%s.json" % (u.name, side)), encoding="utf-8"))
    if c.get("def") != f5.DEF_TAG:
        raise GuardError("counts %s %s: определение %r ≠ %r — вход отвергнут" % (u.name, side, c.get("def"), f5.DEF_TAG))
    for w in st.TRADE_WINDOWS:
        done = os.path.join(ROOT, sub(u, "cells"), "%s-%s" % (w.name, side), "DONE")
        got = open(done, encoding="utf-8").read().split() if os.path.exists(done) else []
        want = sha256(q(sub(u, "ftab33"), "%s-%s.csv" % (w.name, side)))
        if len(got) != 3 or got[0] != f5.DEF_TAG or got[2] != want:
            raise GuardError("клетки %s %s-%s: DONE %s ≠ (%s, …, %s) — вход отвергнут" % (u.name, w.name, side, got, f5.DEF_TAG, want[:12]))


def build_side(u, side):
    check_inputs(u, side)
    base = load_series(u, side, "base")
    x = base_cols(u, side, base)[0]
    cells = []
    for c in cell_info(u, side):
        if c["c"] is None:
            mask = np.zeros(len(base), dtype=bool)
        else:
            mask = f5.excluded(x[c["col"]], c["c"]) if c["fo"] == "lo" else excluded_hi(x[c["col"]], c["c"])
        ser = None if (c["dup_of"] is not None or c["c"] is None) else load_series(u, side, "cell", c["cid"])
        cells.append(s5.Cell(c["cid"], c["m"], c["D"], c["q"], c["c"], c["trial"], c["dup_of"], ser, mask))
    return s5.SideCells(base, cells, len(u.coins()))


def unit_of(side, cid):
    return UNIT[(side, cid[0])]


def unit_outcome(ev, repl, unit):
    """Исход единицы (п. 6): «работает» — клетка прошла g1–g5 и повторилась на o566; «не работает» — все допущенные пробные клетки единицы (≥ 1) с Δ < 0; иначе «не ясно»."""
    works = [(s, cid) for s, cid in ev["passed"] if unit_of(s, cid) == unit and repl.get((s, cid), {}).get("repl")]
    adm = [r for s in SIDES for cid, r in ev["rows"][s].items() if r["trial"] and r["admitted"] and unit_of(s, cid) == unit]
    if works:
        return "работает", [cid for _, cid in works]
    if adm and all(r["d"] < 0 for r in adm):
        return "не работает", []
    return "не ясно", []


def answer_line(outcomes):
    names = [u for u, (o, _) in outcomes.items() if o == "работает"]
    if names:
        return "работает (%s)" % ", ".join(names)
    if all(o == "не работает" for o, _ in outcomes.values()):
        return "не работает"
    return "не ясно"


def joint_holm(ev, ps_old):
    """(g1) по совместному семейству: 9 пробных клеток §13.32 (p как есть) + новые пробные клетки (п. 5). Пересчитывает g1 и `pass` новых клеток; Холм по одним новым — рядом, как контекст."""
    trial = ev["holm"]["trial"]
    ps_new = ev["holm"]["ps"]
    rej_new_only = st.holm_reject(ps_new, 0.05) if ps_new else []
    rej_all = st.holm_reject(list(ps_old) + list(ps_new), 0.05)[len(ps_old):]
    passed = []
    for i, (s, cid) in enumerate(trial):
        r = ev["rows"][s][cid]
        r["g1_new_only"] = bool(rej_new_only[i] and r["admitted"] and r["d"] > 0)
        r["g1"] = bool(rej_all[i] and r["admitted"] and r["d"] > 0)
        r["pass"] = bool(r["admitted"] and r["g1"] and r.get("g2") and r.get("g3") and r.get("g4") and r.get("g5"))
        if r["pass"]:
            passed.append((s, cid))
    ev["passed"] = passed
    ev["holm"]["m_joint"] = len(ps_old) + len(ps_new)
    ev["holm"]["ps_old"] = list(ps_old)
    return ev


def with_series(side, c):
    if c.series is not None:
        return c
    src = next((x for x in side.cells if x.cid == c.dup_of), None)
    return dataclasses.replace(c, series=src.series) if src is not None and src.series is not None else None


def cell_slices(side_cells):
    """Σ $, $/сделку, Sharpe, по месяцам — для базы и каждой клетки с рядом (дубль — ряд клетки с тем же множеством)."""
    out = {"база": s5.slice_stats(side_cells.base.day, side_cells.base.val)}
    for c in side_cells.cells:
        c2 = with_series(side_cells, c)
        if c2 is not None:
            out[c.cid] = s5.slice_stats(c2.series.day, c2.series.val)
    return out


def cmd_select(a):
    """Выбор по п. 5 на h775 (совместный Холм, g1–g5), повтор клеток-победителей на o566, ответ по п. 6."""
    h, o = r5.Uni("h775"), r5.Uni("o566")
    allom = np.arange(st.NDAYS)
    ps_old = json.load(open(OLD_SELECT, encoding="utf-8"))["holm"]["ps"]
    sh = {s: build_side(h, s) for s in SIDES}
    ev = joint_holm(s5.evaluate(sh, allom), ps_old)
    so = {s: build_side(o, s) for s in SIDES}
    repl = {}
    for s, cid in ev["passed"]:
        c = with_series(so[s], next(c for c in so[s].cells if c.cid == cid))
        if c is not None:
            repl[(s, cid)] = s5.replicate(so[s], c, allom)
    rows_o = {s: {c.cid: s5.cell_row(so[s], with_series(so[s], c), allom) for c in so[s].cells if with_series(so[s], c) is not None} for s in SIDES}
    outcomes = {u_: unit_outcome(ev, repl, u_) for u_ in ("ЛШ", "ШЛ", "ЛЛ-обр")}
    ans = answer_line(outcomes)
    sl_h = {s: cell_slices(sh[s]) for s in SIDES}
    sl_o = {s: cell_slices(so[s]) for s in SIDES}
    say("Холм совместный: m = %d (9 §13.32 + %d новых допущенных/недопущенных пробных); p новых %s" % (ev["holm"]["m_joint"], ev["holm"]["m"], [round(p_, 4) for p_ in ev["holm"]["ps"]]))
    for s in SIDES:
        b = sl_h[s]["база"]
        say("== %s h775: сделок базы %d, Σ %.0f $, %.2f $/сделку, Sharpe %.2f" % (s, b["n"], b["sum"], b["mean"], b["sharpe"]))
        for cid, r in ev["rows"][s].items():
            sl = sl_h[s].get(cid) or {}
            say("   %-14s %-7s c=%s: Δ %s ± %s $/сделку, z %s, p %s, n_c %d, n_r %d, исключено %d, Σ %s, Sharpe %s, допуск %s, g1 %s (новые %s) g2 %s g3 %s g4 %s g5 %s%s" % (
                cid, "пробная" if r["trial"] else "сосед", r["c"], "%.3f" % r["d"] if r["d"] == r["d"] else "—", "%.3f" % r["se"] if r["se"] == r["se"] else "—",
                "%.2f" % r["z"] if r["z"] == r["z"] else "—", "%.4f" % r["p"] if r["p"] == r["p"] else "—", r["n_c"], r["n_r"], r["n_ex"],
                "%.0f" % sl["sum"] if sl else "—", "%.2f" % sl["sharpe"] if sl else "—", r["admitted"], r.get("g1"), r.get("g1_new_only"), r.get("g2"), r.get("g3"), r.get("g4"), r.get("g5"),
                " ПРОШЛА" if r.get("pass") else ""))
    for (s, cid), r in repl.items():
        say("   повтор o566 %s %s: Δ %.3f ± %.3f, g2 %s g3 %s g4 %s → %s" % (s, cid, r["d"], r["se"], r.get("g2"), r.get("g3"), r.get("g4"), r["repl"]))
    for u_, v in outcomes.items():
        say("ИСХОД %s: %s %s" % (u_, *v))
    say("ОТВЕТ: %s" % ans)
    out = dict(answer=ans, outcomes={k: list(v) for k, v in outcomes.items()}, holm=ev["holm"], passed=ev["passed"], rows=ev["rows"], repl={"%s/%s" % k: v for k, v in repl.items()},
               rows_o566=rows_o, slices_h775=sl_h, slices_o566=sl_o)
    write_json(q("res", "select33.json"), stamp(jsonable(out)))
    say("метка: def %s, код %s" % (f5.DEF_TAG, code_id()))


COMMANDS = {"counts": cmd_counts, "geq": cmd_geq, "g3x": cmd_g3x, "step1": cmd_step1, "cells": cmd_cells, "cellsall": cmd_cellsall, "g8": cmd_g8, "select": cmd_select}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=sorted(COMMANDS))
    ap.add_argument("window", nargs="?")
    ap.add_argument("--uni", default="o566", choices=("o566", "h775"))
    ap.add_argument("--cmap", default="")
    a = ap.parse_args()
    try:
        COMMANDS[a.cmd](a)
    except GuardError as e:
        say("СТОП:", e)
        sys.exit(2)


if __name__ == "__main__":
    main()
