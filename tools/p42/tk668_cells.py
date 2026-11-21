#!/usr/bin/env python3
"""П-42 §13.68 (TK-668): клетки фильтров E5 / EP / B, пороги, таблицы `--ftab`, счёт E30, события E8 и лента — без P&L. Подмодуль `tk668_run.py` (команды) и `tk668_post.py` (P&L после PASS)."""
from __future__ import annotations

import os

import numpy as np

import tk604_stats as st
import tk668_feat as ft

QS = (0.10, 0.25, 0.40, 0.60, 0.75, 0.90)
EP_COLS = ("X24", "X7", "XP", "XV")
MIN_N, MIN_MONTH_SUB = 70, 8


def cell_specs():
    """Клетки: пробные (trial) и соседи. `group` — пробная клетка-хозяин, `family` — семейство Холма (E / B), `op`: lt / gt (значение ниже / выше порога), abs / neg / pos (шок BTC)."""
    sp = []
    for cid, x, trial in (("RM10", 1.0, True), ("RM05", 0.5, False), ("RM20", 2.0, False)):
        sp.append(dict(cid=cid, fam="E5", col="R", op="lt", x=x, q=None, trial=trial, group="RM10", family="E"))
    for col in EP_COLS:
        for hl in ("H", "L"):
            host = col + hl
            qm, nbs, op = (0.75, (0.90, 0.60), "gt") if hl == "H" else (0.25, (0.10, 0.40), "lt")
            sp.append(dict(cid=host, fam="EP", col=col, op=op, x=None, q=qm, trial=True, group=host, family="E"))
            for qn in nbs:
                sp.append(dict(cid="%s_q%02d" % (host, round(qn * 100)), fam="EP", col=col, op=op, x=None, q=qn, trial=False, group=host, family="E"))
    for h in ft.HORIZONS:
        for nm, op in (("Bsk", "abs"), ("Bwi", "neg"), ("Bag", "pos")):
            host = "%s_%d" % (nm, h)
            sp.append(dict(cid=host, fam="B", col="bz%d" % h, op=op, x=ft.K_MAIN, q=None, trial=True, group=host, family="B"))
            for k in ft.K_NB:
                sp.append(dict(cid="%s_k%d" % (host, round(k * 10)), fam="B", col="bz%d" % h, op=op, x=k, q=None, trial=False, group=host, family="B"))
    return sp


def excl_mask(sp, v, thr):
    """Исключённые строки (пусто проходит). thr — {столбец: {"0.75": число}} порогов квантилей; E5 и B — фиксированные."""
    ok = v == v
    x = thr[sp["col"]]["%.2f" % sp["q"]] if sp["fam"] == "EP" else sp["x"]
    with np.errstate(invalid="ignore"):
        if sp["op"] == "lt":
            return ok & (v < x)
        if sp["op"] == "gt":
            return ok & (v > x)
        if sp["op"] == "abs":
            return ok & (np.abs(v) >= x)
        if sp["op"] == "neg":
            return ok & (v <= -x)
        return ok & (v >= x)


def cell_label(sp):
    if sp["fam"] == "E5":
        return "исключить сделки, где до следующего уровня < %g NATR" % sp["x"]
    if sp["fam"] == "EP":
        return "исключить значение %s %s q%d сделок базы стороны" % (sp["col"], "выше" if sp["op"] == "gt" else "ниже", round(sp["q"] * 100))
    h = int(sp["col"][2:])
    what = {"abs": "любой шок BTC", "neg": "шок BTC против сделки", "pos": "шок BTC по сделке"}[sp["op"]]
    return "исключить %s за %d мин (|ход| ≥ %g σ)" % (what, h, sp["x"])


def quantile_table(cols):
    """Квантили EP по значениям на сделках базы стороны (конечные, linear): {столбец: {"0.25": …, "n": …}}."""
    thr = {}
    for c in EP_COLS:
        v = cols[c][np.isfinite(cols[c])]
        thr[c] = {"%.2f" % q: float(np.quantile(v, q, method="linear")) for q in QS}
        thr[c]["n"] = int(len(v))
    return thr


def spearman(x, y):
    import pandas as pd
    ok = np.isfinite(x) & np.isfinite(y)
    if ok.sum() < 10:
        return float("nan"), int(ok.sum())
    return float(np.corrcoef(pd.Series(x[ok]).rank().to_numpy(), pd.Series(y[ok]).rank().to_numpy())[0, 1]), int(ok.sum())


def admit_floor(rem, n_excl, n_base):
    """Допуск фильтра (§13.68 п. 6): ≥ 20 сделок в месяце у клетки (01–13.11 — ≥ 8), исключено ≥ 30; подмножество (осталось < 50 % сделок базы) — ≥ 70 за окно и ≥ 8 в месяце. (подмножество?, допуск?)"""
    subset = bool(sum(rem) < 0.5 * n_base)
    if subset:
        ok = bool(sum(rem) >= MIN_N and all(r >= MIN_MONTH_SUB for r in rem))
    else:
        ok = all(rem[k] >= st.MIN_TRADES[k] for k in range(len(st.MONTH_LEN)))
    return subset, bool(ok and n_excl >= st.MIN_EXCL)


def load_tape(tape_dir, sym, start_abs_ms, ndays=2):
    """Лента монеты с суток `start_abs_ms` и `ndays − 1` следующих: (время мс, цена, дней найдено); zip дня — `<СИМВОЛ>-aggTrades-<день>.zip` (читаем два столбца, порядок — по времени, стабильно)."""
    import datetime
    import zipfile
    import pandas as pd
    d0 = datetime.datetime.fromtimestamp(start_abs_ms / 1000, datetime.timezone.utc).date()
    ts, ps, found = [], [], 0
    for k in range(ndays):
        d = d0 + datetime.timedelta(days=k)
        pth = os.path.join(tape_dir, "%s-aggTrades-%s.zip" % (sym, d.isoformat()))
        if not os.path.exists(pth):
            continue
        found += 1
        with zipfile.ZipFile(pth) as z:
            with z.open(z.namelist()[0]) as f:
                df = pd.read_csv(f, usecols=["price", "transact_time"], dtype={"price": "float64", "transact_time": "int64"})
        ts.append(df["transact_time"].to_numpy())
        ps.append(df["price"].to_numpy())
    if not ts:
        return np.zeros(0, dtype=np.int64), np.zeros(0), 0
    t, p = np.concatenate(ts), np.concatenate(ps)
    if len(t) > 1 and (np.diff(t) < 0).any():
        o = np.argsort(t, kind="stable")
        t, p = t[o], p[o]
    return t, p, found
