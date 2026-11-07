#!/usr/bin/env python3
"""TK-493, П-38 r2 §7.4, §14: счёт сетки комбинаций по компонентам (пары с общими ногами) -> суточные таблицы пара x сутки и счётчики.

Одна задача = одна компонента (все пары актива, общие ноги -> дедупликация события ноги). Кандидаты входа считаются один раз на пару и (theta, k, p),
выходы - по комбинации (T, s_stop). Выход задачи (детерминирован, не зависит от числа потоков):
  tab  [n_combos, n_pairs, n_days + 1, NF]  float64 - суточные суммы (последний слой - MTM позиций, открытых на конце периода); поля DF
  act  [n_combos, n_pairs, n_days]          uint8   - сутки с активной парой (§6)
  cnt  по комбинации: Counter счётчиков (signal_total, drop_Q1.., dedup_dropped, entry_deferred, signal_expired, exit_*, jump_trades, dead_leg_end, pair_excluded)
  hist [n_combos, NH] - гистограмма net сделки (2 bps) для квантилей и хвоста; minnet [n_combos]
  trades (по запросу) - строки сделок для --trades-out
"""
import os
import sys
from collections import Counter

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import conv_guard as G      # noqa: E402
import conv_lib as L        # noqa: E402
import conv_world as W      # noqa: E402

MIN_DAY = 1440
DF = ["net", "n", "net_ns", "n_ns", "gross", "fund", "n_le2", "n_def", "net_def", "n_ff", "net_ff", "n_win", "sum_win", "sx2"]
FI = {k: i for i, k in enumerate(DF)}
NF = len(DF)
NH = 1002


def grid_combos(thetas=(25.2, 40.0, 80.0, 160.0, 320.0), ks=(3, 5, 8), ps=(1, 3), Ts=(120, 360, 1440), stops=(2.0, None)):
    return [L.Combo(th, k, p, T, s) for th in thetas for k in ks for p in ps for T in Ts for s in stops]


def hist_index(net):
    return int(min(max((net + 1000.0) // 2.0 + 1, 0), NH - 1))


def run_component(pairs, world, out, cfgd, cpd, combos, d0, d1, period0_day, q5, want_trades=False, lvar=None, want_turn=True):
    """pairs: список dict пар одной компоненты (один класс?). Класс может смешиваться? - нет: компонента строится внутри класса. cpd: {cls: ClassParams}."""
    cfg = L.Cfg(**cfgd)
    N = (d1 - d0 + 1) * MIN_DAY
    period0 = (period0_day - d0) * MIN_DAY
    nd = d1 - d0 + 1
    cells = {}
    pss = []
    for p in pairs:
        if p["cell"] not in cells:
            cells[p["cell"]] = W.load_cell_c(out, p["cell"], cfg.L)
        ps, meta = W.load_series(world, p, d0, d1, cfg, out, cells[p["cell"]][0], want_turn=want_turn)
        pss.append(ps)
    cp = cpd[pairs[0]["cls"]]
    feats = [L.Feats(ps, cp, cfg, period0) for ps in pss]
    nC, nP = len(combos), len(pairs)
    tab = np.zeros((nC, nP, nd + 1, NF))
    act = np.zeros((nC, nP, nd), np.uint8)
    cnts = [Counter() for _ in combos]
    hist = np.zeros((nC, NH), np.int64)
    minnet = np.full(nC, np.inf)
    trades = []
    candcnt = Counter()
    for ci, cb in enumerate(combos):
        adm = None
        if q5 is not None:
            adm = [bool(q5.get(p["pid"]) is not None and q5[p["pid"]] is not None and q5[p["pid"]] <= cb.T / 4.0) for p in pairs]
        sims = L.simulate_component(pss, cp, cfg, cb, feats, period0, adm)
        for i, sm in enumerate(sims):
            cnts[ci].update(sm.cnt)
            for dday in sm.active_days():
                if 0 <= dday - d0 < nd:
                    act[ci, i, dday - d0] = 1
            for tr in sm.trades:
                r = dict(zip(L.TRADE_FIELDS, tr))
                slot = nd if r["mtm"] else sm.ps.day_of(r["f_out"]) - d0
                a = tab[ci, i, slot]
                net = r["net"]
                a[FI["net"]] += net
                a[FI["n"]] += 1
                if not r["stale"]:
                    a[FI["net_ns"]] += net
                    a[FI["n_ns"]] += 1
                a[FI["gross"]] += r["gross"]
                a[FI["fund"]] += r["fund"]
                if r["hold"] <= 2:
                    a[FI["n_le2"]] += 1
                if r["deferred"]:
                    a[FI["n_def"]] += 1
                    a[FI["net_def"]] += net
                if r["ffill"]:
                    a[FI["n_ff"]] += 1
                    a[FI["net_ff"]] += net
                if net > 0:
                    a[FI["n_win"]] += 1
                    a[FI["sum_win"]] += net
                a[FI["sx2"]] += (r["gross"] + r["fund"]) ** 2
                hist[ci, hist_index(net)] += 1
                minnet[ci] = min(minnet[ci], net)
                if want_trades:
                    trades.append((ci, pairs[i]["pid"], pairs[i]["key"]) + tr)
        if ci == 0 or True:
            pass
    # счётчики отбора кандидатов (не зависят от пути): по (theta, k, p) на первой встреченной комбинации
    seen = set()
    for ci, cb in enumerate(combos):
        kk = (cb.theta, cb.k, cb.p)
        if kk in seen:
            continue
        seen.add(kk)
        for ft in feats:
            cnts[ci].update(ft.cand(*kk)[2])
    return dict(tab=tab, act=act, cnt=cnts, hist=hist, minnet=minnet, trades=trades, pids=[p["pid"] for p in pairs], keys=[p["key"] for p in pairs],
                cells=[p["cell"] for p in pairs], exch=[[p["exA"], p["exB"]] for p in pairs])
