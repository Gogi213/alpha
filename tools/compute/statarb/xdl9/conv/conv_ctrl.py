#!/usr/bin/env python3
"""TK-493, П-38 r2 §7.6, §10.1-§10.2: контроли на уровне сделок (условные случайные входы, перенос минуты входа) и таблицы K1b/K1c.

Контроли считают сделки теми же правилами исполнения, что основной счёт (вход open[t+2] с отложенным заполнением до K_stale, выход open[u+2] или по последней цене,
издержки 12,6 bps, фандинг). Случайные/сдвинутые сделки независимы и могут перекрываться (нулевая модель для ожидания, не портфель).
Перевёрнутое направление и перестановка знака считаются по суточным таблицам (conv_agg.y_series(rev=True), perm_pwin).
"""
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import conv_lib as L        # noqa: E402

MIN_DAY = 1440


class TradeCalc(L.PairSim):
    """Расчёт одной сделки по заданным входу/длительности (для контролей); автомат не используется."""

    def calc(self, t, d, hold):
        """-> (day_of_f_out, net) либо None (сигнал не исполним: нет данных / срок заявки). hold = f_out - f_in в минутах."""
        ps, cfg, cp = self.ps, self.cfg, self.cp
        N = ps.N
        f = int(ps.nextV2[min(t + 1 + cfg.delay, N)])
        if f >= N or f - t > cp.kstale:
            return None
        fo_want = f + hold
        u = fo_want - 1 - cfg.delay
        fx = self._fill_exit(u)
        if fx is None:
            return None
        pA, pB, ffill, fo, mA, mB = fx
        oA, oB = float(ps.oA[f]), float(ps.oB[f])
        gross = 1e4 * d * ((pB / oB - 1.0) - (pA / oA - 1.0))
        base = dict(d=d, oA=oA, oB=oB)
        fund = self._funding(base, f, fo)
        return ps.day_of(fo), gross - L.FEE_RT + fund


def eligible_minutes(ft, theta, k, p, period0):
    """Минуты, где пороговое условие и Q1/Q3/Q4/Q6/Q7 выполнены (без условия взвода): основа условных случайных входов."""
    ps = ft.ps
    gent = L.gentry_array(ps.sig, theta, k)
    with np.errstate(invalid="ignore"):
        E = ft.q4 & (ps.g >= gent)
    R = E.copy()
    for j in range(1, p):
        R[j:] &= E[:-j] & (ps.sgn[j:] == ps.sgn[:-j])
        R[:j] = False
    R &= ft.qbase
    R[:period0] = False
    return np.flatnonzero(R)


def random_entries(ps, cp, cfg, combo, ft, n_trades, durations, period0, rng):
    """Условные случайные входы: n_trades входов равномерно среди подходящих минут, направление случайное, длительности - выбор с возвращением из durations.
    -> {сутки: Σ net} и число исполненных."""
    out = {}
    done = 0
    el = eligible_minutes(ft, combo.theta, combo.k, combo.p, period0)
    if len(el) == 0 or n_trades <= 0 or len(durations) == 0:
        return out, 0
    calc = TradeCalc(ps, cp, cfg, combo, ft, period0, True)
    ts = rng.choice(el, size=n_trades, replace=True)
    ds = rng.choice([-1.0, 1.0], size=n_trades)
    hs = rng.choice(np.asarray(durations), size=n_trades, replace=True)
    for t, d, h in zip(ts, ds, hs):
        r = calc.calc(int(t), float(d), int(h))
        if r is not None:
            out[r[0]] = out.get(r[0], 0.0) + r[1]
            done += 1
    return out, done


def shifted_entries(ps, cp, cfg, combo, ft, real_trades, delta, period0):
    """Перенос минуты входа на delta минут: сигнал в t+delta, если там выполнены пороговое условие и Q1..Q7; направление по знаку d_idio там; длительность та же.
    real_trades - кортежи TRADE_FIELDS реальных закрытых сделок пары. -> ({сутки: Σ net}, исполнено, пропущено)."""
    out = {}
    done = skipped = 0
    el = set(eligible_minutes(ft, combo.theta, combo.k, combo.p, period0).tolist())
    calc = TradeCalc(ps, cp, cfg, combo, ft, period0, True)
    for tr in real_trades:
        r = dict(zip(L.TRADE_FIELDS, tr))
        if r["mtm"]:
            continue
        t2 = int(r["t_sig"]) + delta
        if t2 not in el:
            skipped += 1
            continue
        res = calc.calc(t2, float(ps.sgn[t2]), int(r["hold"]))
        if res is None:
            skipped += 1
            continue
        out[res[0]] = out.get(res[0], 0.0) + res[1]
        done += 1
    return out, done, skipped


def k1c_power(gr, T=360, s_stop=2.0, k=3, p=1):
    """K1c: по ячейке и theta ожидаемое число сделок при +1 мин (k = 3, p = 1) на всём периоде формирования ДО выбора; помечает «мощность ниже A1» (< 20 сделок)."""
    from conv_grid import FI
    rows = {}
    for cell in sorted(set(gr.cells)):
        rr = gr.rows(cells=[cell])
        line = {}
        for th in (25.2, 40.0, 80.0, 160.0, 320.0):
            ci = gr.cindex.get((th, k, p, T, s_stop))
            line["theta_%s" % th] = int(gr.tab[ci][rr][:, :, FI["n"]].sum()) if ci is not None else None
        best = max((v for v in line.values() if v is not None), default=0)
        rows[cell] = dict(line, pairs=int(len(rr)), power_below_A1=bool(best < 20))
    return rows


def k1b_outside(world, d0, d1, theta=160.0, nb=240, L_=1440, limit=None):
    """K1b: пары с c1d2 = 0 (вне универсума C2) с минутами |dev| >= theta bps (dev - отклонение от медианы за прошлые сутки): число пар и эпизодов по вердикту R2'.
    Описательно: читает формирование через conv_guard (K0 обязателен); пары берутся из asset_pairs.csv как есть."""
    import csv
    import conv_guard as G
    import conv_world as W
    path = os.path.join(world.root, "asset_pairs.csv")
    rows = [r for r in csv.DictReader(open(path, encoding="utf-8")) if r.get("c1d2") == "0" and r.get("scope") == "между биржами" and r["big1"] == "крипта" and r["big1"] == r["big2"]]
    if limit:
        rows = rows[:limit]
    out = {}
    for r in rows:
        pa = os.path.join(world.root, "panels", r["ex1"], W.safe_name(r["sym1"]) + ".sap")
        pb = os.path.join(world.root, "panels", r["ex2"], W.safe_name(r["sym2"]) + ".sap")
        if not (os.path.isfile(pa) and os.path.isfile(pb)):
            continue
        _, cA = G.load_panel_range(pa, d0, d1, what="k1b")
        _, cB = G.load_panel_range(pb, d0, d1, what="k1b")
        s, valid = L.spread_series(cA, cB)
        b, sig, _ = L.basis_sigma(s, L_, 5, nb, 0.0)
        dev = s - b
        with np.errstate(invalid="ignore"):
            hit = np.isfinite(dev) & (np.abs(dev) >= theta)
        n_ep = int(((hit[1:]) & (~hit[:-1])).sum() + (1 if hit[:1].any() else 0))
        v = r.get("verdict", "?")
        a = out.setdefault(v, dict(pairs=0, pairs_with_episodes=0, episodes=0))
        a["pairs"] += 1
        a["pairs_with_episodes"] += int(n_ep > 0)
        a["episodes"] += n_ep
    return out
