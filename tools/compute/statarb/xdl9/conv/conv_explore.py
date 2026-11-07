#!/usr/bin/env python3
"""TK-493, П-39 (вид C2, схождения): режим ПОИСКА на 35 сутках 01.10–04.11.2026 (сутки 2465..2499), описательно, без статусов. НЕ подтверждение.

Оркестратор: строки params `v_*` (ОДИН вариант на строку) -> один проход по компонентам (пары с общими ногами): ряды пары загружаются ОДИН раз, затем по ним считаются все варианты
(группы по (база, потолок входа, θ, k) - кэши признаков освобождаются при смене группы). Автомат, выходы, издержки, фандинг - прежний `conv_lib` (поведение по умолчанию побайтно прежнее).
Новые правила вариантов (все ex-ante: данные ТОЛЬКО на минутах <= t сигнала):
  A: стоп Λ (gmax_stop) отдельно от потолка входа (gmax_entry), Λ = inf выключает S1; s0 (S0 = 2·g_in); потолок входа 640 bps / 55σ, A90 корзин >= 320 = A90 последней корзины с опорой;
  B (Q8): фильтр по известному фандингу: Ê(h) = 1e4·dir·(r̂_A·n_A(h) − r̂_B·n_B(h)), r̂ - последняя расчётная ставка <= t, интервал I - расстояние двух последних выплат (одна выплата - колонка interval_h),
      n(h) - число выплат по расписанию m_s + k·I на минутах [f+1, f+h-1], f = t + 1 + d; вход разрешён, если Ê(h) >= −φ·(1−ρ)·g_in;
  C (Q9): S_n(t) = |медиана s| за n суток до t (>= 240 валидных, пересчёт кратно 5, окно усечено) <= β(n, q) из addendum;
  D: задержка d ∈ {0, 1, 2, 5} на обеих ногах (Cfg.delay);
  E: без биржи X (подмножество пар), без шорта на X, терцили оборота (по предыдущим 1440 мин), выбросная нога S/L, «без выбросных ног» (r60 > γ(q) из addendum), Gate, класс eq.
Знаменатель y_d (P39-N02): активные пары-сутки базы на универсуме варианта: фильтры входа его не сужают; подмножества сужают по построению (режимы den: ref / own / ref_terc).
Защита: conv_xguard (сутки >= 2500 - отказ, код 3; журнал explore_reads.log; баннер в каждом выходном файле; запись только под ~/xdl9/c2x). conv_guard / conv_window не используются.
  python conv_explore.py prep  --univ main|gate|eq [--threads N]            боковые панели оборота + кэш рядов (K1-K3) в <out>/work
  python conv_explore.py run   --univ main|gate|eq --tag TAG [--ids a,b | --fam A,B | --stage 1|2] [--first-sig-day D] [--last-day D] [--threads N] [--limit-pairs N] [--strict-format]
"""
import argparse
import csv
import dataclasses
import gzip
import hashlib
import json
import math
import multiprocessing as mp
import os
import resource
import sys
import time
import types
from collections import Counter

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import conv_xguard as XG        # noqa: E402
import conv_lib as L            # noqa: E402
import conv_world as W          # noqa: E402
import conv_run as CR           # noqa: E402

W.G = XG            # conv_world читает панели/оборот/фандинг через защиту ПОИСКА (conv_guard остаётся нетронутым: чтение сутки >= 2479 без токена по-прежнему отказ)

MIN_DAY = 1440
D0 = XG.D_FORM0
D_LAST = XG.D_SEARCH_LAST
P0_DEFAULT = XG.D_SIG0
H1_LAST = 2478
H_Q90 = {"B1": 121, "B2": 69}                       # 0,9-квантиль длительности схождений V1 / V2 на 35 сутках (измерено 07.11, P-39 §5.4)
ENTRY_EXT = (640.0, 55.0)                           # потолок входа (bps) и σ-потолок при ecap=640 (P39-N04)
OFFS = (0, 5, 15, 30, 60, 120, 240)
EXTRA_FIELDS = ["terc", "ehat_T", "ehat_q", "sp0", "sp5", "sp15", "sp30", "sp60", "sp120", "sp240", "r60_short", "r60_long"]
COLS = list(L.TRADE_FIELDS) + EXTRA_FIELDS
NSTD = len(L.TRADE_FIELDS)
INT_FIELDS = {"t_sig", "f_in", "u_dec", "f_out", "code", "deferred", "ffill", "jump", "stale", "hold", "mtm", "terc"}
INT_IDX = {i for i, c in enumerate(COLS) if c in INT_FIELDS}
CALIB = "docs/research/P-38-calib-gate-2026-11-07/"
FROZEN = {                                          # замороженные числа П-38 r3 (классы, список Q5): файлы развёрнутого кода
    "main": {"B1": (CALIB + "classpar_full_nogate.json", CALIB + "q5_full_nogate.json"), "B2": (CALIB + "classpar_full_nogate_noq5.json", None)},
    "gate": {"B1": (CALIB + "classpar_full.json", CALIB + "q5_full.json"), "B2": (CALIB + "classpar_full_noq5.json", None)},
}
FROZEN["eq"] = {"B1": FROZEN["main"]["B1"], "B2": FROZEN["main"]["B1"]}          # класс eq: строки E-eq имеют q5=on - замороженные числа набора с Q5
PARAMS_REL = "docs/research/P-39-params-2026-11-07.csv"
PROTO_REL = "docs/research/P-39-convergence-explore-2026-11-07.md"
SUPPL_REL = "docs/research/P-39-params-suppl-2026-11-07.csv"      # добавочное семейство Cu (Инженер, до поиска)
CX = {}


# ------------------------------------------------------------------ варианты
def parse_variants(path):
    out = []
    with open(path, encoding="utf-8", newline="") as f:
        for r in csv.reader(f):
            if r and r[0].startswith("v_"):
                d = dict(kv.strip().split("=", 1) for kv in r[1].split(";"))
                out.append(make_variant(d))
    return out


def make_variant(d):
    v = dict(id=d["id"], fam=d["fam"], base=d["base"], th=float(d["th"]), k=int(d["k"]), p=int(d["p"]), T=int(d["T"]),
             s0=None if d["s0"] == "none" else float(d["s0"]), lam=d["lam"], ecap=d["ecap"], q5=(d["q5"] == "on"), delay=int(d["delay"]), cls=d["cls"], gate=d["gate"],
             stage=int(d["stage"]), ffilt=None, sgap=None, excl=None if d["excl_ex"] == "none" else d["excl_ex"], shortban=None if d["shortban"] == "none" else d["shortban"],
             terc=None if d["terc"] == "all" else int(d["terc"]), out=None)
    if d["ffilt"] != "none":
        kv = dict(x.split(":", 1) for x in d["ffilt"].split("/"))
        v["ffilt"] = (float(kv["phi"]), kv["h"])
    v["sgap_u"] = False
    if d["sgap"] != "none":
        kv = dict(x.split(":", 1) for x in d["sgap"].split("/"))
        v["sgap"] = (int(kv["n"]), float(kv["q"]))
        v["sgap_u"] = kv.get("u", "0") == "1"                # u:1 - S_n после снятия сдвига единиц кратного ln10 (добавлено Инженером сверх протокола, семейство Cu)
    if d["out"] != "none":
        v["out"] = ("OX", float(d["out"].split(":")[1])) if d["out"].startswith("OX") else (d["out"],)
    v["univ"] = "eq" if v["cls"] == "eq" else ("gate" if v["gate"] != "no" else "main")
    return v


def group_key(v):
    return (v["base"], 0 if v["ecap"] == "base" else 1, v["th"], v["k"])


def sort_key(v):
    return group_key(v) + (v["p"], 0 if v["fam"] == "REF" else 1, v["id"])


def den_mode(v):
    """own - свой прогон (универсум или θ/k/p иной); ref - активные пары-сутки базы; ref_terc - база, суженная терцилем."""
    if v["excl"] or v["univ"] != "main" or (v["th"], v["k"], v["p"]) != (40.0, 3, 3):
        return "own"
    return "ref_terc" if v["terc"] else "ref"


def read_calib(path):
    """Addendum П-39: calib_beta_sgap = JSON {"n|q": β}, calib_gamma_outlier = JSON {"q": γ}."""
    out = dict(beta={}, beta_u={}, gamma={})
    if not path or not os.path.isfile(path):
        return out
    for r in csv.reader(open(path, encoding="utf-8", newline="")):
        if len(r) > 1 and r[0] == "calib_beta_sgap" and r[1].strip():
            out["beta"] = {tuple(float(x) for x in k.split("|")): float(val) for k, val in json.loads(r[1]).items()}
        if len(r) > 1 and r[0] == "calib_beta_sgap_unit" and r[1].strip():
            out["beta_u"] = {tuple(float(x) for x in k.split("|")): float(val) for k, val in json.loads(r[1]).items()}
        if len(r) > 1 and r[0] == "calib_gamma_outlier" and r[1].strip():
            out["gamma"] = {float(k): float(val) for k, val in json.loads(r[1]).items()}
    return out


# ------------------------------------------------------------------ чистые функции правил (проверяются тестами G-X-2, G-X-3)
def fund_ex_arrays(ts_min, rates, ih, N):
    """Ex-ante состояние фандинга ноги на каждой минуте t: (mlast, rhat, I). mlast - минута последней выплаты <= t (-1 нет), rhat - её ставка,
    I - интервал в минутах: расстояние двух последних выплат <= t; если выплата одна - 60·interval_h её строки; иначе nan (нога «неизвестна»). Данные после t не используются."""
    t = np.arange(N)
    mlast = np.full(N, -1, np.int64)
    rhat = np.zeros(N)
    I = np.full(N, np.nan)
    if len(ts_min):
        idx = np.searchsorted(ts_min, t, side="right") - 1
        has = idx >= 0
        mlast = np.where(has, ts_min[np.maximum(idx, 0)], -1).astype(np.int64)
        rhat = np.where(has, rates[np.maximum(idx, 0)], 0.0)
        two = idx >= 1
        if two.any():
            I[two] = (ts_min[idx[two]] - ts_min[idx[two] - 1]).astype(np.float64)
        one = idx == 0
        if one.any() and np.isfinite(ih[0]):
            I[one] = 60.0 * ih[0]
        I[~(I > 0)] = np.nan
    return mlast, rhat, I


def n_payments(mlast, I, f, h):
    """Число выплат ноги по расписанию mlast + k·I (k >= 1) на минутах [f+1, f+h-1]; -> (n, известна)."""
    ok = (mlast >= 0) & np.isfinite(I) & (I > 0)
    with np.errstate(invalid="ignore", divide="ignore"):
        a = np.floor((f + h - 1 - mlast) / I)
        b = np.maximum(np.floor((f - mlast) / I), 0.0)           # f > t >= mlast всегда; max(.,0) - определение «k >= 1» при тестовых состояниях mlast > f
    n = np.where(ok, np.maximum(a - b, 0.0), 0.0)
    return n, ok


def ehat_array(sgn, fa, fb, t_idx, h, delay):
    """Ê(h) в bps на минутах t_idx: 1e4·dir·(r̂_A·n_A − r̂_B·n_B); -> (Ê, обе ноги известны)."""
    f = t_idx + 1 + delay
    nA, okA = n_payments(fa[0][t_idx], fa[2][t_idx], f, h)
    nB, okB = n_payments(fb[0][t_idx], fb[2][t_idx], f, h)
    return 1e4 * sgn[t_idx] * (fa[1][t_idx] * nA - fb[1][t_idx] * nB), (okA & okB)


UNIT_BPS = math.log(10.0) * 1e4         # сдвиг спреда у пар с ногами в разных единицах (контракт x10^k): ln(10^k)·1e4 bps - постоянная масштаба, не подбираемое число


def sn_at(s, t5, L_, nmin=240, unit_fix=False):
    """|медиана s| по валидным минутам [max(t5-L, 0), t5); nan, если < nmin наблюдений. unit_fix: перед модулем снимается ближайшее кратное ln(10)·1e4 (пары 1000PEPE/PEPE и т. п.: сдвиг единиц - не структурный разрыв)."""
    w = s[max(t5 - L_, 0):t5]
    w = w[np.isfinite(w)]
    if len(w) < nmin:
        return float("nan")
    m = float(np.median(w))
    if unit_fix:
        m -= round(m / UNIT_BPS) * UNIT_BPS
    return abs(m)


def r60_bps(c, lc):
    """r_leg = 1e4·ln(close_t / последний известный close на t-60) (bps); nan, где не определено. Только данные <= t."""
    r = np.full(len(c), np.nan)
    if len(c) > 60:
        with np.errstate(invalid="ignore", divide="ignore"):
            r[60:] = 1e4 * np.log(c[60:] / lc[:-60])
    return r


class PairCtx:
    """Ex-ante контекст одной пары: фандинг ног, r60, S_n по запросу, терцили по суткам."""

    def __init__(self, p, ps, fa, fb, terc_days):
        self.p, self.ps, self.fa, self.fb, self.terc = p, ps, fa, fb, terc_days
        self._r = None
        self._sn = {}

    def r60(self):
        if self._r is None:
            ps = self.ps
            self._r = (r60_bps(ps.cA, ps.lcA), r60_bps(ps.cB, ps.lcB))
        return self._r

    def short_leg_is_a(self):
        return self.ps.sgn > 0

    def sgap_allowed(self, Cb, n, beta, unit_fix=False):
        idx = np.flatnonzero(Cb)
        ok = np.ones(self.ps.N, bool)
        if not len(idx):
            return ok, 0
        t5 = idx - idx % 5
        cache = self._sn.setdefault((n, unit_fix), {})
        for t in np.unique(t5):
            if int(t) not in cache:
                cache[int(t)] = sn_at(self.ps.s, int(t), n * MIN_DAY, unit_fix=unit_fix)
        vals = np.array([cache[int(t)] for t in t5])
        with np.errstate(invalid="ignore"):
            ok[idx] = vals <= beta
        return ok, int(np.isnan(vals).sum())

    def ehat_full(self, h, delay):
        t = np.arange(self.ps.N)
        return ehat_array(self.ps.sgn, self.fa, self.fb, t, h, delay)

    def terc_minutes(self, k):
        return np.repeat(self.terc, MIN_DAY)[:self.ps.N] == k


def build_extra(v, pc, Cb, cfg, cx, cnt):
    """Маска кандидатов варианта (AND всех фильтров) или None. cnt - счётчики отсечённых кандидатов-минут (каждый фильтр независимо от остальных)."""
    ps = pc.ps
    mask = None
    p0 = cx["period0"]

    def comb(m, name, undef=0, undef_name=None):
        nonlocal mask
        cnt[name] += int(np.count_nonzero((Cb & ~m)[p0:]))
        if undef_name:
            cnt[undef_name] += undef
        mask = m if mask is None else (mask & m)
    if v["ffilt"] is not None:
        phi, hsel = v["ffilt"]
        h = v["T"] if hsel == "T" else H_Q90[v["base"]]
        E, known = pc.ehat_full(h, cfg.delay)
        with np.errstate(invalid="ignore"):
            m = E >= -phi * (1.0 - cfg.rho) * ps.g
        cnt["fund_unknown"] += int(np.count_nonzero((Cb & ~known)[p0:]))
        comb(m, "drop_Q8")
    if v["sgap"] is not None:
        n, q = v["sgap"]
        beta = cx["calib"]["beta_u" if v["sgap_u"] else "beta"][(float(n), float(q))]
        m, nund = pc.sgap_allowed(Cb, n, beta, v["sgap_u"])
        comb(m, "drop_Q9", nund, "sgap_undef")
    if v["shortban"] is not None:
        ex = v["shortban"]
        m = np.ones(ps.N, bool)
        if ps.exA == ex:
            m &= ~(ps.sgn > 0)
        if ps.exB == ex:
            m &= ~(ps.sgn < 0)
        comb(m, "drop_SB")
    if v["terc"] is not None:
        comb(pc.terc_minutes(v["terc"]), "drop_terc")
    if v["out"] is not None:
        rA, rB = pc.r60()
        aA, aB = np.abs(rA), np.abs(rB)
        if v["out"][0] in ("S", "L"):
            with np.errstate(invalid="ignore"):
                a_out = aA > aB
                b_out = aB > aA
            defined = a_out | b_out
            short_a = ps.sgn > 0
            short_out = (a_out & short_a) | (b_out & ~short_a)
            m = (short_out if v["out"][0] == "S" else ~short_out) & defined
            cnt["out_undef"] += int(np.count_nonzero((Cb & ~defined)[p0:]))
            comb(m, "drop_O")
        else:
            gamma = cx["calib"]["gamma"][float(v["out"][1])]
            rmax = np.fmax(aA, aB)
            with np.errstate(invalid="ignore"):
                m = ~(rmax > gamma)
            comb(m, "drop_OX")
    return mask


def cps_for(v, base_cp):
    """-> (cp входа - для Feats, cp автомата - для PairSim)."""
    cp_ent = base_cp
    if v["ecap"] == "640":
        vals = np.array(base_cp.a90_vals, float)
        fin = np.flatnonzero(np.isfinite(vals))
        if len(fin):
            last = fin[-1]
            vals[last + 1:] = vals[last]
        cp_ent = dataclasses.replace(base_cp, gmax_entry=ENTRY_EXT[0], gsig_max=ENTRY_EXT[1], a90_vals=vals)
    lam = v["lam"]
    if lam == "class":
        gs = None
    elif lam == "inf":
        gs = math.inf
    else:
        gs = float(lam)
        if lam == "320" and abs(gs - base_cp.gmax) > 1e-9:
            raise SystemExit("variant %s: lam=320, а gmax класса %s" % (v["id"], base_cp.gmax))
    return cp_ent, dataclasses.replace(cp_ent, gmax_stop=gs)


# ------------------------------------------------------------------ загрузка пары, фандинг
def fund_leg(world, p, side, d0, d1):
    ex, sym = p["legs"][side]
    ts, rs, ih = XG.load_funding_full(world.fund_path(ex, sym), d0, d1)
    N = (d1 - d0 + 1) * MIN_DAY
    return fund_ex_arrays(ts - d0 * MIN_DAY, rs, ih, N)


def trade_extras(pc, rows, v, delay, nd):
    """Столбцы EXTRA_FIELDS по сделкам пары (rows: float64 [n, NSTD])."""
    ps = pc.ps
    n = len(rows)
    t = rows[:, 0].astype(np.int64)
    f = rows[:, 1].astype(np.int64)
    d = rows[:, 4]
    out = np.full((n, len(EXTRA_FIELDS)), np.nan)
    day = np.minimum(t // MIN_DAY, nd - 1)
    out[:, 0] = pc.terc[day] if pc.terc is not None else 0
    E1, _ = ehat_array(ps.sgn, pc.fa, pc.fb, t, v["T"], delay)
    E2, _ = ehat_array(ps.sgn, pc.fa, pc.fb, t, H_Q90[v["base"]], delay)
    out[:, 1], out[:, 2] = E1, E2
    b_in = ps.b[t] + ps.c[t]
    for j, k in enumerate(OFFS):
        ix = f + k
        okk = ix < ps.N
        out[:, 3 + j] = np.where(okk, d * (ps.s[np.minimum(ix, ps.N - 1)] - b_in), np.nan)
    rA, rB = pc.r60()
    sa = d > 0
    out[:, 10] = np.where(sa, rA[t], rB[t])
    out[:, 11] = np.where(sa, rB[t], rA[t])
    return out


# ------------------------------------------------------------------ задача: одна компонента, все варианты
def explore_task(arg):
    ci, pairs = arg
    cx = CX
    t_start = time.time()
    d0, d1 = cx["d0"], cx["d1"]
    nd = d1 - d0 + 1
    period0 = cx["period0"]
    cfg0 = L.Cfg(**cx["cfgd"])
    world = cx["world"]
    cells = {}
    pss, pcs = [], []
    for p in pairs:
        if p["cell"] not in cells:
            cells[p["cell"]] = W.load_cell_c(cx["work"], p["cell"], cfg0.L)
        ps, _ = W.load_series(world, p, d0, d1, cfg0, cx["work"], cells[p["cell"]][0], want_turn=True, want_fund=True)
        pss.append(ps)
        pcs.append(PairCtx(p, ps, fund_leg(world, p, 0, d0, d1), fund_leg(world, p, 1, d0, d1), cx["terc"].get(p["pid"], np.zeros(nd, np.int8))))
    res = {}
    act_ref = {}
    feats_cache = {}
    cur_group = None
    for v in cx["variants"]:
        g = group_key(v)
        if g != cur_group:
            feats_cache.clear()
            cur_group = g
        cpd = cx["cpd"][v["base"]]
        cp_ent, cp_sim = cps_for(v, cpd[v["cls"]])
        cfg_v = dataclasses.replace(cfg0, delay=v["delay"])
        combo = L.Combo(v["th"], v["k"], v["p"], v["T"], v["s0"])
        sel = [i for i, p in enumerate(pairs) if not (v["excl"] and v["excl"] in (p["exA"], p["exB"]))]
        key = (v["base"], v["ecap"])
        fts = []
        for i in sel:
            ft = feats_cache.get((i,) + key)
            if ft is None:
                ft = feats_cache[(i,) + key] = L.Feats(pss[i], cp_ent, cfg0, period0)
            fts.append(ft)
        cnt = Counter()
        extras = []
        for i, ft in zip(sel, fts):
            Cb = ft.cand(v["th"], v["k"], v["p"])[0]
            extras.append(build_extra(v, pcs[i], Cb, cfg_v, cx, cnt))
        q5 = cx["q5"][v["base"]] if v["q5"] else None
        adm = None if q5 is None else [bool(q5.get(pairs[i]["pid"]) is not None and q5[pairs[i]["pid"]] <= v["T"] / 4.0) for i in sel]
        sims = L.simulate_component([pss[i] for i in sel], cp_sim, cfg_v, combo, fts, period0, adm, extras if any(e is not None for e in extras) else None)
        act = np.zeros((len(pairs), nd), np.uint8)
        rows_l, pidx_l = [], []
        for j, (i, sm) in enumerate(zip(sel, sims)):
            cnt.update(sm.cnt)
            for dd in sm.active_days():
                if 0 <= dd - d0 < nd:
                    act[i, dd - d0] = 1
            if sm.trades:
                r = np.array(sm.trades, dtype=np.float64)
                rows_l.append(np.hstack([r, trade_extras(pcs[i], r, v, cfg_v.delay, nd)]))
                pidx_l.append(np.full(len(r), i, np.int32))
        for i, ft in zip(sel, fts):
            cnt.update(ft.cand(v["th"], v["k"], v["p"])[2])
        mode = den_mode(v)
        if v["fam"] == "REF":
            act_ref[v["base"]] = act.copy()
        if mode == "ref" or mode == "ref_terc":
            if v["base"] not in act_ref:
                raise SystemExit("нет REF-%s для знаменателя варианта %s (REF считается раньше)" % (v["base"], v["id"]))
            act_v = act_ref[v["base"]].copy()
            if mode == "ref_terc":
                tm = np.stack([np.asarray(pc.terc) for pc in pcs]) == v["terc"]
                act_v = act_v * tm.astype(np.uint8)
        else:
            act_v = act
        res[v["id"]] = dict(rows=np.vstack(rows_l) if rows_l else np.zeros((0, len(COLS))), pidx=np.concatenate(pidx_l) if pidx_l else np.zeros(0, np.int32), act=act_v, cnt=dict(cnt))
    return ci, res, dict(sec=round(time.time() - t_start, 2), pairs=len(pairs), maxrss_mb=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024)


# ------------------------------------------------------------------ подготовка: оборот, ряды, терцили
def make_world(root, univ, turn_dir, test=False):
    return W.World(root, test=test, turn_dir=turn_dir, gate=(univ == "gate"))


def select_pairs(world, univ, limit):
    cls = "eq" if univ == "eq" else "crypto"
    return CR.select_pairs(world, types.SimpleNamespace(test_world=False, classes=cls, pairs_file=None, limit_pairs=limit or 0))


def pool_map(fn, tasks, threads):
    if threads <= 1:
        for t in tasks:
            yield fn(t)
        return
    ctx = mp.get_context("fork")
    with ctx.Pool(threads) as pool:
        for r in pool.imap(fn, tasks, chunksize=1):
            yield r


def _turn_task(args):
    src, dst, ex, mult, d0, d1 = args
    a = XG.load_turnover_raw(src, d0, d1, exchange=ex, mult=mult)
    np.save(dst, a.astype(np.float32))
    return 1


def build_turnover_parallel(world, pairs, d0, d1, raw_root, turn_dir, threads):
    seen, tasks = set(), []
    for p in pairs:
        for ex, sym in p["legs"]:
            if (ex, sym) in seen:
                continue
            seen.add((ex, sym))
            src = os.path.join(raw_root, ex, "candles", W.safe_name(sym) + ".csv.gz")
            if not os.path.isfile(src):
                continue
            os.makedirs(os.path.join(turn_dir, ex), exist_ok=True)
            tasks.append((src, os.path.join(turn_dir, ex, W.safe_name(sym) + ".npy"), ex, world.gate_mult() if ex == "gate" else 1.0, d0, d1))
    return sum(pool_map(_turn_task, tasks, threads))


def build_terciles(world, pairs, d0, d1):
    """Терцили оборота пары по суткам d (d0+1..d1): оборот пары = min по ногам медианы минутного оборота за предыдущие 1440 минут; границы 1/3 и 2/3 - по всем парам на 00:00 UTC
    суток d; терцил 1 - v <= q1, 2 - v <= q2, иначе 3 (0 - нет данных). -> {pid: int8 [nd]}."""
    nd = d1 - d0 + 1
    legmed = {}
    for p in pairs:
        for ex, sym in p["legs"]:
            if (ex, sym) in legmed:
                continue
            a = world.load_turn(p, 0 if p["legs"][0] == (ex, sym) else 1, d0, d1)
            if a is None:
                legmed[(ex, sym)] = np.full(nd, np.nan)
                continue
            m = np.full(nd, np.nan)
            for dd in range(1, nd):
                m[dd] = float(np.median(a[(dd - 1) * MIN_DAY:dd * MIN_DAY]))
            legmed[(ex, sym)] = m
    val = np.stack([np.fmin(legmed[p["legs"][0]], legmed[p["legs"][1]]) for p in pairs]) if pairs else np.zeros((0, nd))
    terc = np.zeros((len(pairs), nd), np.int8)
    for dd in range(1, nd):
        col = val[:, dd]
        ok = np.isfinite(col)
        if ok.sum() < 3:
            continue
        q1, q2 = np.quantile(col[ok], [1 / 3, 2 / 3])
        terc[ok, dd] = np.where(col[ok] <= q1, 1, np.where(col[ok] <= q2, 2, 3))
    return {p["pid"]: terc[i] for i, p in enumerate(pairs)}


def _series_cfg():
    return CR.make_cfg(types.SimpleNamespace(L=1440, nb=240, no_dedup=False, delay=1, entry_mode="defer", exit_mode="ffill", no_ceiling=False, no_q6=False))


def cmd_prep(a):
    XG.configure(script_path=os.path.abspath(__file__), out_root=a.out)
    work, turn = os.path.join(a.out, "work"), os.path.join(a.out, "turn")
    XG.log_event("prep_start", univ=a.univ, d0=D0, d1=D_LAST, threads=a.threads)
    t0 = time.time()
    world = make_world(a.root, a.univ, turn)
    pairs = select_pairs(world, a.univ, a.limit_pairs)
    print("[%6.1f] пар %d, универсум %s" % (time.time() - t0, len(pairs), a.univ), flush=True)
    n = build_turnover_parallel(world, pairs, D0, D_LAST, a.raw_root, turn, a.threads)
    print("[%6.1f] боковых панелей оборота: %d" % (time.time() - t0, n), flush=True)
    CR._CTX.update(world=world, cfgd=_series_cfg(), out=work, d0=D0, d1=D_LAST)
    cells = {}
    for p in pairs:
        cells.setdefault(p["cell"], []).append(p)
    todo = sorted(cells.items())
    if a.univ != "main":                                  # ячейки, уже посчитанные основным набором, повторно не строятся (ряды пары не зависят от универсума)
        cdir = W.cache_dirs(work, 1440)[1]
        todo = [(c, ps) for c, ps in todo if not os.path.isfile(os.path.join(cdir, W.safe(c) + ".npy"))]
    done = 0
    for _ in pool_map(CR._series_task, todo, a.threads):
        done += 1
        if done % 100 == 0:
            print("[%6.1f] ячеек %d/%d" % (time.time() - t0, done, len(todo)), flush=True)
    print("[%6.1f] ряды s, b, sigma, r6, c готовы: ячеек %d" % (time.time() - t0, len(todo)), flush=True)
    XG.log_max_read("prep_end")


# ------------------------------------------------------------------ запись выходов
def fmt_row(row, ci, pid, key):
    parts = [str(ci), pid, key]
    for i, x in enumerate(row):
        parts.append(str(int(x)) if i in INT_IDX else "%.9g" % x)
    return ",".join(parts)


def write_trades_gz(path, ncols, rows, pidx, pairs):
    XG.check_write(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    import io
    raw = open(path, "wb")
    f = io.TextIOWrapper(gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0, compresslevel=6), encoding="utf-8", newline="\n")
    try:
        f.write(XG.banner_csv())
        f.write("combo,pid,key," + ",".join(COLS[:ncols]) + "\n")
        for r, i in zip(rows, pidx):
            p = pairs[i]
            f.write(fmt_row(r[:ncols], 0, p["pid"], p["key"]) + "\n")
    finally:
        f.close()
        raw.close()


def write_trades_strict(path, rows, pidx, pairs):
    """Формат trades.csv раннера окна (conv_window.write_grid) БЕЗ баннера: только для сверки G-X-1 по sha256."""
    XG.check_write(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline=chr(10)) as f:
        f.write("combo,pid,key," + ",".join(L.TRADE_FIELDS) + chr(10))
        for r, i in zip(rows, pidx):
            p = pairs[i]
            f.write(fmt_row(r[:NSTD], 0, p["pid"], p["key"]) + chr(10))


def load_frozen(code_root, univ):
    cpd, q5 = {}, {}
    for base, (cp_rel, q5_rel) in FROZEN[univ].items():
        cpd[base] = CR.class_params(os.path.join(code_root, cp_rel))
        q5[base] = json.load(open(os.path.join(code_root, q5_rel), encoding="utf-8")) if q5_rel else None
    return cpd, q5


def cmd_run(a):
    out_root = os.path.abspath(a.out)
    XG.configure(script_path=os.path.abspath(__file__), out_root=out_root)
    code_root = os.path.abspath(a.code_root)
    params_path = os.path.join(code_root, PARAMS_REL)
    suppl = os.path.join(code_root, SUPPL_REL)
    commit, shas = XG.ensure_protocol_frozen(code_root, [PARAMS_REL, PROTO_REL] + ([SUPPL_REL] if os.path.isfile(suppl) else []) + ([a.addendum_rel] if a.addendum_rel else []))
    variants = [v for v in parse_variants(params_path) if v["univ"] == a.univ]
    if os.path.isfile(suppl) and not a.no_suppl:
        variants += [v for v in parse_variants(suppl) if v["univ"] == a.univ]
    if a.extra_params:
        variants += [v for v in parse_variants(a.extra_params) if v["univ"] == a.univ]
    if a.ids:
        want = set(a.ids.split(","))
        variants = [v for v in variants if v["id"] in want or v["fam"] == "REF"]
    if a.fam:
        want = set(a.fam.split(","))
        variants = [v for v in variants if v["fam"] in want or v["fam"] == "REF"]
    if a.stage:
        variants = [v for v in variants if v["stage"] == int(a.stage) or v["fam"] == "REF"]
    variants = [v for v in variants if v["gate"] != "only"]          # gate-only - срез прогона gate-plus (пары с ногой Gate), отдельным прогоном не считается
    if len({v["id"] for v in variants}) != len(variants):
        raise SystemExit("повтор id вариантов")
    variants.sort(key=sort_key)
    d0, d1, p0 = D0, a.last_day, a.first_sig_day
    if d1 > D_LAST:
        raise XG.Refused("last_day %d > %d" % (d1, D_LAST))
    XG.log_event("run_start", tag=a.tag, univ=a.univ, d0=d0, d1=d1, first_sig_day=p0, variants=len(variants), threads=a.threads, commit=commit, files=shas)
    work, turn = os.path.join(out_root, "work"), os.path.join(out_root, "turn")
    world = make_world(a.root, a.univ, turn)
    pairs = select_pairs(world, a.univ, a.limit_pairs)
    comps = W.components(pairs)
    all_pairs = [p for c in comps for p in c]
    cpd, q5 = load_frozen(code_root, a.univ)
    calib = read_calib(os.path.join(code_root, a.addendum_rel)) if a.addendum_rel else dict(beta={}, gamma={})
    nd = d1 - d0 + 1
    t0 = time.time()
    terc = build_terciles(world, all_pairs, d0, d1)
    print("[%6.1f] пар %d, компонент %d, вариантов %d, терцили готовы" % (time.time() - t0, len(all_pairs), len(comps), len(variants)), flush=True)
    CX.update(world=world, cfgd=_series_cfg(), work=work, d0=d0, d1=d1, period0=(p0 - d0) * MIN_DAY, variants=variants, cpd=cpd, q5=q5, calib=calib, terc=terc)
    N_by = {v["id"]: [] for v in variants}
    metas = []
    offs = [0]
    for c in comps:
        offs.append(offs[-1] + len(c))
    results = {}
    for n_done, (ci, res, meta) in enumerate(pool_map(explore_task, list(enumerate(comps)), a.threads), 1):
        results[ci] = res
        metas.append(dict(meta, comp=ci))
        if n_done % 50 == 0 or n_done == len(comps):
            print("[%6.1f] компонент %d/%d, maxrss %d МБ" % (time.time() - t0, n_done, len(comps), max(m["maxrss_mb"] for m in metas)), flush=True)
    # сборка по вариантам
    arr_dir = os.path.join(work, "arrays", a.tag)
    os.makedirs(arr_dir, exist_ok=True)
    pair_meta = [[p["pid"], p["key"], p["exA"], p["exB"], p["cell"], p["cls"]] for p in all_pairs]
    json.dump(pair_meta, open(os.path.join(arr_dir, "pairs.json"), "w"))
    counters = {}
    summary = []
    for v in variants:
        rows = [results[ci][v["id"]]["rows"] for ci in range(len(comps))]
        pidx = [results[ci][v["id"]]["pidx"] + offs[ci] for ci in range(len(comps))]
        act = np.vstack([results[ci][v["id"]]["act"] for ci in range(len(comps))])
        R = np.vstack(rows) if rows else np.zeros((0, len(COLS)))
        P_ = np.concatenate(pidx) if pidx else np.zeros(0, np.int32)
        cnt = Counter()
        for ci in range(len(comps)):
            cnt.update(results[ci][v["id"]]["cnt"])
        counters[v["id"]] = dict(cnt)
        XG.write_csv(os.path.join(out_root, "out", a.tag, "den", v["id"] + ".csv"), ["day", "den"], [[d0 + j, int(x)] for j, x in enumerate(act.sum(axis=0))])
        np.savez(os.path.join(arr_dir, v["id"] + ".npz"), rows=R, pidx=P_, act=act)
        if a.strict_format:
            write_trades_strict(os.path.join(out_root, "repro", a.tag, v["id"] + "_trades.csv"), R, P_, all_pairs)
        else:
            write_trades_gz(os.path.join(out_root, "out", a.tag, "trades", v["id"] + ".csv.gz"), len(COLS), R, P_, all_pairs)
        summary.append((v["id"], len(R), float(R[:, L.TRADE_FIELDS.index("net")].sum()) if len(R) else 0.0))
    XG.write_json(os.path.join(out_root, "out", a.tag, "counters.json"), counters)
    meta = dict(tag=a.tag, univ=a.univ, d0=d0, d1=d1, first_sig_day=p0, pairs=len(all_pairs), components=len(comps), variants=[v["id"] for v in variants], threads=a.threads,
                seconds=round(time.time() - t0, 1), commit=commit, protocol_files=shas, maxrss_mb=max(m["maxrss_mb"] for m in metas), comp_seconds_sum=round(sum(m["sec"] for m in metas), 1),
                comp_seconds_max=max(m["sec"] for m in metas), summary=summary, max_day_read=int(XG.STATE.max_read.value), calib=dict(beta={"%s|%s" % k: v_ for k, v_ in calib["beta"].items()},
                                                                                                                              gamma={str(k): v_ for k, v_ in calib["gamma"].items()}))
    XG.write_json(os.path.join(out_root, "out", a.tag, "run_meta.json"), meta)
    XG.log_max_read("run_end")
    for vid, n, net in summary:
        print("%-28s сделок %6d  Σnet %12.1f" % (vid, n, net))
    print("готово за %.0f с" % (time.time() - t0), flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    for name in ("prep", "run"):
        s = sp.add_parser(name)
        s.add_argument("--univ", default="main", choices=("main", "gate", "eq"))
        s.add_argument("--root", default=os.path.expanduser("~/xdl9"))
        s.add_argument("--raw-root", default=os.path.expanduser("~/xdl/out"))
        s.add_argument("--out", default=XG.c2x_home())
        s.add_argument("--threads", type=int, default=6)
        s.add_argument("--limit-pairs", type=int, default=0)
        if name == "run":
            s.add_argument("--code-root", default=os.path.abspath(os.path.join(HERE, "..", "..", "..", "..", "..")))
            s.add_argument("--tag", default="main")
            s.add_argument("--ids", default=None)
            s.add_argument("--fam", default=None)
            s.add_argument("--stage", default=None)
            s.add_argument("--first-sig-day", type=int, default=P0_DEFAULT)
            s.add_argument("--last-day", type=int, default=D_LAST)
            s.add_argument("--strict-format", action="store_true", help="trades.csv в формате раннера окна, без баннера (только сверка G-X-1)")
            s.add_argument("--addendum-rel", default=None, help="docs/research/P-39-params-calib-<дата>.csv (относительно code-root)")
            s.add_argument("--extra-params", default=None, help="доп. файл строк v_* (стадия 2)")
            s.add_argument("--no-suppl", action="store_true", help="не добавлять семейство Cu (для воспроизведения G-X-1)")
    a = ap.parse_args(argv)
    {"prep": cmd_prep, "run": cmd_run}[a.cmd](a)


if __name__ == "__main__":
    main()
