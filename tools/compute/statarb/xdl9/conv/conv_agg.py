#!/usr/bin/env python3
"""TK-493, П-38 r2: суточные ряды y_d, выбор комбинации класса (§7.4), допуск клеток A1-A7 (§7.5), статус (§9.3), контроли (§7.6, §10), хвост и «каток» (§10.9).

Вход: каталог сетки conv_run grid (tab/act/pids/pairmeta). Статистика интервалов и статус - ТЕ ЖЕ функции, что у P-37: statarb/portfolio.py и cell_status.py.
Метрика: y_d = (Σ net закрытых сделок суток d + MTM открытых на конце периода в последние сутки) / число активных пар-суток; в ряд входят только сутки с >= 1 активной
парой (cell_status.build_series). Для каждого прогона печатаются n, блок b = ceil(n^(1/3)), n_eff = ceil(n/b), df; при n_eff < 3 интервал не строится.
"""
import hashlib
import importlib.util
import json
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import conv_grid as GR      # noqa: E402

FI = GR.FI
MIN_DAY = 1440
ALPHA = 0.10
GRID_TH = (25.2, 40.0, 80.0, 160.0, 320.0)
GRID_K = (3, 5, 8)
GRID_P = (1, 3)
GRID_T = (120, 360, 1440)
GRID_S = (2.0, None)


def _load_statarb():
    for base in (os.environ.get("C2_STATARB"), os.path.join(HERE, "..", ".."), os.path.expanduser("~/xdl9/tools/statarb")):
        if base and os.path.isfile(os.path.join(base, "portfolio.py")) and os.path.isfile(os.path.join(base, "cell_status.py")):
            mods = []
            for nm in ("portfolio", "cell_status"):
                spec = importlib.util.spec_from_file_location("c2_" + nm, os.path.join(base, nm + ".py"))
                m = importlib.util.module_from_spec(spec)
                sys.modules["c2_" + nm] = m
                spec.loader.exec_module(m)
                mods.append(m)
            return mods
    raise SystemExit("не найдены portfolio.py / cell_status.py (C2_STATARB)")


P, CS = _load_statarb()


# ------------------------------------------------------------------ загрузка
class Grid:
    def __init__(self, gdir, cls):
        d = os.path.join(gdir, cls)
        self.cls = cls
        self.tab = np.load(os.path.join(d, "tab.npy"))
        self.act = np.load(os.path.join(d, "act.npy"))
        self.pids = json.load(open(os.path.join(d, "pids.json")))
        pm = json.load(open(os.path.join(d, "pairmeta.json")))
        self.keys, self.cells, self.exch = pm["keys"], pm["cells"], pm["exch"]
        self.combos = [tuple(c) for c in json.load(open(os.path.join(gdir, "combos.json")))]
        self.meta = json.load(open(os.path.join(gdir, "meta.json")))
        self.cnt = json.load(open(os.path.join(d, "counters.json")))
        self.hist = np.load(os.path.join(d, "hist.npy"))
        self.d0, self.d1, self.first_sig = self.meta["d0"], self.meta["d1"], self.meta["first_sig_day"]
        self.nd = self.tab.shape[2] - 1
        self.cindex = {c: i for i, c in enumerate(self.combos)}
        self.asset_ids = {}
        for k in self.keys:
            self.asset_ids.setdefault(k, len(self.asset_ids))
        self.asset_of = np.array([self.asset_ids[k] for k in self.keys])

    def rows(self, cells=None, assets_excl=None, asset_half=None, without_exch=None):
        m = np.ones(len(self.pids), bool)
        if cells is not None:
            cs = set(cells)
            m &= np.array([c in cs for c in self.cells])
        if assets_excl is not None:
            ex = set(assets_excl)
            m &= np.array([k not in ex for k in self.keys])
        if asset_half is not None:
            m &= np.array([(hashlib.sha256(k.encode("utf-8")).digest()[0] % 2) == asset_half for k in self.keys])
        if without_exch is not None:
            m &= np.array([without_exch not in e for e in self.exch])
        return np.flatnonzero(m)


# ------------------------------------------------------------------ ряд y_d
def y_series(gr, ci, rows, *, days_from=None, field="net", count="n", cost=None, rev=False, maker=False, add_per_trade=0.0):
    """-> dict(build_series) для комбинации ci и строк пар. field: 'net' | 'net_ns' (без протухшей ноги). cost: массив издержек a_pair на сделку по строкам (A5).
    rev: перевёрнутое направление (-gross - 12,6 n - fund). maker: издержки 5,04 вместо 12,6."""
    first = days_from if days_from is not None else gr.first_sig
    t = gr.tab[ci][rows]                                 # [пары, сутки+1, поля]
    act = gr.act[ci][rows]
    if rev:
        num = -t[:, :, FI["gross"]] - 12.6 * t[:, :, FI["n"]] - t[:, :, FI["fund"]]
    else:
        num = t[:, :, FI["net_ns"]] if field == "net_ns" else t[:, :, FI["net"]]
        if maker:
            num = num + (12.6 - 5.04) * (t[:, :, FI["n_ns"]] if field == "net_ns" else t[:, :, FI["n"]])
    if cost is not None:
        cnt = t[:, :, FI["n_ns"]] if field == "net_ns" else t[:, :, FI["n"]]
        num = num - cost[:, None] * cnt
    if add_per_trade:
        num = num - add_per_trade * t[:, :, FI["n"]]
    nd = gr.nd
    days = list(range(gr.d0, gr.d0 + nd))
    day_net = {gr.d0 + j: float(num[:, j].sum()) for j in range(nd)}
    day_act = {gr.d0 + j: int(act[:, j].sum()) for j in range(nd)}
    sig_days = [d for d in days if d >= first]
    mtm = float(num[:, nd].sum())
    n_open = int(t[:, nd, FI["n"]].sum())
    return CS.build_series(day_net, day_act, sig_days, mtm_net_sum=mtm, n_open=n_open)


def interval(series, block=None):
    n = len(series)
    if n == 0:
        return None
    b = block or P.block_len(n)
    n_eff = math.ceil(n / b)
    info = dict(n=n, block=b, n_eff=n_eff, df=n_eff - 1)
    if n_eff < 3:
        return dict(info, mean=sum(series) / n, lo=None, hi=None)
    m, lo, hi, p, df = P.block_t_interval(list(series), ALPHA, b)
    return dict(info, mean=m, lo=lo, hi=hi, p=p)


def n_trades(gr, ci, rows, field="n"):
    return int(gr.tab[ci][rows][:, :, FI[field]].sum())


def n_days_with_trades(gr, ci, rows):
    t = gr.tab[ci][rows][:, :gr.nd, FI["n"]].sum(axis=0)
    first = gr.first_sig - gr.d0
    return int((t[first:] > 0).sum())


def n_assets_with_trades(gr, ci, rows):
    t = gr.tab[ci][rows][:, :, FI["n"]].sum(axis=1)
    return len({gr.keys[r] for r, x in zip(rows, t) if x > 0})


def asset_sums(gr, ci, rows):
    t = gr.tab[ci][rows][:, :, FI["net"]].sum(axis=1)
    out = {}
    for r, x in zip(rows, t):
        out[gr.keys[r]] = out.get(gr.keys[r], 0.0) + float(x)
    return out


# ------------------------------------------------------------------ выбор комбинации класса (§7.4)
def thresholds(days):
    """n_min, k_days по длине периода (дни): 20/5 на Pi_full (13 суток), 9/2 на Pi_train (6), 10/3 на проверке (7); общая формула ceil(30*дни/21), ceil(дни/3)."""
    return max(1, math.ceil(30 * days / 21)) if days != 13 else 20, max(1, math.ceil(days / 3))


def class_selection(gr, rows=None, days_n=None):
    """Комбинация с максимальной нижней границей (LCB) 90 %-интервала y_d среди допустимых; ничья - меньшие theta, k, p, T, s_stop.
    -> (индекс комбинации, таблица всех комбинаций - список dict)."""
    rows = np.arange(len(gr.pids)) if rows is None else rows
    ndays = days_n or (gr.d1 - gr.first_sig + 1)
    n_min, k_days = thresholds(ndays)
    table = []
    best, best_key = None, None
    for ci, c in enumerate(gr.combos):
        ser = y_series(gr, ci, rows)
        prim = ser["primary"]
        n = n_trades(gr, ci, rows)
        row = dict(ci=ci, theta=c[0], k=c[1], p=c[2], T=c[3], s_stop=c[4], trades=n, days=n_days_with_trades(gr, ci, rows), assets=n_assets_with_trades(gr, ci, rows),
                   mean=None, lo=None, hi=None, ok=False)
        if prim is not None and len(prim) > 0:
            iv = interval(prim)
            row.update(mean=iv["mean"], lo=iv["lo"], hi=iv["hi"], n_days=iv["n"], n_eff=iv["n_eff"], df=iv["df"], block=iv["block"])
            tn = gr.tab[ci][rows]
            row["mean_trade"] = float(tn[:, :, FI["net"]].sum() / max(tn[:, :, FI["n"]].sum(), 1))
            row["maker_mean_trade"] = row["mean_trade"] + 12.6 - 5.04
            row["share_le2"] = float(tn[:, :, FI["n_le2"]].sum() / max(tn[:, :, FI["n"]].sum(), 1))
            row["ok"] = (n >= n_min and row["days"] >= k_days and row["assets"] >= 5 and iv["lo"] is not None)
            if row["ok"]:
                key = (-iv["lo"], c[0], c[1], c[2], c[3], 0 if c[4] is not None else 1)
                if best_key is None or key < best_key:
                    best, best_key = ci, key
        table.append(row)
    return best, table


def neighbors(gr, ci):
    c = gr.combos[ci]
    th, k, T = GRID_TH, GRID_K, GRID_T
    out = []
    for ax, vals, pos in ((0, th, c[0]), (1, k, c[1]), (3, T, c[3])):
        i = vals.index(pos)
        for j in (i - 1, i + 1):
            if 0 <= j < len(vals):
                cc = list(c)
                cc[ax] = vals[j]
                out.append(gr.cindex[tuple(cc)])
    return out


def plateau(gr, ci, rows):
    """Все существующие соседи по theta, k, T (шаг +-1) имеют среднее y_d > 0."""
    res = []
    for nb in neighbors(gr, ci):
        ser = y_series(gr, nb, rows)["primary"]
        res.append(ser is not None and len(ser) > 0 and sum(ser) / len(ser) > 0)
    return all(res), len(res)


def step_distance(c1, c2):
    d = 0
    for ax, vals in ((0, GRID_TH), (1, GRID_K), (3, GRID_T)):
        d = max(d, abs(vals.index(c1[ax]) - vals.index(c2[ax])))
    return d


def class_formation(gr_full, gr_train, cls_name):
    """Условия формирования класса §7.4: выбор c_полн и c_обуч, плато, согласие, A3b (половины активов), A8 (без каждой биржи). -> dict с причинами."""
    out = dict(cls=cls_name, formed=False, reasons=[])
    ci_full, table = class_selection(gr_full)
    out["table"] = table
    out["distinct_behaviours"] = len({(round(r["trades"], 0), round(r.get("mean") or 0.0, 6)) for r in table})
    if ci_full is None:
        out["reasons"].append("нет комбинации с достаточным числом сделок, суток и активов")
        return out
    out["c_full"] = list(gr_full.combos[ci_full])
    ci_tr, _ = class_selection(gr_train, days_n=6)
    out["c_train"] = list(gr_train.combos[ci_tr]) if ci_tr is not None else None
    ok_plateau, nn = plateau(gr_full, ci_full, np.arange(len(gr_full.pids)))
    out["plateau"] = bool(ok_plateau)
    if not ok_plateau:
        out["reasons"].append("плато: у соседей c_полн среднее y_d <= 0")
    if ci_tr is None or step_distance(gr_full.combos[ci_full], gr_train.combos[ci_tr]) > 1:
        out["reasons"].append("согласие: c_обуч и c_полн отличаются больше чем на шаг")
        out["agree"] = False
    else:
        out["agree"] = True
    a3b = []
    for h in (0, 1):
        r_h = gr_full.rows(asset_half=h)
        r_o = gr_full.rows(asset_half=1 - h)
        ci_h, _ = class_selection(gr_full, r_h)
        if ci_h is None:
            a3b.append(False)
            continue
        ser = y_series(gr_full, ci_h, r_o)["primary"]
        a3b.append(bool(ser is not None and len(ser) > 0 and sum(ser) / len(ser) > 0))
    out["a3b"] = a3b
    if not all(a3b):
        out["reasons"].append("A3b: комбинация одной половины активов не даёт плюс на другой")
    exch = sorted({e for ex in gr_full.exch for e in ex})
    a8 = {}
    for e in exch:
        rr = gr_full.rows(without_exch=e)
        ser = y_series(gr_full, ci_full, rr)["primary"] if len(rr) else None
        a8[e] = bool(ser is not None and len(ser) > 0 and sum(ser) / len(ser) > 0)
    out["a8"] = a8
    if not all(a8.values()):
        out["reasons"].append("A8: без биржи(ей) %s среднее y_d <= 0" % ",".join(e for e, v in a8.items() if not v))
    out["formed"] = not out["reasons"]
    return out


# ------------------------------------------------------------------ допуск клетки (§7.5) и статус (§9.3)
def roll_cost(meta_by_pid, pids, key="roll_full", fallback=None):
    """Стоимость круга 4*mean(a_A, a_B) по паре (A5); нет оценки - медиана положительных оценок (fallback)."""
    out = np.zeros(len(pids))
    for i, p in enumerate(pids):
        m = meta_by_pid.get(p)
        a = [x for x in (m[key] if m else []) if x is not None]
        out[i] = 4.0 * (sum(a) / len(a)) if a else (4.0 * fallback if fallback else 0.0)
    return out


def admit_cell(gr_full, gr_train_valid, cell, ci_full, ci_train_fixed, meta_by_pid, fallback_a=None):
    """A1..A7 для клетки. gr_train_valid - прогон c_обуч с числами Pi_train на сутках 2472..2478 (ci_train_fixed - индекс c_обуч в нём)."""
    rows = gr_full.rows(cells=[cell])
    res = dict(cell=cell, pairs=int(len(rows)), A=[False] * 7)
    if len(rows) == 0:
        return res
    ser = y_series(gr_full, ci_full, rows)
    prim = ser["primary"]
    n = n_trades(gr_full, ci_full, rows)
    na = n_assets_with_trades(gr_full, ci_full, rows)
    nd = n_days_with_trades(gr_full, ci_full, rows)
    res.update(trades=n, assets=na, days=nd)
    A = res["A"]
    A[0] = n >= 20 and na >= 5 and nd >= 5
    if prim and len(prim) >= 1:
        iv = interval(prim)
        res["mean"], res["lo_t"], res["n"], res["n_eff"] = iv["mean"], iv["lo"], iv["n"], iv["n_eff"]
        lo_b = None
        if iv["lo"] is not None:
            _, lo_b, _ = P.block_boot_t_interval(list(prim), ALPHA, 3000, 37, iv["block"])
        res["lo_boot"] = lo_b
        A[1] = iv["lo"] is not None and iv["lo"] > 0 and lo_b is not None and lo_b > 0
    # A3: вложенный walk-forward
    if gr_train_valid is not None and ci_train_fixed is not None:
        rv = gr_train_valid.rows(cells=[cell])
        sv = y_series(gr_train_valid, ci_train_fixed, rv, days_from=2472)["primary"] if len(rv) else None
        tn = gr_train_valid.tab[ci_train_fixed][rv]
        first = 2472 - gr_train_valid.d0
        ntr = float(tn[:, first:gr_train_valid.nd, FI["n"]].sum())
        ndv = int((tn[:, first:gr_train_valid.nd, FI["n"]].sum(axis=0) > 0).sum())
        A[2] = bool(sv and sum(sv) / len(sv) > 0 and ntr >= 10 and ndv >= 3)
        res["a3_mean"] = (sum(sv) / len(sv)) if sv else None
    # A4: без лучшего и без трёх лучших активов
    sums = asset_sums(gr_full, ci_full, rows)
    order = sorted(sums, key=lambda k: (-sums[k], k))
    ok4 = True
    for kcut in (1, 3):
        rr = gr_full.rows(cells=[cell], assets_excl=order[:kcut])
        s4 = y_series(gr_full, ci_full, rr)["primary"] if len(rr) else None
        ok4 = ok4 and bool(s4 and sum(s4) / len(s4) > 0)
    A[3] = ok4
    # A5: проскальзывание a по Роллу
    cost = roll_cost(meta_by_pid, [gr_full.pids[r] for r in rows], "roll_full", fallback_a)
    s5 = y_series(gr_full, ci_full, rows, cost=cost)["primary"]
    A[4] = bool(s5 and sum(s5) / len(s5) > 0)
    # A6: >= 5 допущенных пар после Q5 - число пар с хотя бы одним активным днём в прогоне
    A[5] = int((gr_full.act[ci_full][rows].sum(axis=1) > 0).sum()) >= 5
    # A7: без сделок с протухшей ногой
    s7 = y_series(gr_full, ci_full, rows, field="net_ns")["primary"]
    A[6] = bool(s7 and sum(s7) / len(s7) > 0)
    tn = gr_full.tab[ci_full][rows]
    tot = float(tn[:, :, FI["net"]].sum())
    res["share_net_stale"] = None if tot == 0 else float(1.0 - tn[:, :, FI["net_ns"]].sum() / tot)
    res["admitted"] = all(A)
    return res


# ------------------------------------------------------------------ контроли
def perm_pwin(gr, cls_rows, n_perm=1000, seed=37):
    """K8 (в): перестановка знака сделки. Для каждой комбинации ряд y_d* = (Σ ±x_i - 12,6 n)/den с x_i = gross_i + fund_i; нормальное приближение по суткам с точной
    дисперсией Σ x_i^2 (поля sx2); выходы не пересчитываются. -> p_win = P(max LCB среди комбинаций >= наблюдённый максимум LCB)."""
    rng = np.random.default_rng(seed)
    nd = gr.nd
    first = gr.first_sig - gr.d0
    obs_best = -math.inf
    for ci in range(len(gr.combos)):
        s = y_series(gr, ci, cls_rows)["primary"]
        if s and len(s) >= 3:
            iv = interval(s)
            if iv["lo"] is not None:
                obs_best = max(obs_best, iv["lo"])
    if not math.isfinite(obs_best):
        return None
    t = gr.tab[:, cls_rows]                           # [комб, пары, сутки+1, поля]
    n = t[:, :, :, FI["n"]].sum(axis=1)               # [комб, сутки+1]
    var = t[:, :, :, FI["sx2"]].sum(axis=1)
    den = gr.act[:, cls_rows].sum(axis=1).astype(float)   # [комб, сутки]
    hit = 0
    for _ in range(n_perm):
        z = rng.standard_normal(var.shape)
        num = np.sqrt(var) * z - 12.6 * n
        best = -math.inf
        for ci in range(len(gr.combos)):
            ds = [j for j in range(first, nd) if den[ci, j] > 0]
            if len(ds) < 3:
                continue
            series = [float(num[ci, j] / den[ci, j]) for j in ds]
            m, lo, hi, p, df = P.block_t_interval(series, ALPHA, None)
            best = max(best, lo)
        if best >= obs_best:
            hit += 1
    return dict(p_win=(hit + 1) / (n_perm + 1), obs_best=obs_best)


def tail_metrics(gr, ci, rows):
    """Хвост сделок §10.9 по гистограмме net (шаг 2 bps): доля побед, отношение выигрыша к проигрышу, квантили 1/5/50 %, ожидаемый хвостовой убыток (худшие 5 %)."""
    t = gr.tab[ci][rows]
    n = float(t[:, :, FI["n"]].sum())
    if n == 0:
        return None
    net = float(t[:, :, FI["net"]].sum())
    nw = float(t[:, :, FI["n_win"]].sum())
    sw = float(t[:, :, FI["sum_win"]].sum())
    sl = net - sw
    nl = n - nw
    return dict(trades=n, win_rate=nw / n, win_loss_ratio=((sw / nw) / (-sl / nl)) if nw > 0 and nl > 0 and sl < 0 else None, mean=net / n)


def katok_bounds(table_rows):
    """Квартили 0,25/0,75 по комбинациям (K9): верхний квартиль доли побед и нижний квартиль отношения выигрыш/проигрыш."""
    wr = [r["win_rate"] for r in table_rows if r and r.get("win_rate") is not None]
    ratio = [r["win_loss_ratio"] for r in table_rows if r and r.get("win_loss_ratio") is not None]
    return dict(win_rate_q75=float(np.quantile(wr, 0.75)) if wr else None, ratio_q25=float(np.quantile(ratio, 0.25)) if ratio else None)


def is_katok(tm, bounds):
    return bool(tm and bounds["win_rate_q75"] is not None and tm["win_rate"] >= bounds["win_rate_q75"]
                and tm["win_loss_ratio"] is not None and tm["win_loss_ratio"] <= bounds["ratio_q25"])


def cell_status(gr, ci, cell, control_days, sigma_form, mean_wo_best, exchanges, r1_mean=None, mean_no_stale=None, katok_ok=True):
    """Статус клетки окна: cell_status.classify + дополнительные условия r2 (§9.3)."""
    rows = gr.rows(cells=[cell])
    ser = y_series(gr, ci, rows)
    tn = gr.tab[ci][rows]
    ntr = int(tn[:, :, FI["n"]].sum())
    nas = n_assets_with_trades(gr, ci, rows)
    prim = ser["primary"]
    res = CS.classify(prim, control_days, n_trades=ntr, n_pairs=nas, sigma_form=sigma_form, mean_without_best_coin=mean_wo_best, mtm_ok=ser["mtm_available"],
                      closed_days=ser["closed"], net_plus1=(sum(prim) / len(prim) if prim else None),
                      short_trade_share=(float(tn[:, :, FI["n_le2"]].sum() / ntr) if ntr else None), exchanges=exchanges)
    extra = []
    if res["status"] == CS.CANDIDATE:
        m = sum(prim) / len(prim)
        if r1_mean is not None and abs(m - r1_mean) > 0.5 * abs(m):
            extra.append("основной и r1-вариант различаются больше чем на половину основного")
        if mean_no_stale is None or mean_no_stale <= 0:
            extra.append("среднее без сделок с протухшей ногой <= 0")
        if not katok_ok:
            extra.append("метка «каток» без пройденного стресса «стоп x3» / «без стопа»")
        if extra:
            res["status"], res["label"] = CS.UNCLEAR, CS.LABEL_RU[CS.UNCLEAR]
            res["unmet"] = list(res.get("unmet", [])) + extra
    res["r2_extra_unmet"] = extra
    return res


# ------------------------------------------------------------------ CLI: select (K7) для класса
def _meta_by_pid(meta_dir, pids):
    out = {}
    for p in pids:
        fp = os.path.join(meta_dir, p + ".json")
        if os.path.isfile(fp):
            out[p] = json.load(open(fp, encoding="utf-8"))
    return out


def main(argv=None):
    import argparse
    import csv
    ap = argparse.ArgumentParser(description="K7: выбор комбинации класса, условия формирования, допуск клеток A1-A7, K1c.")
    ap.add_argument("cmd", choices=["select"])
    ap.add_argument("--full", required=True, help="каталог grid_<tag> конвейера Pi_full")
    ap.add_argument("--train", required=True, help="каталог grid_<tag> конвейера Pi_train (ряды до 2471)")
    ap.add_argument("--train-valid", default=None, help="каталог grid прогона c_обуч с числами Pi_train на сутках 2472..2478 (одна комбинация)")
    ap.add_argument("--meta", required=True, help="каталог meta пар (cache/L<L>/meta)")
    ap.add_argument("--cls", default="crypto")
    ap.add_argument("--out", required=True)
    ap.add_argument("--perm", type=int, default=0, help="число перестановок K8 (в); 0 - не считать")
    a = ap.parse_args(argv)
    import conv_ctrl as CT
    gf, gt = Grid(a.full, a.cls), Grid(a.train, a.cls)
    res = class_formation(gf, gt, a.cls)
    meta = _meta_by_pid(a.meta, gf.pids)
    pos = [x for m in meta.values() for x in (m.get("roll_full") or []) if x is not None]
    fb = float(np.median(pos)) if pos else None
    cells_out = []
    ci_full = gf.cindex[tuple(res["c_full"])] if res.get("c_full") else None
    gv = Grid(a.train_valid, a.cls) if a.train_valid else None
    ci_tv = 0 if gv is not None else None
    if ci_full is not None:
        for cell in sorted(set(gf.cells)):
            cells_out.append(admit_cell(gf, gv, cell, ci_full, ci_tv, meta, fb))
        res["cells_admitted"] = [c["cell"] for c in cells_out if c.get("admitted") and res["formed"]]
        if a.perm:
            res["p_win"] = perm_pwin(gf, np.arange(len(gf.pids)), a.perm)
        tm = [tail_metrics(gf, ci, np.arange(len(gf.pids))) for ci in range(len(gf.combos))]
        res["katok_bounds"] = katok_bounds(tm)
        res["tail_selected"] = tm[ci_full]
    res["k1c"] = {k: v for k, v in CT.k1c_power(gf).items()}
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "selection_%s.json" % a.cls), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1, sort_keys=True, default=lambda o: None if (isinstance(o, float) and not math.isfinite(o)) else (float(o) if isinstance(o, (np.floating, np.integer)) else str(o)))
    with open(os.path.join(a.out, "grid_%s.csv" % a.cls), "w", encoding="utf-8", newline="\n") as f:
        w = csv.writer(f, lineterminator="\n")
        cols = ["theta", "k", "p", "T", "s_stop", "trades", "days", "assets", "n_days", "block", "n_eff", "df", "mean", "lo", "hi", "mean_trade", "maker_mean_trade", "share_le2", "ok"]
        w.writerow(cols)
        for r in res["table"]:
            w.writerow([r.get(c) for c in cols])
    with open(os.path.join(a.out, "cells_%s.csv" % a.cls), "w", encoding="utf-8", newline="\n") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["cell", "pairs", "trades", "assets", "days", "mean", "lo_t", "lo_boot", "A1", "A2", "A3", "A4", "A5", "A6", "A7", "admitted"])
        for c in cells_out:
            w.writerow([c["cell"], c.get("pairs"), c.get("trades"), c.get("assets"), c.get("days"), c.get("mean"), c.get("lo_t"), c.get("lo_boot")] + [int(x) for x in c["A"]] + [int(bool(c.get("admitted")) and res["formed"])])
    print("класс %s: сформирован=%s, c_полн=%s, c_обуч=%s, причины=%s, клеток допущено=%d из %d" % (a.cls, res["formed"], res.get("c_full"), res.get("c_train"), res["reasons"],
                                                                                          len(res.get("cells_admitted", [])), len(cells_out)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
