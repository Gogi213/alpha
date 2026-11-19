#!/usr/bin/env python3
"""П-42 §13.32 (TK-625): события пробоев уровней по эпизодам U0 одного окна — пересечение уровня и «минимальный успех» (≥ m·NATR за уровнем раньше возврата к уровню). srv.

Использование (окружение окна обязательно): SIGFEAT_BASE_DATE=<T0 окна> SIGFEAT_DAYS=80 python3 tk625_events.py --t0 <T0> --eng <TK-604 eng/окно> --cache <кэш .b42>
    --trades <каталог aggTrades> --lo <начало зачёта> --hi <конец зачёта> --out <events.csv> [--procs 4]
Эпизоды и их границы — как `tk604_episodes.py` (тот же дамп `b0p0`, те же правила нового эпизода; код эпизода скопирован без изменения). Меняется только то, что считается по тикам эпизода:
  пересечение — первая сделка со временем в (sig, sig + 60 мин] и ценой строго за уровнем (длинная рама: P > R); итог при m ∈ {0,5; 1; 2} NATR (NATR сигнала, `natr_e8`):
  `o` (успех) — раньше возврата к уровню (P ≤ R) цена дошла до R + ⌈m·|R|·natr⌉; `f` — раньше успеха цена вернулась к уровню; `s` — за 60 мин после пересечения ни то ни другое.
  Момент исхода: успех / возврат — время сделки; `s` — пересечение + 60 мин. Событие без пересечения — `cross_ms` пусто (подход, не пробой).
`ok_ev = 1`, если все нужные сутки сделок на месте (до sig + 60 мин без пересечения, до пересечения + 60 мин с пересечением) и NATR > 0.
Пишет CSV (столбцы `EV_COLS`) и `<out>.stats.json`. Изоляция: окно и дата зачёта не позже 13.11 (G6).
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
import tk604_stats as st  # noqa: E402

SIDES = {"long": 1, "short": -1}
MS = (("05", 1, 2), ("10", 1, 1), ("20", 2, 1))        # (имя, числитель, знаменатель): m = num/den NATR
T_APPROACH_MS = 60 * L.MIN_MS                           # пересечение ищется в 60 мин после сигнала подхода
T_AFTER_MS = 60 * L.MIN_MS                              # исход ищется в 60 мин после пересечения
EV_COLS = ["side", "sym", "level", "sig_ms", "indep", "ok_ev", "cross_ms", "natr_e8", "lp_e8", "locate_fail",
           "r05", "t05", "r10", "t10", "r20", "t20"]
G = {}


def load_sigs(eng, variant, columns=None):
    out = {}
    for side, s in SIDES.items():
        df = L.read_sigsx(f"{eng}/{variant}/{side}-sigsx.csv", usecols=columns)
        for sym, g in df.groupby("sym"):
            out[(sym, s)] = g.sort_values(["level", "sig_ms"]).reset_index(drop=True)
    return out


def minmax(a):
    return (None, None) if len(a) == 0 else (int(a.max()), int(a.min()))


def covered(tk, a, b):
    """Все сутки [a, b] в окне есть среди сделок и b внутри сетки."""
    if a < 0 or b > L.END_MS - 1:
        return False
    return all(tk.day(d) is not None for d in range(a // L.DAY_MS, b // L.DAY_MS + 1))


def event_outcomes(Tw, Pw, R, natr_e8, n_ap):
    """Пересечение и исходы по сделкам после сигнальной (Tw — время, Pw — цена в длинной раме, R — уровень в раме; n_ap — число сделок среза в окне подхода).
    Возвращает (t_cross | None, {имя: (код, момент исхода)})."""
    cr = Pw[:n_ap] > R
    if not cr.any():
        return None, {}
    c = int(cr.argmax())
    t_cross = int(Tw[c])
    end = int(np.searchsorted(Tw, t_cross + T_AFTER_MS, "right"))
    Pa, Ta = Pw[c + 1:end], Tw[c + 1:end]
    ret = Pa <= R
    ir = int(ret.argmax()) if ret.any() else None
    absR = abs(R)
    res = {}
    for name, num, den in MS:
        x = L.ceil_mul(absR, num * natr_e8, den * L.SCALE)
        ge = Pa >= R + x
        ix = int(ge.argmax()) if ge.any() else None
        if ix is not None and (ir is None or ix < ir):
            res[name] = ("o", int(Ta[ix]))
        elif ir is not None:
            res[name] = ("f", int(Ta[ir]))
        else:
            res[name] = ("s", t_cross + T_AFTER_MS)
    return t_cross, res


def process(sym):
    """События монеты (обе стороны, все эпизоды зачёта): список строк словарём и счётчики. Логика эпизодов — `tk604_episodes.process`."""
    cfg = G
    lo_rel, hi_rel = cfg["lo_rel"], cfg["hi_rel"]
    stt = dict(sym=sym, signals=0, episodes=0, locate_fail=0, not_ok=0, crossed=0)
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
        row = dict(side=side, sym=sym, level=level, sig_ms=sig_ms, indep=int(gap != gap or gap > 60), ok_ev=0, cross_ms="", natr_e8=natr_e8,
                   lp_e8=abs(R), locate_fail=0, r05="", t05="", r10="", t10="", r20="", t20="")
        tt, pp, _ = tk.span(sig_ms, sig_ms + T_APPROACH_MS + T_AFTER_MS)
        P2 = s * pp
        k2 = L.find_signal_tick(tt, P2, sig_ms, R, dist)
        if k2 is None:
            stt["locate_fail"] += 1
            row["locate_fail"] = 1
            rows.append(row)
            continue
        if natr_e8 <= 0:
            stt["not_ok"] += 1
            rows.append(row)
            continue
        Tw, Pw = tt[k2 + 1:], P2[k2 + 1:]
        n_ap = int(np.searchsorted(Tw, sig_ms + T_APPROACH_MS, "right"))
        t_cross, res = event_outcomes(Tw, Pw, R, natr_e8, n_ap)
        need_to = (t_cross if t_cross is not None else sig_ms) + (T_AFTER_MS if t_cross is not None else T_APPROACH_MS)
        if not covered(tk, sig_ms, need_to):
            stt["not_ok"] += 1
            rows.append(row)
            continue
        row["ok_ev"] = 1
        if t_cross is not None:
            stt["crossed"] += 1
            row["cross_ms"] = t_cross
            for name, (code, tres) in res.items():
                row["r" + name], row["t" + name] = code, tres
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
    ap.add_argument("--syms", default="", help="только отладка: список монет через запятую")
    a = ap.parse_args()
    if L.BASE_DATE != a.t0 or L.DAYS_TOTAL != 80:
        sys.exit("SIGFEAT_BASE_DATE=%s SIGFEAT_DAYS=%d ≠ --t0 %s / 80" % (L.BASE_DATE, L.DAYS_TOTAL, a.t0))
    t0, lo, hi = (datetime.date.fromisoformat(x) for x in (a.t0, a.lo, a.hi))
    st.check_dates(a.t0, a.hi)
    t = time.time()
    G.update(cache=a.cache, trades=a.trades, lo_rel=(lo - t0).days * L.DAY_MS, hi_rel=((hi - t0).days + 1) * L.DAY_MS - 1, u0=load_sigs(a.eng, "b0p0"))
    syms = sorted({k[0] for k in G["u0"]})
    if a.syms:
        syms = [x for x in syms if x in a.syms.split(",")]
    print("монет %d, загрузка дампов %.0f с" % (len(syms), time.time() - t), flush=True)
    rows, tot = [], dict(signals=0, episodes=0, locate_fail=0, not_ok=0, crossed=0, unsorted_days=0)
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
        f.write(",".join(EV_COLS) + "\n")
        for r in rows:
            f.write(",".join(str(r[c]) for c in EV_COLS) + "\n")
    os.replace(tmp, a.out)
    tot.update(rows=len(rows), indep=sum(r["indep"] for r in rows), ok_ev=sum(r["ok_ev"] for r in rows), seconds=round(time.time() - t))
    with open(a.out + ".stats.json", "w", encoding="utf-8") as f:
        json.dump(tot, f, ensure_ascii=False, indent=1)
    print(json.dumps(tot, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
