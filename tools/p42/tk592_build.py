"""П-42 §13.18 (TK-592): таблица анализа — U0 независимые подходы + `zv6h` + контроль с состоянием пула эпизода (метка с глубиной и строгая).

Использование (srv, venv не нужен): SIGFEAT_DAYS=74 python3 tk592_build.py <episodes.parquet> <out.parquet> [--cache DIR] [--trades DIR] [--procs 6]
                                    [--coins A,B] [--min-day N] [--max-day N] [--no-ctrl]
Берёт эпизоды `univ == 'U0'`, `indep == 1`, `ok_t60` (`--univ U1` — все эпизоды пула, зерно как в §13.17); добавляет `zv2h`, `zv6h` (sparse_features, исправление по проверке TK-588 П1) и контроль:
до 20 случайных минут той же монеты в те же UTC-сутки, **состояние пула = состоянию эпизода** (`in_pool`), окно не пересекается с ±60 мин вокруг
первого и последнего сигналов эпизода, псевдоуровень на `dist` эпизода от цены первой сделки минуты (генератор §13.17, зерно 588).
`c_f` — доля O3 с возвратом глубже f = 0,1 %, `c_s` — строгая; `c_n` — число действительных окон. Для отложенных суток: тот же код (`SIGFEAT_DAYS=78`).
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
from sigfeat_lib import (DAY_MS, END_MS, MIN_MS, O3, SCALE, START_DAY, Ticks, ceil_mul, ffill_close, load_b42, natr_series,  # noqa: E402
                         outcome, sparse_features)
from sigfeat_run import pool_mask  # noqa: E402

NCTRL = 20
F_PPM = 1000  # f = 0,1 %
X_PPM = 10_000  # X = 1 %
MIN_C = 5


def ctrl_candidates(m, natr, pool, day, t_first, t_last, in_pool: bool):
    lo, hi = max(day * 1440, START_DAY * 1440), (day + 1) * 1440
    cand = np.arange(lo + 1, hi)
    ok = (m["o"][cand] != 0) & (natr[cand - 1] >= 0)
    ok &= pool[cand] if in_pool else ~pool[cand]
    ok &= (np.abs(cand - t_first) > 60) & (np.abs(cand - t_last) > 60)
    return cand[ok]


def rng_for(sym: str, s: int, ep_no: int, sig_ms: int, univ_flag: int = 1):
    base = [588, sum((i + 1) * ord(ch) for i, ch in enumerate(sym)), 0 if s > 0 else 1, univ_flag]  # зерно §13.17: U1 → 0, U0 → 1
    return np.random.default_rng(base + [ep_no, int(sig_ms // 1000)])


def one_control(tk, s, mm, dist):
    """Окно контроля: доли исходов (strict, depth) или None, если окно недействительно."""
    a, b = int(mm) * MIN_MS, int(mm) * MIN_MS + 61 * MIN_MS
    if b > END_MS - 1:
        return None
    t, p, full = tk.span(a, b)
    if not full or len(t) == 0 or t[0] >= a + MIN_MS:
        return None
    P = s * p
    P0 = int(P[0])
    R = P0 * SCALE // (SCALE - s * dist)
    aR = abs(R)
    end = int(np.searchsorted(t, int(t[0]) + 60 * MIN_MS, "right"))
    Pw = P[1:end]
    x_add = ceil_mul(aR, X_PPM, 1_000_000)
    ocs = outcome(Pw, R, x_add, 0)[0] == O3
    ocf = outcome(Pw, R, x_add, aR * F_PPM // 1_000_000)[0] == O3
    return bool(ocs), bool(ocf)


def process_coin(args):
    sym, rows, cache, trades, do_ctrl, univ_flag = args
    m = load_b42(f"{cache}/{sym}.b42")
    first_valid = int(np.argmax(m["o"] != 0)) if (m["o"] != 0).any() else len(m["o"])
    natr = natr_series(m)
    pool, _ = pool_mask(m, natr)
    tk = Ticks(trades, sym, keep=3)
    out = []
    for r in sorted(rows, key=lambda x: x["sig_ms"]):
        t = r["sig_ms"] // MIN_MS
        s = 1 if r["side"] == "long" else -1
        f = sparse_features(m, first_valid, t)
        rec = {"ep_id": r["ep_id"], "zv2h": f["zv2h"], "zv6h": f["zv6h"], "c_n": 0, "c_f": np.nan, "c_s": np.nan}
        if do_ctrl:
            day = r["sig_ms"] // DAY_MS
            cand = ctrl_candidates(m, natr, pool, day, t, r["last_sig_ms"] // MIN_MS, bool(r["in_pool"]))
            if len(cand):
                rng = rng_for(sym, s, int(r["ep_no"]), int(r["sig_ms"]), univ_flag)
                pick = rng.choice(cand, size=min(NCTRL, len(cand)), replace=False)
                n = ks = kf = 0
                for mm in pick:
                    res = one_control(tk, s, mm, int(r["dist_e8"]))
                    if res is None:
                        continue
                    n += 1
                    ks += res[0]
                    kf += res[1]
                if n:
                    rec.update({"c_n": n, "c_f": kf / n, "c_s": ks / n})
        out.append(rec)
    return sym, out, tk.unsorted_days


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("episodes")
    ap.add_argument("out")
    ap.add_argument("--cache", default="/data/p42-t566/cache")
    ap.add_argument("--trades", default="/data/aggtrades/binance")
    ap.add_argument("--procs", type=int, default=6)
    ap.add_argument("--coins", default="")
    ap.add_argument("--min-day", type=int, default=0)
    ap.add_argument("--max-day", type=int, default=10**9)
    ap.add_argument("--no-ctrl", action="store_true")
    ap.add_argument("--univ", default="U0", help="U0: независимые подходы (§13.18); U1: все эпизоды пула (воспроизведение контроля §13.17 + глубина, правки TK-588)")
    a = ap.parse_args()
    t0 = time.time()
    e = pd.read_parquet(a.episodes)
    e = e.drop(columns=[c for c in ("zv2h", "zv6h") if c in e.columns])  # кэш-цепочка отложенных суток уже кладёт zv*; здесь пересчитываются тем же кодом
    sel = (e.univ == a.univ) & e.ok_t60 & e.tick_ok & (e.day >= a.min_day) & (e.day <= a.max_day)
    if a.univ == "U0":
        sel &= e.indep == 1
    e = e[sel].reset_index(drop=True)
    if a.coins:
        e = e[e.sym.isin(a.coins.split(","))].reset_index(drop=True)
    print(f"эпизодов {len(e)}; монет {e.sym.nunique()}", flush=True)
    need = ["ep_id", "sig_ms", "side", "ep_no", "last_sig_ms", "in_pool", "dist_e8"]
    jobs = []
    for sym, g in e.groupby("sym"):
        jobs.append((sym, g[need].to_dict("records"), a.cache, a.trades, not a.no_ctrl, 0 if a.univ == "U1" else 1))
    jobs.sort(key=lambda j: -len(j[1]))
    recs = []
    unsorted = 0
    with Pool(a.procs) as pool_:
        for i, (sym, out, uns) in enumerate(pool_.imap_unordered(process_coin, jobs, chunksize=1)):
            recs += out
            unsorted += uns
            if (i + 1) % 25 == 0:
                print(f"{i + 1}/{len(jobs)} монет, {time.time() - t0:.0f} с", flush=True)
    add = pd.DataFrame(recs)
    res = e.merge(add, on="ep_id", how="left", validate="one_to_one")
    res["y"] = (res["o_x1_t60_f01"] == O3).astype(np.int8)
    res["y_s"] = (res["o_x1_t60"] == O3).astype(np.int8)
    res.to_parquet(a.out)
    info = {"episodes": int(len(res)), "ctrl_ge5": int((res.c_n >= MIN_C).sum()), "unsorted_days": unsorted, "seconds": time.time() - t0,
            "by_side": {k: int(v) for k, v in res.groupby("side").size().items()}, "zv6h_nan": int(res.zv6h.isna().sum())}
    json.dump(info, open(a.out + ".info.json", "w"), ensure_ascii=False)
    print(json.dumps(info, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
