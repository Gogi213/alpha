#!/usr/bin/env python3
"""П-42 §13.54 (TK-654): эпизоды U0 (независимые подходы, метка `y` = O3 1 % / 60 мин) для части монет — обёртка `tk604_episodes.py` без изменения логики. srv.

Использование (окружение окна обязательно): SIGFEAT_BASE_DATE=<T0 окна> SIGFEAT_DAYS=80 python3 tk654_episodes.py --t0 <T0> --eng <out движка окна> --cache <кэш .b42>
    --trades <каталог aggTrades> --lo <начало зачёта> --hi <конец зачёта> --syms <файл монет> --out <episodes.csv> [--procs 4]
Отличия от `tk604_episodes.main`: (1) обходятся только монеты из `--syms`; (2) дампа `b0` у деревьев TK-625 h775 нет — `in_pool` = 0 у всех строк (в признак `bk` не входит).
Эпизод, метка, `indep`, `ok_t60` — `tk604_episodes.process` как есть. Пишет CSV (столбцы `tk604_feat.EP_COLS`) и `<out>.stats.json`.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import time
from multiprocessing import Pool

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk604_episodes as E  # noqa: E402  (импорт `sigfeat_lib` читает SIGFEAT_BASE_DATE / SIGFEAT_DAYS)
import tk604_feat as tf  # noqa: E402
import tk604_stats as st  # noqa: E402

L = E.L


def read_syms(path):
    return sorted({x for x in open(path, encoding="utf-8").read().replace("\n", ",").split(",") if x})


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    for k in ("--t0", "--eng", "--cache", "--trades", "--lo", "--hi", "--syms", "--out"):
        ap.add_argument(k, required=True)
    ap.add_argument("--procs", type=int, default=4)
    a = ap.parse_args()
    if L.BASE_DATE != a.t0 or L.DAYS_TOTAL != 80:
        sys.exit("SIGFEAT_BASE_DATE=%s SIGFEAT_DAYS=%d ≠ --t0 %s / 80" % (L.BASE_DATE, L.DAYS_TOTAL, a.t0))
    t0, lo, hi = (datetime.date.fromisoformat(x) for x in (a.t0, a.lo, a.hi))
    st.check_dates(a.t0, a.hi)
    want = set(read_syms(a.syms))
    t = time.time()
    u0 = {k: v for k, v in E.load_sigs(a.eng, "b0p0").items() if k[0] in want}
    E.G.update(cache=a.cache, trades=a.trades, lo_rel=(lo - t0).days * L.DAY_MS, hi_rel=((hi - t0).days + 1) * L.DAY_MS - 1, u0=u0, b0={})
    syms = sorted({k[0] for k in u0})
    print("монет в дампе из списка %d (в списке %d), загрузка %.0f с" % (len(syms), len(want), time.time() - t), flush=True)
    rows, tot = [], dict(signals=0, episodes=0, locate_fail=0, not_ok=0, unsorted_days=0)
    with Pool(a.procs) as pool:
        for i, (r, s_) in enumerate(pool.imap_unordered(E.process, syms, chunksize=1), 1):
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
    tot.update(coins=len(syms), rows=len(rows), indep=sum(r["indep"] for r in rows), labelled=sum(1 for r in rows if r["y"] != ""), seconds=round(time.time() - t))
    with open(a.out + ".stats.json", "w", encoding="utf-8") as f:
        json.dump(tot, f, ensure_ascii=False, indent=1)
    print(json.dumps(tot, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
