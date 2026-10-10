#!/usr/bin/env python3
"""TK-004 H1 (П-07): можно ли взять `frontrun_lots_at_arm` из кэша подходов (`study/approaches/D20/<день>/
approaches-<SYM>.csv`) к сигналам базы Г-85б без движка. Условие Судьи (TK-004, 27.09 23:31): ключ точный —
(монета, сутки, цена стены `price_tick`, `arm_ms` == t0 сигнала) и соединились 100 % сигналов базы; доля фронтрана —
`frontrun_lots_at_arm / size_at_arm` того же взвода. Пишет tmp-p07/h1-join.json: число сигналов, несоединённых,
неоднозначных (> 1 подхода на ключ) и признак по каждому сигналу (для порогов p25/p50/p75 — отдельно, до счёта оси).

    python3 p07-h1-join.py --cell p07b-base --out tmp-p07/h1-join.json
"""
import argparse
import csv
import glob
import json
import os

A = os.path.expanduser("~/alpha")
HOMES = {"aug": f"{A}/epochs/e-aug", "hist": f"{A}/tmp-lsk0914.used-20260926/home", "rec": f"{A}/tmp-t29/rec"}
SET_ = "t-bid-btc4h-q1"


def rows(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(line for line in f if not line.startswith("#")))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cell", default="p07b-base")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    n = unmatched = ambiguous = 0
    miss_examples, feats = [], []
    for tag, home in HOMES.items():
        for sig_path in sorted(glob.glob(f"{home}/b5/{a.cell}/20*/{SET_}/signals.csv")):
            day = sig_path.split("/")[-3]
            cache = {}
            for s in rows(sig_path):
                n += 1
                sym = s["symbol"]
                if sym not in cache:
                    p = f"{home}/study/approaches/D20/{day}/approaches-{sym}.csv"
                    idx = {}
                    if os.path.exists(p):
                        for r in rows(p):
                            if r["side"] == "bid":
                                idx.setdefault((r["price_tick"], r["arm_ms"]), []).append(r)
                    cache[sym] = idx
                key = (s["price_tick"], str(int(s["t0_ns"]) // 1_000_000))
                hit = cache[sym].get(key, [])
                if not hit:
                    unmatched += 1
                    if len(miss_examples) < 10:
                        miss_examples.append([tag, day, sym, s["signal_index"], s["t0_ns"], s["price_tick"]])
                    continue
                if len(hit) > 1:
                    ambiguous += 1
                r = hit[0]
                size = float(r["size_at_arm"] or 0)
                fr = float(r["frontrun_lots_at_arm"] or 0)
                feats.append([tag, day, sym, s["signal_index"], r["approach_index"], fr, size,
                              round(fr / size, 6) if size > 0 else None])
    out = {"cell": a.cell, "n_signals": n, "unmatched": unmatched, "ambiguous": ambiguous,
           "miss_examples": miss_examples,
           "columns": ["home", "day", "symbol", "signal_index", "approach_index", "frontrun_lots_at_arm",
                       "size_at_arm", "frontrun_share"],
           "rows": feats}
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f)
    print(f"signals {n} unmatched {unmatched} ambiguous {ambiguous}")


if __name__ == "__main__":
    main()
