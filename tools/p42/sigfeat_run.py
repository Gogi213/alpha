"""П-42 §13.17 (TK-588): таблица эпизодов подхода — признаки, метки, контроли; запуск на srv.

Использование: python3 sigfeat_run.py <каталог прогона движка out/eng> <рабочий каталог> [--coins A,B] [--procs 6]
Читает `<side>-sigsx.csv` / `<side>-levels.csv` вариантов b0, b0p0, b0pk, ow; тики — /data/aggtrades/binance; минутки — /data/p42-t566/cache.
Пишет `<work>/episodes.parquet` (+csv.gz), `signals_all.parquet`, `crossings.csv.gz`, `run_stats.json`. Окно — сутки 14…73 (15.09–13.11), 14.11+ не читается.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from multiprocessing import Pool

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sigfeat_lib import *  # noqa: F401,F403

CACHE = "/data/p42-t566/cache"
TRADES = "/data/aggtrades/binance"
E16 = "/data/p42-t566/out/e16"
SIDES = {"long": 1, "short": -1}
NCTRL = 20
GRID_X = {"0p5": 5000, "1": 10000, "2": 20000}  # ppm цены уровня
GRID_T = (15, 60, 240)
NATR_K = (1, 2, 4)
APPROACH_D = {"0p2": 2000, "0p1": 1000, "0p05": 500}  # ppm
SYMS_REF = ("BTCUSDT", "ETHUSDT")


def parse_members(s: str):
    return [(int(a), int(b)) for a, b in (x.split(":") for x in s.split("|"))]


def load_sigsx(eng: str, variant: str) -> dict[tuple[str, int], pd.DataFrame]:
    out = {}
    for side, s in SIDES.items():
        df = read_sigsx(f"{eng}/{variant}/{side}-sigsx.csv")
        df["side"] = s
        for sym, g in df.groupby("sym"):
            out[(sym, s)] = g.sort_values(["level", "sig_ms"]).reset_index(drop=True)
    return out


def pool_mask(m, natr):
    """Пул §13.2 по минутам ≤ t−1: NATR ≥ 0,4 %, оборот часа ≥ $500 тыс."""
    qv = m["qv"].astype(np.int64)
    pq = np.concatenate([[0], np.cumsum(qv)])
    t = np.arange(N_MIN)
    ok = np.zeros(N_MIN, bool)
    ok[61:] = (natr[60:-1] >= 400_000)
    t1h = np.zeros(N_MIN, np.int64)
    t1h[60:] = pq[60:N_MIN] - pq[0:N_MIN - 60]  # сумма qv минут t−60…t−1 = pq[t] − pq[t−60]
    ok &= t1h >= 50_000_000_000
    ok &= m["o"] != 0
    return ok, t1h


class Worker:
    def __init__(self, cfg):
        self.cfg = cfg
        self.ref = {}
        for r in SYMS_REF:
            m = load_b42(f"{CACHE}/{r}.b42")
            self.ref[r] = {"m": m, "cf": ffill_close(m), "r5": block_returns(m, 5), "r15": block_returns(m, 15),
                           "r1": minute_returns(m)}

    # ── минутка сигнала: найти сигнальную сделку, голову и хвост минуты
    def locate_minute(self, tk, s, sig_ms, R, dist):
        t_min = sig_ms // MIN_MS
        t, p, _ = tk.span(t_min * MIN_MS, t_min * MIN_MS + MIN_MS - 1)
        P = s * p
        k = find_signal_tick(t, P, sig_ms, R, dist)
        return t, P, k

    def process(self, sym: str):
        cfg = self.cfg
        st = {"sym": sym, "locate_fail": 0, "natr_mismatch": 0, "pool_violation": 0, "sigs": 0}
        m = load_b42(f"{CACHE}/{sym}.b42")
        cf = ffill_close(m)
        first_valid = int(np.argmax(m["o"] != 0)) if (m["o"] != 0).any() else N_MIN
        r5, r15, r1 = block_returns(m, 5), block_returns(m, 15), minute_returns(m)
        tk = Ticks(TRADES, sym)
        natr = natr_series(m)
        pool, t1h = pool_mask(m, natr)
        emp_coin = float((m["o"][max(first_valid, START_DAY * 1440):] == 0).mean()) if first_valid < N_MIN else float("nan")
        sig_rows, ep_rows, cross_rows = [], [], []
        self.trade_rows = []
        for univ in ("U1", "U0"):
            var = "b0" if univ == "U1" else "b0p0"
            for side, s in SIDES.items():
                df = cfg["sigs"][var].get((sym, s))
                if df is None or df.empty:
                    continue
                self.do_universe(sym, univ, side, s, df, m, cf, natr, pool, t1h, r5, r15, r1, tk, first_valid, emp_coin,
                                 sig_rows, ep_rows, st)
        # пересечения без сигнала (только U1 = b0)
        for side, s in SIDES.items():
            lev = cfg["levels"].get((sym, s))
            if lev is not None:
                self.crossings(sym, side, s, lev, cfg["sigs"]["b0"].get((sym, s)), m, pool, cross_rows)
        st["unsorted_days"] = tk.unsorted_days
        return sig_rows, ep_rows, cross_rows, self.trade_rows, st

    # ── вселенная × сторона монеты
    def do_universe(self, sym, univ, side, s, df, m, cf, natr, pool, t1h, r5, r15, r1, tk, first_valid, emp_coin, sig_rows, ep_rows, st):
        rng_base = [588, sum((i + 1) * ord(ch) for i, ch in enumerate(sym)), 0 if s > 0 else 1, 0 if univ == "U1" else 1]
        for level, g in df.groupby("level", sort=True):
            g = g.sort_values("sig_ms")
            prev = None  # (sig_ms, t_min, tail ticks (P) )
            ep_no = -1
            recs = []
            for _, row in g.iterrows():
                sig_ms = int(row.sig_ms)
                R = s * int(row.level_px_e8)
                t_min = sig_ms // MIN_MS
                t_arr, P_arr, k = self.locate_minute(tk, s, sig_ms, R, int(row.dist_e8))
                if k is None:
                    st["locate_fail"] += 1
                    head = P_arr[:0]
                    tail = P_arr[:0]
                else:
                    head, tail = P_arr[:k], P_arr[k + 1:]
                new_ep = True
                if prev is not None:
                    P_lo = R - abs(R) * (E_E8 + 1) / SCALE
                    away = bool(((tail_prev >= R).any() or (tail_prev <= P_lo).any() or (head >= R).any() or (head <= P_lo).any()))
                    if not away and t_min - prev_tmin > 1:
                        a, b = prev_tmin + 1, t_min
                        H, Lw, O, C = long_frame(m, s)
                        dat = m["o"][a:b] != 0
                        if dat.any():
                            away = bool((H[a:b][dat] >= R).any() or (Lw[a:b][dat] <= P_lo).any())
                    new_ep = away
                if new_ep:
                    ep_no += 1
                    recs.append({"first": row, "n": 1, "last_ms": sig_ms, "ep_no": ep_no})
                else:
                    recs[-1]["n"] += 1
                    recs[-1]["last_ms"] = sig_ms
                prev_tmin = t_min
                tail_prev = tail
                prev = sig_ms
                if univ == "U1" and sig_ms in self.cfg["traded"].get((sym, side), ()):
                    self.trade_rows.append(self.trade_feats(sym, side, s, sig_ms, R, int(row.dist_e8), int(row.natr_e8), m, cf, tk, first_valid))
                sig_rows.append({"univ": univ, "side": side, "sym": sym, "level": int(level), "sig_ms": sig_ms, "ep_no": ep_no,
                                 "ep_first": new_ep})
                st["sigs"] += 1
            for r in recs:
                row = r["first"]
                ep = self.episode(sym, univ, side, s, row, r["ep_no"], r["n"], r["last_ms"], m, cf, natr, pool, t1h, r5, r15, r1, tk,
                                  first_valid, emp_coin, st, rng_base)
                if ep is not None:
                    ep_rows.append(ep)

    # ── признаки разреженности и подхода на сигнале сделки (для разреза P&L по разреженности)
    def trade_feats(self, sym, side, s, sig_ms, R, dist, natr_e8, m, cf, tk, first_valid):
        t = sig_ms // MIN_MS
        tt, pp, _ = tk.span(sig_ms - 60 * MIN_MS, sig_ms)
        k = find_signal_tick(tt, s * pp, sig_ms, R, dist)
        pre = tt[:k] if k is not None else tt[:0]
        f = {"side": side, "sym": sym, "sig_ms": sig_ms}
        f.update(sparse_features(m, first_valid, t))
        f.update(tick_sparse_features(pre, sig_ms))
        f.update(approach_features(m, cf, s, t, R, natr_e8 / SCALE))
        return f

    # ── один эпизод: признаки, метки, контроль
    def episode(self, sym, univ, side, s, row, ep_no, n_sig, last_ms, m, cf, natr, pool, t1h, r5, r15, r1, tk, first_valid, emp_coin, st,
                rng_base):
        sig_ms = int(row.sig_ms)
        t = sig_ms // MIN_MS
        day = sig_ms // DAY_MS
        R = s * int(row.level_px_e8)
        absR = abs(R)
        natr_e8 = int(row.natr_e8)
        if natr_e8 != int(natr[t - 1]):
            st["natr_mismatch"] += 1
        if univ == "U1" and not pool[t]:
            st["pool_violation"] += 1
        members = parse_members(row.members)
        a0, b0_ = sig_ms - 60 * MIN_MS, sig_ms + 240 * MIN_MS
        tt, pp, full = tk.span(a0, b0_)
        P = s * pp
        k = find_signal_tick(tt, P, sig_ms, R, int(row.dist_e8))
        out = {"univ": univ, "side": side, "sym": sym, "level": int(row.level), "ep_no": ep_no, "ep_id": f"{univ}|{side}|{sym}|{int(row.level)}|{ep_no}",
               "sig_ms": sig_ms, "day": int(day), "half": "A" if day < START_DAY + 30 else "B", "n_sig_ep": n_sig, "last_sig_ms": last_ms,
               "lp_e8": absR, "natr_e8": natr_e8, "dist_e8": int(row.dist_e8), "emp_coin": emp_coin,
               "hour4": int((sig_ms % DAY_MS) // (4 * 3600_000)), "wday": int((day + 1) % 7)}  # 01.09.2026 — вторник → пн = 0: (day+1) % 7
        if k is None:
            st["locate_fail"] += 1
            return None
        out["tick_ok"] = True
        pre_t = tt[:k]
        # признаки
        lf = level_features(m, s, t, R, members, int(row.born), int(row.first_bar), int(row.dist_e8), natr_e8)
        out.update(lf)
        out.update(fresh_prior_features(m, s, t, R, [x[0] for x in members], int(row.first_bar)))
        out.update(sparse_features(m, first_valid, t))
        out.update(tick_sparse_features(pre_t, sig_ms))
        natr_f = natr_e8 / SCALE
        out.update(approach_features(m, cf, s, t, R, natr_f))
        out.update(context_features(m, t, natr_e8))
        out.update(self.corr_features(sym, s, t, r5, r15, r1))
        # метки
        mean_px = np.mean([x[1] for x in members])
        lpx = max(x[1] for x in members) if s > 0 else min(x[1] for x in members)
        Rx = s * int(lpx)
        out["lpx_e8"] = int(lpx)
        Pk = int(P[k])
        absPk = abs(Pk)

        def window_end(T):
            return int(np.searchsorted(tt, sig_ms + T * MIN_MS, "right"))

        def ok_T(T):
            if sig_ms + T * MIN_MS > END_MS - 1:
                return False
            return all(tk.day(d) is not None for d in range((sig_ms) // DAY_MS, (sig_ms + T * MIN_MS) // DAY_MS + 1))

        ok = {T: ok_T(T) for T in GRID_T}
        for T in GRID_T:
            out[f"ok_t{T}"] = ok[T]
        tc240 = INF
        for xn, ppm in GRID_X.items():
            x_add = ceil_mul(absR, ppm, 1_000_000)
            for T in GRID_T:
                if not ok[T]:
                    out[f"o_x{xn}_t{T}"] = 0
                    continue
                Pw = P[k + 1:window_end(T)]
                oc, tc, tx, tr = outcome(Pw, R, x_add, 0)
                out[f"o_x{xn}_t{T}"] = oc
                if xn == "1" and T == 60:
                    base = k + 1
                    out["tc_s"] = (tt[base + tc] - sig_ms) / 1000.0 if tc < INF else float("nan")
                    out["tx_s"] = (tt[base + tx] - sig_ms) / 1000.0 if tx < INF else float("nan")
                    out["tr_s"] = (tt[base + tr] - sig_ms) / 1000.0 if tr < INF else float("nan")
                    out["mfe60_bp"] = float((Pw.max() - R) / absR * 1e4) if len(Pw) else float("nan")
                    # возврат глубже f = 0,1 % (справочно)
                    f_floor = absR * 1000 // 1_000_000
                    out["o_x1_t60_f01"] = outcome(Pw, R, x_add, f_floor)[0]
                if xn == "1" and T == 240:
                    tc240 = tc
                    base240 = k + 1
                    out["mfe240_bp"] = float((Pw.max() - R) / absR * 1e4) if len(Pw) else float("nan")
        # NATR-метки (T = 60)
        for kk in NATR_K:
            x_add = -(-(absR * natr_e8 * kk) // SCALE)
            out[f"o_n{kk}_t60"] = outcome(P[k + 1:window_end(60)], R, x_add, 0)[0] if ok[60] else 0
        # крайний пивот
        absRx = abs(Rx)
        for xn, ppm in GRID_X.items():
            x_add = ceil_mul(absRx, ppm, 1_000_000)
            out[f"ox_x{xn}_t60"] = outcome(P[k + 1:window_end(60)], Rx, x_add, 0)[0] if ok[60] else 0
        # T от первой сделки на уровне (касание в окне 240)
        if ok[240] and tc240 < INF:
            Pw240 = P[k + 1:window_end(240)]
            tw = tt[k + 1:window_end(240)]
            for tp in (15, 60):
                out[f"tw_x1_t{tp}"] = touch_window_outcome(tw, Pw240, tc240, tp * MIN_MS, R, ceil_mul(absR, 10000, 1_000_000))
                out[f"tw_n2_t{tp}"] = touch_window_outcome(tw, Pw240, tc240, tp * MIN_MS, R, -(-(absR * natr_e8 * 2) // SCALE))
        else:
            for tp in (15, 60):
                out[f"tw_x1_t{tp}"] = None
                out[f"tw_n2_t{tp}"] = None
        # обратный ход
        for T in GRID_T:
            if ok[T]:
                Pw = P[k + 1:window_end(T)]
                out[f"opp_t{T}_bp"] = float((Pk - Pw.min()) / absPk * 1e4) if len(Pw) else float("nan")
            else:
                out[f"opp_t{T}_bp"] = float("nan")
        # подход к уровню: достижение расстояний
        if ok[60]:
            Pw = P[k + 1:window_end(60)]
            tw = tt[k + 1:window_end(60)]
            for dn, ppm in APPROACH_D.items():
                thr = R - absR * ppm // 1_000_000
                hit = Pw >= thr
                if hit.any():
                    i = int(hit.argmax())
                    out[f"reach_{dn}_s"] = (tw[i] - sig_ms) / 1000.0
                    out[f"mae_{dn}_bp"] = float(max((Pk - Pw[:i + 1].min()) / absPk * 1e4, 0.0))
                else:
                    out[f"reach_{dn}_s"] = float("nan")
                    out[f"mae_{dn}_bp"] = float("nan")
        else:
            for dn in APPROACH_D:
                out[f"reach_{dn}_s"] = float("nan")
                out[f"mae_{dn}_bp"] = float("nan")
        # контроль: случайные моменты той же монеты в те же сутки (только U1)
        if univ == "U1":
            out.update(self.controls(sym, s, t, day, last_ms // MIN_MS, int(row.dist_e8), natr_e8, absR, m, pool, tk, rng_base, ep_no, sig_ms))
        return out

    def controls(self, sym, s, t_first, day, t_last, dist, natr_e8, absR, m, pool, tk, rng_base, ep_no, sig_ms):
        lo, hi = max(day * 1440, START_DAY * 1440), (day + 1) * 1440
        cand = np.arange(lo + 1, hi)
        cand = cand[pool[cand] & (np.abs(cand - t_first) > 60) & (np.abs(cand - t_last) > 60)]
        rng = np.random.default_rng(rng_base + [ep_no, int(sig_ms // 1000)])
        if len(cand) == 0:
            return {"ctrl_n": 0, "ctrl_o3_x1": float("nan"), "ctrl_o3_n2": float("nan"), "ctrl_touch_x1": float("nan")}
        pick = rng.choice(cand, size=min(NCTRL, len(cand)), replace=False)
        n_ok = o3x = o3n = touch = 0
        x1_pct = 10000
        for mm in pick:
            a, b = int(mm) * MIN_MS, int(mm) * MIN_MS + 61 * MIN_MS
            if b > END_MS - 1:
                continue
            t, p, full = tk.span(a, b)
            if not full or len(t) == 0 or t[0] >= a + MIN_MS:
                continue
            P = s * p
            P0 = int(P[0])
            R = P0 * SCALE // (SCALE - s * dist)
            aR = abs(R)
            end = int(np.searchsorted(t, int(t[0]) + 60 * MIN_MS, "right"))
            Pw = P[1:end]
            ocx = outcome(Pw, R, ceil_mul(aR, x1_pct, 1_000_000), 0)
            ocn = outcome(Pw, R, -(-(aR * natr_e8 * 2) // SCALE), 0)
            n_ok += 1
            o3x += ocx[0] == O3
            o3n += ocn[0] == O3
            touch += ocx[0] != O1
        if n_ok == 0:
            return {"ctrl_n": 0, "ctrl_o3_x1": float("nan"), "ctrl_o3_n2": float("nan"), "ctrl_touch_x1": float("nan")}
        return {"ctrl_n": n_ok, "ctrl_o3_x1": o3x / n_ok, "ctrl_o3_n2": o3n / n_ok, "ctrl_touch_x1": touch / n_ok}

    def corr_features(self, sym, s, t, r5, r15, r1):
        f = {}
        b5, b15 = t // 5 - 1, t // 15 - 1
        for name in ("BTCUSDT", "ETHUSDT"):
            tag = name[:3].lower()
            ref = self.ref[name]
            if sym == name:
                for k in (f"c5_{tag}_7d", f"c5_{tag}_24h", f"c15_{tag}_7d", f"c15_{tag}_24h", f"c1_{tag}_6h"):
                    f[k] = float("nan")
            else:
                f[f"c5_{tag}_7d"] = window_corr(r5, ref["r5"], b5, 2016, 700)
                f[f"c5_{tag}_24h"] = window_corr(r5, ref["r5"], b5, 288, 100)
                f[f"c15_{tag}_7d"] = window_corr(r15, ref["r15"], b15, 672, 250)
                f[f"c15_{tag}_24h"] = window_corr(r15, ref["r15"], b15, 96, 40)
                f[f"c1_{tag}_6h"] = pearson_pairs(r1[t - 360:t], ref["r1"][t - 360:t], 120)
            cfr = ref["cf"]
            for n in (5, 15):
                f[f"{tag}_mv{n}"] = float((cfr[t - 1] / cfr[t - 1 - n] - 1.0) * 1e4 * s)
        return f

    # ── пересечения уровня без сигнала (U1 = b0): descriptive
    def crossings(self, sym, side, s, lev: pd.DataFrame, sigs: pd.DataFrame | None, m, pool, out):
        H, Lw, O, C = long_frame(m, s)
        sig_by_level: dict[int, np.ndarray] = {}
        if sigs is not None and not sigs.empty:
            for lv, g in sigs.groupby("level"):
                sig_by_level[int(lv)] = (g["sig_ms"].to_numpy() // MIN_MS)
        for row in lev.itertuples(index=False):
            born, end = int(row.born), min(int(row.end), N_MIN)
            a = max(born, START_DAY * 1440)
            if a >= end:
                continue
            segs = [(int(x.split(":")[0]), int(x.split(":")[1])) for x in row.segs.split("|")]
            mins = np.arange(a, end)
            price = np.empty(len(mins), np.int64)
            for i, (sm, px) in enumerate(segs):
                nxt = segs[i + 1][0] if i + 1 < len(segs) else N_MIN + 1
                msk = (mins >= sm) & (mins < nxt)
                price[msk] = s * px
            hi = H[mins]
            valid = m["o"][mins] != 0
            ph = H[mins - 1]
            pv = m["o"][mins - 1] != 0
            prev_below = ~pv | (ph < price)
            ev = valid & (hi >= price) & prev_below & pool[mins]
            if not ev.any():
                continue
            sg = sig_by_level.get(int(row.level), np.empty(0, np.int64))
            for tm, lp in zip(mins[ev], price[ev]):
                with_sig = bool(((sg >= tm - 60) & (sg <= tm)).any()) if len(sg) else False
                op = int(O[tm])
                dist_open = (lp - op) / abs(lp)
                cls = "gap_above" if op >= lp else ("jump" if dist_open * SCALE > E_E8 else "in_zone")
                out.append({"side": side, "sym": sym, "level": int(row.level), "t": int(tm), "with_signal": with_sig, "open_class": cls})


_W: Worker | None = None
_CFG = None


def _init(cfg):
    global _W, _CFG
    _CFG = cfg
    _W = Worker(cfg)


def _job(sym):
    t0 = time.time()
    r = _W.process(sym)
    r[4]["sec"] = time.time() - t0
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("eng")
    ap.add_argument("work")
    ap.add_argument("--coins", default="")
    ap.add_argument("--procs", type=int, default=6)
    a = ap.parse_args()
    os.makedirs(a.work, exist_ok=True)
    t0 = time.time()
    cfg = {"sigs": {v: load_sigsx(a.eng, v) for v in ("b0", "b0p0")}, "levels": {}}
    for side, s in SIDES.items():
        lv = read_levels_csv(f"{a.eng}/b0/{side}-levels.csv")
        for sym, g in lv.groupby("sym"):
            cfg["levels"][(sym, s)] = g.reset_index(drop=True)
    cfg["traded"] = {}
    for src in (f"{a.eng}/b0", f"{E16}/ow_e"):
        for side in SIDES:
            tr = pd.read_csv(f"{src}/{side}-trades.csv", usecols=["sym", "sig_ms"])
            for sym, g in tr.groupby("sym"):
                cfg["traded"].setdefault((sym, side), set()).update(int(x) for x in g["sig_ms"])
    coins = sorted({k[0] for v in cfg["sigs"].values() for k in v})
    if a.coins:
        coins = [c for c in coins if c in a.coins.split(",")]
    print(f"монет с сигналами {len(coins)}; загрузка {time.time() - t0:.0f} с", flush=True)
    sig_rows, ep_rows, cross_rows, trade_rows, stats = [], [], [], [], []
    with Pool(a.procs, initializer=_init, initargs=(cfg,)) as pool_:
        for i, (sr, er, cr, tr_, st) in enumerate(pool_.imap_unordered(_job, coins, chunksize=1)):
            sig_rows += sr
            ep_rows += er
            cross_rows += cr
            trade_rows += tr_
            stats.append(st)
            if (i + 1) % 25 == 0:
                print(f"{i + 1}/{len(coins)} монет, {time.time() - t0:.0f} с", flush=True)
    sig = pd.DataFrame(sig_rows)
    ep = pd.DataFrame(ep_rows)
    cr = pd.DataFrame(cross_rows)
    pd.DataFrame(trade_rows).to_parquet(f"{a.work}/trade_feats.parquet")
    sig.to_parquet(f"{a.work}/signals_all.parquet")
    ep.to_parquet(f"{a.work}/episodes_raw.parquet")
    cr.to_csv(f"{a.work}/crossings.csv.gz", index=False)
    tot = {k: int(sum(s_[k] for s_ in stats)) for k in ("locate_fail", "natr_mismatch", "pool_violation", "sigs", "unsorted_days")}
    tot["episodes"] = int(len(ep))
    tot["seconds"] = time.time() - t0
    json.dump({"total": tot, "by_coin": stats}, open(f"{a.work}/run_stats.json", "w"), ensure_ascii=False)
    print(json.dumps(tot, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
