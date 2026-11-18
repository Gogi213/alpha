#!/usr/bin/env python3
"""П-42 §13.26 (TK-604): тонкая таблица эпизодов U0 одного окна — только независимые подходы и метка `y` (`o_x1_t60_f01` TK-594) — для истории признака пробиваемости монеты. srv.

Использование (окружение окна обязательно): SIGFEAT_BASE_DATE=<T0 окна> SIGFEAT_DAYS=80 python3 tk604_episodes.py --t0 <T0> --eng <out движка окна> --cache <кэш .b42>
    --trades <каталог aggTrades> --lo <начало зачёта> --hi <конец зачёта> --out <episodes.csv> [--procs 4]
Читает `<eng>/b0p0/{long,short}-sigsx.csv` (U0: все правила и пул выкл., зона E = 0,3 %) и `<eng>/b0/…` (флаг `in_pool`). Логика эпизода и метки — `sigfeat_run.Worker.do_universe` /
`Worker.episode` (TK-588/594) без изменения правил; отличия — только порядок обхода и объём: сигналы обходятся по времени (один проход по суткам aggTrades на монету), у каждого сигнала
из тиков его минуты хранятся лишь max/min «головы» и «хвоста» (`any(>= R)` и `any(<= P_lo)` эквивалентны max/min), признаки/контроли TK-588 не считаются. `indep` — как `sigfeat_join.add_gap`
(предыдущего сигнала того же уровня нет или разрыв > 60 мин). Метка — только у первого сигнала эпизода в зачётном диапазоне окна; у уровня берётся последний сигнал ДО диапазона как предшественник.
Пишет CSV (столбцы `tk604_feat.EP_COLS`) и `<out>.stats.json`. Изоляция: окно и дата зачёта не позже 13.11 (G6).
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import time
from multiprocessing import Pool

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sigfeat_lib as L  # noqa: E402  (читает SIGFEAT_BASE_DATE / SIGFEAT_DAYS при импорте)
import tk604_feat as tf  # noqa: E402
import tk604_stats as st  # noqa: E402

SIDES = {"long": 1, "short": -1}
G = {}                                          # конфигурация процесса (fork)


def load_sigs(eng, variant, columns=None):
    """{(sym, s): DataFrame сигналов стороны}, строки по (level, sig_ms)."""
    out = {}
    for side, s in SIDES.items():
        df = L.read_sigsx(f"{eng}/{variant}/{side}-sigsx.csv", usecols=columns)
        for sym, g in df.groupby("sym"):
            out[(sym, s)] = g.sort_values(["level", "sig_ms"]).reset_index(drop=True)
    return out


def ok_t60(tk, sig_ms):
    """`ok_t60` как `Worker.episode.ok_T(60)`: окно метки целиком в сетке и все сутки окна есть среди сделок."""
    if sig_ms + 60 * L.MIN_MS > L.END_MS - 1:
        return False
    return all(tk.day(d) is not None for d in range(sig_ms // L.DAY_MS, (sig_ms + 60 * L.MIN_MS) // L.DAY_MS + 1))


def minmax(a):
    return (None, None) if len(a) == 0 else (int(a.max()), int(a.min()))


def process(sym):
    """Эпизоды монеты (обе стороны): список строк словарём и счётчики."""
    cfg = G
    lo_rel, hi_rel = cfg["lo_rel"], cfg["hi_rel"]
    stt = dict(sym=sym, signals=0, episodes=0, locate_fail=0, not_ok=0)
    items = []
    for side, s in SIDES.items():
        df = cfg["u0"].get((sym, s))
        if df is None or df.empty:
            continue
        df = df.copy()
        df["gap"] = df.groupby("level")["sig_ms"].diff() / L.MIN_MS
        before = df[df["sig_ms"] < lo_rel].groupby("level").tail(1)
        inr = df[(df["sig_ms"] >= lo_rel) & (df["sig_ms"] <= hi_rel)]
        for emit, part in ((False, before), (True, inr)):
            for r in part.itertuples(index=False):
                items.append((int(r.sig_ms), side, s, int(r.level), int(r.dist_e8), int(r.level_px_e8), int(r.natr_e8), float(r.gap), emit))
    if not items:
        return [], stt
    items.sort(key=lambda x: (x[0], x[1], x[3]))
    m = L.load_b42(f"{cfg['cache']}/{sym}.b42")
    tk = L.Ticks(cfg["trades"], sym, keep=4)
    pool = cfg["b0"]
    pool_keys = {(side, int(ms)) for side in SIDES for ms in (pool[(sym, SIDES[side])]["sig_ms"].tolist() if (sym, SIDES[side]) in pool else [])}
    state = {}
    rows = []
    for sig_ms, side, s, level, dist, lpx, natr_e8, gap, emit in items:
        R = s * lpx
        t_min = sig_ms // L.MIN_MS
        t_arr, p_arr, _ = tk.span(t_min * L.MIN_MS, t_min * L.MIN_MS + L.MIN_MS - 1)
        P = s * p_arr
        k = L.find_signal_tick(t_arr, P, sig_ms, R, dist)
        if k is None:
            head_hi = head_lo = tail_hi = tail_lo = None
        else:
            head_hi, head_lo = minmax(P[:k])
            tail_hi, tail_lo = minmax(P[k + 1:])
        new_ep = True
        prev = state.get((s, level))
        if prev is not None:
            p_lo = R - abs(R) * (L.E_E8 + 1) / L.SCALE
            away = bool((prev[1] is not None and prev[1] >= R) or (prev[2] is not None and prev[2] <= p_lo)
                        or (head_hi is not None and head_hi >= R) or (head_lo is not None and head_lo <= p_lo))
            if not away and t_min - prev[0] > 1:
                a, b = prev[0] + 1, t_min
                H, Lw, _, _ = L.long_frame(m, s)
                dat = m["o"][a:b] != 0
                if dat.any():
                    away = bool((H[a:b][dat] >= R).any() or (Lw[a:b][dat] <= p_lo).any())
            new_ep = away
        state[(s, level)] = (t_min, tail_hi, tail_lo)
        stt["signals"] += 1
        if not (emit and new_ep):
            continue
        stt["episodes"] += 1
        row = dict(side=side, sym=sym, level=level, sig_ms=sig_ms, indep=int(gap != gap or gap > 60), ok_t60=0, y="", y_n="", natr_e8=natr_e8,
                   lp_e8=abs(R), in_pool=int((side, sig_ms) in pool_keys), locate_fail=0)
        tt, pp, _ = tk.span(sig_ms, sig_ms + 60 * L.MIN_MS)
        P2 = s * pp
        k2 = L.find_signal_tick(tt, P2, sig_ms, R, dist)
        if k2 is None:
            stt["locate_fail"] += 1
            row["locate_fail"] = 1
        elif ok_t60(tk, sig_ms):
            absR = abs(R)
            Pw = P2[k2 + 1:]
            f_floor = absR * 1000 // 1_000_000
            row["ok_t60"] = 1
            row["y"] = int(L.outcome(Pw, R, L.ceil_mul(absR, 10000, 1_000_000), f_floor)[0] == L.O3)
            if natr_e8 > 0:
                row["y_n"] = int(L.outcome(Pw, R, -(-(absR * natr_e8) // L.SCALE), f_floor)[0] == L.O3)
        else:
            stt["not_ok"] += 1
        rows.append(row)
    stt["unsorted_days"] = tk.unsorted_days
    return rows, stt


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--t0", required=True)
    ap.add_argument("--eng", required=True)
    ap.add_argument("--cache", required=True)
    ap.add_argument("--trades", required=True)
    ap.add_argument("--lo", required=True)
    ap.add_argument("--hi", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--procs", type=int, default=4)
    a = ap.parse_args()
    if L.BASE_DATE != a.t0 or L.DAYS_TOTAL != 80:
        sys.exit("SIGFEAT_BASE_DATE=%s SIGFEAT_DAYS=%d ≠ --t0 %s / 80" % (L.BASE_DATE, L.DAYS_TOTAL, a.t0))
    t0, lo, hi = (datetime.date.fromisoformat(x) for x in (a.t0, a.lo, a.hi))
    st.check_dates(a.t0, a.hi)
    t = time.time()
    G.update(cache=a.cache, trades=a.trades, lo_rel=(lo - t0).days * L.DAY_MS, hi_rel=((hi - t0).days + 1) * L.DAY_MS - 1,
             u0=load_sigs(a.eng, "b0p0"), b0=load_sigs(a.eng, "b0", ["sym", "level", "sig_ms"]))
    syms = sorted({k[0] for k in G["u0"]})
    print("монет %d, загрузка дампов %.0f с" % (len(syms), time.time() - t), flush=True)
    rows, tot = [], dict(signals=0, episodes=0, locate_fail=0, not_ok=0, unsorted_days=0)
    with Pool(a.procs) as pool:
        for i, (r, s_) in enumerate(pool.imap_unordered(process, syms, chunksize=1), 1):
            rows += r
            for k in tot:
                tot[k] += s_.get(k, 0)
            if i % 50 == 0:
                print("  %d/%d монет, эпизодов %d, %.0f с" % (i, len(syms), len(rows), time.time() - t), flush=True)
    rows.sort(key=lambda r: (r["side"], r["sym"], r["sig_ms"], r["level"]))
    tmp = a.out + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(",".join(tf.EP_COLS) + "\n")
        for r in rows:
            f.write(",".join(str(r[c]) for c in tf.EP_COLS) + "\n")
    os.replace(tmp, a.out)
    tot.update(rows=len(rows), indep=sum(r["indep"] for r in rows), labelled=sum(1 for r in rows if r["y"] != ""), seconds=round(time.time() - t))
    with open(a.out + ".stats.json", "w", encoding="utf-8") as f:
        json.dump(tot, f, ensure_ascii=False, indent=1)
    print(json.dumps(tot, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
