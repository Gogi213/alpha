"""П-42 §13.17 ворота (5), (6), (8): независимая метка, отсутствие заглядывания, единицы эпизодов.
Использование: python3 sigfeat_gate2.py <рабочий каталог> <каталог прогона движка out/eng> label|nolook|units [--n N]
Пишет `<work>/gate2_<режим>.json`, код возврата 1 при расхождении.
label  — метка и исходы на случайных эпизодах U1 + шесть примеров владельца + эпизоды у конца окна + контрольные окна, пересчёт без numpy
         построчным чтением zip (csv + целые), сравнение со столбцами таблицы эпизодов; отдельно — число эпизодов, где исход меняется,
         если окно считать «строго после sig_ms» (а не после сигнальной сделки), справочно.
nolook — признаки на случайных сигналах пересчитываются по данным, обрезанным на t−1 (минутки) и сигнальную сделку (тики): за границей — мусор.
units  — (8) число эпизодов = число уровней со ≥ 1 сигналом + повторные эпизоды.
"""
from __future__ import annotations

import bisect
import csv
import datetime as dt
import io
import json
import math
import os
import sys
import zipfile
from decimal import Decimal, ROUND_HALF_EVEN
from fractions import Fraction

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sigfeat_lib as L
import sigfeat_run as R

TRADES = R.TRADES
BAD = []
NAN = float("nan")


# ───────────────────────── независимое чтение сделок ─────────────────────────

def px_e8(s: str) -> int:
    a, _, b = s.partition(".")
    if len(b) <= 8 and "E" not in s and "e" not in s:
        return int(a + b.ljust(8, "0"))
    return int((Decimal(s) * 100000000).to_integral_value(ROUND_HALF_EVEN))


class Day:
    def __init__(self, sym):
        self.sym, self.c, self.order = sym, {}, []

    def get(self, d):
        if d in self.c:
            return self.c[d]
        v = None
        if 0 <= d < L.DAYS_TOTAL:
            name = f"{self.sym}-aggTrades-{L.day_date(d)}"
            try:
                zf = zipfile.ZipFile(f"{TRADES}/{self.sym}/{name}.zip")
            except FileNotFoundError:
                zf = None
            if zf is not None:
                rows = []
                with zf, zf.open(name + ".csv") as fh:
                    rd = csv.reader(io.TextIOWrapper(fh, encoding="utf-8"))
                    head = next(rd)
                    ip, it = head.index("price"), head.index("transact_time")
                    for r in rd:
                        rows.append((int(r[it]) - L.BASE_MS, px_e8(r[ip])))
                v = sorted(rows, key=lambda x: x[0])  # устойчивая сортировка
        self.c[d] = v
        self.order.append(d)
        while len(self.order) > 4:
            self.c.pop(self.order.pop(0), None)
        return v

    def series(self, a_day, b_day, s):
        out = []
        for d in range(a_day, b_day + 1):
            v = self.get(d)
            if v:
                out += [(t, s * p) for t, p in v]
        return out


def dist_ind(Rv: int, P: int) -> int:
    return int(Fraction((Rv - P) * L.SCALE, abs(Rv)))  # усечение к нулю


def ceil_frac(a: int, b: int) -> int:
    return math.ceil(Fraction(a, b))


def scan(win, Rv, x_add, f_floor=0):
    """Исход O1…O4 и индексы касания / пробоя / возврата по окну [(t, P)]."""
    tc = tx = tr = None
    for i, (_, P) in enumerate(win):
        if tc is None and P >= Rv:
            tc = i
        if tx is None and P >= Rv + x_add:
            tx = i
        if tc is not None and i > tc and tr is None and P < Rv - f_floor:
            tr = i
    if tc is None:
        o = 1
    elif tx is not None and (tr is None or tx < tr):
        o = 3
    elif tr is not None and (tx is None or tr < tx):
        o = 4
    else:
        o = 2
    return o, tc, tx, tr


def twin(win, tc, tprime, Rv, x_add):
    if tc is None:
        return None
    lim = win[tc][0] + tprime
    seg = [P for t, P in win[tc + 1:] if t <= lim]
    ix = next((i for i, P in enumerate(seg) if P >= Rv + x_add), None)
    ir = next((i for i, P in enumerate(seg) if P < Rv), None)
    if ix is not None and (ir is None or ix < ir):
        return "x"
    if ir is not None and (ix is None or ir < ix):
        return "r"
    return "n"


def isnull(x):
    return x is None or (isinstance(x, float) and x != x)


def eq(a, b):
    if isnull(a) or isnull(b):
        return isnull(a) and isnull(b)
    if isinstance(a, str) or isinstance(b, str):
        return a == b
    a, b = float(a), float(b)
    return abs(a - b) <= 1e-9 * max(1.0, abs(a), abs(b))


def indep_labels(e, dd: Day):
    s = 1 if e["side"] == "long" else -1
    sig, absR = int(e["sig_ms"]), int(e["lp_e8"])
    Rv = s * absR
    nat = int(e["natr_e8"])
    mem = [tuple(map(int, x.split(":"))) for x in e["members_str"].split("|")]
    lpx = max(p for _, p in mem) if s > 0 else min(p for _, p in mem)
    Rx = s * lpx
    out = {}
    ok = {}
    for T in R.GRID_T:
        end = sig + T * L.MIN_MS
        ok[T] = end <= L.END_MS - 1 and all(dd.get(d) is not None for d in range(sig // L.DAY_MS, end // L.DAY_MS + 1))
        out[f"ok_t{T}"] = ok[T]
    ser = dd.series(sig // L.DAY_MS, (sig + 240 * L.MIN_MS) // L.DAY_MS, s)
    k = next((i for i, (t, P) in enumerate(ser) if t == sig and dist_ind(Rv, P) == int(e["dist_e8"])), None)
    if k is None:
        return None, None
    Pk = ser[k][1]

    def win(T, strict=False):
        lim = sig + T * L.MIN_MS
        if strict:
            return [(t, P) for t, P in ser if sig < t <= lim]
        return [(t, P) for t, P in ser[k + 1:] if t <= lim]

    for xn, ppm in R.GRID_X.items():
        xa = ceil_frac(absR * ppm, 1_000_000)
        for T in R.GRID_T:
            if not ok[T]:
                out[f"o_x{xn}_t{T}"] = 0
                continue
            w = win(T)
            o, tc, tx, tr = scan(w, Rv, xa)
            out[f"o_x{xn}_t{T}"] = o
            if xn == "1" and T == 60:
                out["tc_s"] = (w[tc][0] - sig) / 1000.0 if tc is not None else NAN
                out["tx_s"] = (w[tx][0] - sig) / 1000.0 if tx is not None else NAN
                out["tr_s"] = (w[tr][0] - sig) / 1000.0 if tr is not None else NAN
                out["mfe60_bp"] = (max(P for _, P in w) - Rv) / absR * 1e4 if w else NAN
                out["o_x1_t60_f01"] = scan(w, Rv, xa, absR * 1000 // 1_000_000)[0]
            if xn == "1" and T == 240:
                out["mfe240_bp"] = (max(P for _, P in w) - Rv) / absR * 1e4 if w else NAN
    for kk in R.NATR_K:
        out[f"o_n{kk}_t60"] = scan(win(60), Rv, ceil_frac(absR * nat * kk, L.SCALE))[0] if ok[60] else 0
    for xn, ppm in R.GRID_X.items():
        out[f"ox_x{xn}_t60"] = scan(win(60), Rx, ceil_frac(abs(Rx) * ppm, 1_000_000))[0] if ok[60] else 0
    if ok[240]:
        w240 = win(240)
        tc240 = scan(w240, Rv, ceil_frac(absR * 10000, 1_000_000))[1]
        for tp in (15, 60):
            out[f"tw_x1_t{tp}"] = twin(w240, tc240, tp * L.MIN_MS, Rv, ceil_frac(absR * 10000, 1_000_000))
            out[f"tw_n2_t{tp}"] = twin(w240, tc240, tp * L.MIN_MS, Rv, ceil_frac(absR * nat * 2, L.SCALE))
    else:
        for tp in (15, 60):
            out[f"tw_x1_t{tp}"] = None
            out[f"tw_n2_t{tp}"] = None
    for T in R.GRID_T:
        w = win(T) if ok[T] else []
        out[f"opp_t{T}_bp"] = (Pk - min(P for _, P in w)) / abs(Pk) * 1e4 if w else NAN
    if ok[60]:
        w = win(60)
        for dn, ppm in R.APPROACH_D.items():
            thr = Rv - absR * ppm // 1_000_000
            i = next((i for i, (_, P) in enumerate(w) if P >= thr), None)
            if i is None:
                out[f"reach_{dn}_s"] = NAN
                out[f"mae_{dn}_bp"] = NAN
            else:
                out[f"reach_{dn}_s"] = (w[i][0] - sig) / 1000.0
                out[f"mae_{dn}_bp"] = max((Pk - min(P for _, P in w[:i + 1])) / abs(Pk) * 1e4, 0.0)
    else:
        for dn in R.APPROACH_D:
            out[f"reach_{dn}_s"] = NAN
            out[f"mae_{dn}_bp"] = NAN
    # справочно: окно «строго после sig_ms» вместо «после сигнальной сделки»
    strict = {}
    if ok[60]:
        xa = ceil_frac(absR * 10000, 1_000_000)
        strict["o_x1_t60"] = scan(win(60, True), Rv, xa)[0]
    return out, strict


def indep_controls(e, m, pool, dd: Day):
    """Контрольные окна эпизода: моменты — как в `Worker.controls` (зерно, кандидаты), исходы — построчным сканом."""
    s = 1 if e["side"] == "long" else -1
    sym = e["sym"]
    day = int(e["day"])
    t_first = int(e["sig_ms"]) // L.MIN_MS
    t_last = int(e["last_sig_ms"]) // L.MIN_MS
    rng_base = [588, sum((i + 1) * ord(ch) for i, ch in enumerate(sym)), 0 if s > 0 else 1, 0]
    lo, hi = max(day * 1440, L.START_DAY * 1440), (day + 1) * 1440
    cand = np.arange(lo + 1, hi)
    cand = cand[pool[cand] & (np.abs(cand - t_first) > 60) & (np.abs(cand - t_last) > 60)]
    rng = np.random.default_rng(rng_base + [int(e["ep_no"]), int(int(e["sig_ms"]) // 1000)])
    if len(cand) == 0:
        return {"ctrl_n": 0}, 0
    pick = rng.choice(cand, size=min(R.NCTRL, len(cand)), replace=False)
    inside = int(sum(1 for mm in pick if t_first - 60 <= int(mm) <= t_last + 60))
    n_ok = o3x = o3n = touch = 0
    dist = int(e["dist_e8"])
    nat = int(e["natr_e8"])
    ser_all = dd.series(day, min(day + 1, L.DAYS_TOTAL - 1), s)
    times = [t for t, _ in ser_all]
    for mm in pick:
        a = int(mm) * L.MIN_MS
        b = a + 61 * L.MIN_MS
        if b > L.END_MS - 1:
            continue
        days = range(a // L.DAY_MS, b // L.DAY_MS + 1)
        if any(dd.get(d) is None for d in days):
            continue
        ser = ser_all[bisect.bisect_left(times, a):bisect.bisect_right(times, b)]
        if not ser or ser[0][0] >= a + L.MIN_MS:
            continue
        P0 = ser[0][1]
        Rv = P0 * L.SCALE // (L.SCALE - s * dist)
        aR = abs(Rv)
        w = [(t, P) for t, P in ser[1:] if t <= ser[0][0] + 60 * L.MIN_MS]
        ox = scan(w, Rv, ceil_frac(aR * 10000, 1_000_000))
        on = scan(w, Rv, ceil_frac(aR * nat * 2, L.SCALE))
        n_ok += 1
        o3x += ox[0] == 3
        o3n += on[0] == 3
        touch += ox[0] != 1
    if n_ok == 0:
        return {"ctrl_n": 0, "ctrl_o3_x1": NAN, "ctrl_o3_n2": NAN, "ctrl_touch_x1": NAN}, inside
    return {"ctrl_n": n_ok, "ctrl_o3_x1": o3x / n_ok, "ctrl_o3_n2": o3n / n_ok, "ctrl_touch_x1": touch / n_ok}, inside


EXAMPLES = [("KORUUSDT", "long", "2026-10-12 04:22:36"), ("STXXUSDT", "long", "2026-10-14 18:07:02"), ("CLANKERUSDT", "long", "2026-10-15 22:27:04"),
            ("NXPCUSDT", "long", "2026-10-18 22:04:01"), ("WDCUSDT", "long", "2026-10-23 00:03:00"), ("ANIMEUSDT", "short", "2026-10-15 20:59:48")]


def pick_examples(ep, sg):
    out = []
    for sym, side, ts in EXAMPLES:
        t = dt.datetime.fromisoformat(ts).replace(tzinfo=dt.timezone(dt.timedelta(hours=4)))
        ms = int(t.timestamp() * 1000) - L.BASE_MS
        c = sg[(sg.sym == sym) & (sg.side == side)].copy()
        c["d"] = (c["sig_ms"] - ms).abs()
        c = c.sort_values("d")
        if len(c) and c.iloc[0]["d"] <= 120_000:
            h = c.iloc[0]
            e = ep[(ep.side == side) & (ep.sym == sym) & (ep.level == h["level"]) & (ep.ep_no == h["ep_no"])]
            if len(e):
                out.append(e.index[0])
    return out


def run_label(W, eng, n, univ="U1"):
    ep = pd.read_parquet(f"{W}/episodes.parquet")
    sg = pd.read_parquet(f"{W}/signals_all.parquet")
    u1 = ep[ep.univ == univ]
    rng = np.random.default_rng(5881)
    idx, ctrl_idx = [], []
    for side in ("long", "short"):
        d = u1[(u1.side == side) & (u1.ok_t60)]
        pick = list(rng.choice(d.index.to_numpy(), size=n // 2, replace=False))
        idx += pick
        ctrl_idx += pick[:15]
    late = u1[u1.day >= 72]
    idx += [i for i in late.index.to_numpy()[:6] if i not in idx]  # у конца окна: цензура
    ex = pick_examples(u1, sg[sg.univ == univ])
    idx += [i for i in ex if i not in idx]
    res = {"episodes": 0, "examples_found": len(ex), "mismatch": [], "strict_diff": [], "columns_compared": 0, "ctrl_windows": 0, "ctrl_episodes": 0, "ctrl_inside_episode": 0, "ctrl_mismatch": []}
    sigs = R.load_sigsx(eng, "b0" if univ == "U1" else "b0p0")
    dds = {}
    mcache = {}
    for i in idx:
        e = u1.loc[i].to_dict()
        g = sigs[(e["sym"], 1 if e["side"] == "long" else -1)]
        e["members_str"] = g[(g.level == e["level"]) & (g.sig_ms == e["sig_ms"])].iloc[0]["members"]
        dd = dds.setdefault(e["sym"], Day(e["sym"]))
        out, strict = indep_labels(e, dd)
        if out is None:
            res["mismatch"].append({"ep": e["ep_id"], "col": "signal_tick", "table": "найден", "indep": "нет"})
            continue
        res["episodes"] += 1
        for c, v in out.items():
            res["columns_compared"] += 1
            if not eq(e.get(c), v):
                res["mismatch"].append({"ep": e["ep_id"], "col": c, "table": None if e.get(c) != e.get(c) else e.get(c), "indep": v})
        if strict and strict.get("o_x1_t60") != int(e["o_x1_t60"]):
            res["strict_diff"].append({"ep": e["ep_id"], "table": int(e["o_x1_t60"]), "strict": strict["o_x1_t60"]})
        if univ == "U1" and i in ctrl_idx:
            sym = e["sym"]
            if sym not in mcache:
                m = L.load_b42(f"{R.CACHE}/{sym}.b42")
                nat = L.natr_series(m)
                mcache[sym] = (m, R.pool_mask(m, nat)[0])
            c, inside = indep_controls(e, mcache[sym][0], mcache[sym][1], dd)
            res["ctrl_episodes"] += 1
            res["ctrl_windows"] += int(c.get("ctrl_n", 0))
            res["ctrl_inside_episode"] += inside
            for cn in ("ctrl_n", "ctrl_o3_x1", "ctrl_o3_n2", "ctrl_touch_x1"):
                if not eq(e.get(cn), c.get(cn)):
                    res["ctrl_mismatch"].append({"ep": e["ep_id"], "col": cn, "table": e.get(cn), "indep": c.get(cn)})
    res["strict_vs_after_signal_tick_diff_episodes"] = len(res["strict_diff"])
    json.dump(res, open(f"{W}/gate2_label{'' if univ == 'U1' else '_' + univ}.json", "w"), ensure_ascii=False, indent=1, default=str)
    print({k: (len(v) if isinstance(v, list) else v) for k, v in res.items()})
    return bool(res["mismatch"] or res["ctrl_mismatch"])


# ───────────────────────── отсутствие заглядывания ─────────────────────────

FEATS = ["natr_e8", "dist_e8", "lp_e8", "hour4", "wday", "n_piv", "spread_bp", "spread_tau", "ext_gap_bp", "age_h", "since_born_h", "pierce_ep", "pierce_max_bp",
         "touch_ep", "since_touch_h", "depth_min_bp", "depth_mean_bp", "dist_bp", "since_touch_prior_h", "approach_dur_min", "touch_prior_none",
         "emp2h", "emp6h", "flat60", "tpm15", "tpm60", "gap60_s", "mv5", "mv15", "mv60", "maxmv1", "maxrng15", "cons60", "cons120",
         "c1_btc_6h", "c1_eth_6h", "c5_btc_24h", "c5_eth_24h", "c5_btc_7d", "c5_eth_7d", "c15_btc_24h", "c15_eth_24h", "c15_btc_7d", "c15_eth_7d",
         "btc_mv5", "btc_mv15", "eth_mv5", "eth_mv15", "natr_bp", "turn1h", "turn24h"]


class TruncTicks(L.Ticks):
    """Сделки только до сигнальной сделки включительно; после — мусор (времена позже сигнала, цены случайные)."""

    def __init__(self, root, sym, cut_ms, s, Rv, dist, seed):
        super().__init__(root, sym)
        self.cut, self.s, self.Rv, self.dist = cut_ms, s, Rv, dist
        self.rng = np.random.default_rng(seed)

    def span(self, a, b):
        t, p, full = super().span(a, b)
        if b < self.cut or len(t) == 0:
            return t, p, full
        k = L.find_signal_tick(t, self.s * p, self.cut, self.Rv, self.dist)
        if k is None:
            return t, p, full
        n = len(t) - k - 1
        gt = np.sort(self.rng.integers(self.cut + 1, max(b, self.cut + 2), size=n))
        gp = self.rng.integers(1, 10**11, size=n)
        return np.concatenate([t[:k + 1], gt]), np.concatenate([p[:k + 1], gp]), full


def truncated_minutes(m, t, seed):
    g = {k: v.copy() for k, v in m.items()}
    rng = np.random.default_rng(seed)
    for k in g:
        g[k][t:] = rng.integers(1, 10**11, size=len(g[k]) - t)
    return g


def run_nolook(W, eng, n):
    ep = pd.read_parquet(f"{W}/episodes.parquet")
    rng = np.random.default_rng(5882)
    u1, u0 = ep[ep.univ == "U1"], ep[ep.univ == "U0"]
    idx = []
    for side in ("long", "short"):
        a1, a0 = u1[u1.side == side].index.to_numpy(), u0[u0.side == side].index.to_numpy()
        idx += list(rng.choice(a1, size=min(n // 2, len(a1)), replace=False))
        idx += list(rng.choice(a0, size=min(max(n // 6, 5), len(a0)), replace=False))
    sigs = {v: R.load_sigsx(eng, v) for v in ("b0", "b0p0")}
    W_ = R.Worker({"sigs": sigs, "levels": {}, "traded": {}})
    res = {"episodes": 0, "features_compared": 0, "mismatch": []}
    for i in idx:
        e = ep.loc[i]
        s = 1 if e["side"] == "long" else -1
        var = "b0" if e["univ"] == "U1" else "b0p0"
        g = sigs[var][(e["sym"], s)]
        row = next(g[(g.level == e["level"]) & (g.sig_ms == e["sig_ms"])].itertuples(index=False))
        t = int(e["sig_ms"]) // L.MIN_MS
        m = L.load_b42(f"{R.CACHE}/{e['sym']}.b42")
        mt = truncated_minutes(m, t, 1)
        for r in R.SYMS_REF:
            mr = truncated_minutes(L.load_b42(f"{R.CACHE}/{r}.b42"), t, 2)
            W_.ref[r] = {"m": mr, "cf": L.ffill_close(mr), "r5": L.block_returns(mr, 5), "r15": L.block_returns(mr, 15), "r1": L.minute_returns(mr)}
        first_valid = int(np.argmax(m["o"] != 0))
        natr = L.natr_series(mt)
        pool, t1h = R.pool_mask(mt, natr)
        Rv = s * int(row.level_px_e8)
        tk = TruncTicks(TRADES, e["sym"], int(e["sig_ms"]), s, Rv, int(row.dist_e8), 3)
        rb = [588, sum((j + 1) * ord(ch) for j, ch in enumerate(e["sym"])), 0 if s > 0 else 1, 0 if e["univ"] == "U1" else 1]
        st = {"locate_fail": 0, "natr_mismatch": 0, "pool_violation": 0}
        out = W_.episode(e["sym"], "U0", e["side"], s, row, int(e["ep_no"]), int(e["n_sig_ep"]), int(e["last_sig_ms"]), mt, L.ffill_close(mt), natr, pool, t1h,
                         L.block_returns(mt, 5), L.block_returns(mt, 15), L.minute_returns(mt), tk, first_valid, float(e["emp_coin"]), st, rb)
        res["episodes"] += 1
        if out is None:
            res["mismatch"].append({"ep": e["ep_id"], "col": "episode", "table": "есть", "trunc": "нет"})
            continue
        for c in FEATS:
            res["features_compared"] += 1
            if not eq(e[c], out.get(c)):
                res["mismatch"].append({"ep": e["ep_id"], "col": c, "table": None if e[c] != e[c] else float(e[c]), "trunc": out.get(c)})
    # признаки «рынка» (cosig15, peers7, peer_sig30): пересчёт по сигналам строго раньше и матрице суток до D−1 (мусор с суток D)
    sg = pd.read_parquet(f"{W}/signals_all.parquet")
    u1s = sg[sg.univ == "U1"]
    import sigfeat_join as J
    syms = sorted(f[:-4] for f in os.listdir(R.CACHE) if f.endswith(".b42"))
    ix = {s_: j for j, s_ in enumerate(syms)}
    R5 = np.empty((len(syms), L.N_MIN // 5))
    for j, s_ in enumerate(syms):
        R5[j] = L.block_returns(L.load_b42(f"{R.CACHE}/{s_}.b42"), 5)
    res["market_checked"] = 0
    for i in [j for j in idx if ep.loc[j, "univ"] == "U1"]:
        e = ep.loc[i]
        day = int(e["day"])
        R5t = R5.copy()
        R5t[:, day * 288:] = np.random.default_rng(4).normal(size=R5t[:, day * 288:].shape)
        r, _ = L.corr_matrix(R5t[:, (day - 7) * 288: day * 288], 500)
        pm = np.nan_to_num(r, nan=0.0) >= J.PEER_R
        np.fill_diagonal(pm, False)
        me = ix[e["sym"]]
        prior = u1s[(u1s.side == e["side"]) & (u1s.sig_ms < e["sig_ms"])]
        c15 = set(prior[prior.sig_ms > e["sig_ms"] - 15 * L.MIN_MS]["sym"]) - {e["sym"]}
        c30 = set(prior[prior.sig_ms > e["sig_ms"] - 30 * L.MIN_MS]["sym"]) - {e["sym"]}
        got = {"cosig15": len(c15), "peers7": int(pm[me].sum()), "peer_sig30": int(sum(bool(pm[me][ix[x]]) for x in c30))}
        res["market_checked"] += 1
        for c, v in got.items():
            if int(e[c]) != v:
                res["mismatch"].append({"ep": e["ep_id"], "col": c, "table": int(e[c]), "trunc": v})
    json.dump(res, open(f"{W}/gate2_nolook.json", "w"), ensure_ascii=False, indent=1, default=str)
    print({k: (len(v) if isinstance(v, list) else v) for k, v in res.items()})
    return bool(res["mismatch"])


def run_units(W):
    sg = pd.read_parquet(f"{W}/signals_all.parquet")
    ep = pd.read_parquet(f"{W}/episodes.parquet", columns=["univ", "side", "sym", "level", "ep_no"])
    res = {}
    bad = False
    for univ in ("U1", "U0"):
        for side in ("long", "short"):
            s = sg[(sg.univ == univ) & (sg.side == side)]
            levels = s.groupby(["sym", "level"]).ngroups
            first = int(s["ep_first"].sum())
            repeat = int(((s["ep_no"] >= 1) & s["ep_first"]).sum())
            e = ep[(ep.univ == univ) & (ep.side == side)]
            res[f"{univ}/{side}"] = {"signals": int(len(s)), "levels": int(levels), "episodes_from_signals": first, "repeat_episodes": repeat,
                                     "episodes_in_table": int(len(e)), "identity": first == levels + repeat, "table_eq": first == len(e)}
            bad |= not (first == levels + repeat and first == len(e))
    json.dump(res, open(f"{W}/gate2_units.json", "w"), ensure_ascii=False, indent=1)
    print(json.dumps(res, ensure_ascii=False))
    return bad


def run_strict(W):
    """Все эпизоды U1: (а) основная метка при окне «строго после sig_ms» против «после сигнальной сделки» (как в таблице); (б) выбросы цен в окне
    [sig − 60 мин, sig + 240 мин]: сделки с ценой ≤ 0,2·LP или ≥ 5·LP (нефизично — признак порчи данных); (в) сверка столбца o_x1_t60 с пересчётом по тем же функциям."""
    ep = pd.read_parquet(f"{W}/episodes.parquet", columns=["univ", "side", "sym", "ep_id", "sig_ms", "lp_e8", "dist_e8", "ok_t60", "o_x1_t60"])
    u1 = ep[ep.univ == "U1"]
    res = {"episodes": 0, "strict_diff": [], "outlier": [], "recompute_mismatch": 0, "locate_fail": 0}
    for sym, g in u1.groupby("sym"):
        tk = L.Ticks(TRADES, sym)
        for e in g.itertuples():
            s = 1 if e.side == "long" else -1
            sig, absR = int(e.sig_ms), int(e.lp_e8)
            Rv = s * absR
            t, p, _ = tk.span(sig - 60 * L.MIN_MS, sig + 240 * L.MIN_MS)
            P = s * p
            k = L.find_signal_tick(t, P, sig, Rv, int(e.dist_e8))
            if k is None:
                res["locate_fail"] += 1
                continue
            res["episodes"] += 1
            if (p <= absR * 0.2).any() or (p >= absR * 5).any():
                res["outlier"].append({"ep": e.ep_id, "min_px_e8": int(p.min()), "max_px_e8": int(p.max()), "lp_e8": absR})
            if not e.ok_t60:
                continue
            xa = L.ceil_mul(absR, 10000, 1_000_000)
            end = int(np.searchsorted(t, sig + 60 * L.MIN_MS, "right"))
            o_tab = L.outcome(P[k + 1:end], Rv, xa)[0]
            o_str = L.outcome(P[int(np.searchsorted(t, sig, "right")):end], Rv, xa)[0]
            res["recompute_mismatch"] += int(o_tab != int(e.o_x1_t60))
            if o_tab != o_str:
                res["strict_diff"].append({"ep": e.ep_id, "side": e.side, "table": o_tab, "strict": o_str})
    res["strict_diff_n"] = len(res["strict_diff"])
    res["outlier_n"] = len(res["outlier"])
    json.dump(res, open(f"{W}/gate2_strict.json", "w"), ensure_ascii=False, indent=1, default=str)
    print({k: (len(v) if isinstance(v, list) else v) for k, v in res.items()})
    return bool(res["recompute_mismatch"] or res["locate_fail"])


def main():
    W, eng, mode = sys.argv[1], sys.argv[2], sys.argv[3]
    n = int(sys.argv[sys.argv.index("--n") + 1]) if "--n" in sys.argv else 40
    univ = sys.argv[sys.argv.index("--univ") + 1] if "--univ" in sys.argv else "U1"
    bad = {"label": lambda: run_label(W, eng, n, univ), "nolook": lambda: run_nolook(W, eng, n), "units": lambda: run_units(W), "strict": lambda: run_strict(W)}[mode]()
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
