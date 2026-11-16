#!/usr/bin/env python3
"""G-022 pool-fix: полнота сделок по объёму минутных свечей биржи (независимый эталон, когда нет истории сделок по REST: Bybit `recent-trade` — только последние 1000 на символ).

  klinecheck.py <binance|bybit> <корень данных> <t0_ms> --symbols A,B,… [--covered-ms ms] [--window-s 180] [--out file.json]

Для каждого символа и каждой минуты, целиком лежащей в окне после `covered-ms` и `t0 + window` (нет неполных краёв): сумма размеров записанных сделок (`t`, столбец `q`, время сделки
T = приём − lag − ET) против объёма свечи биржи за ту же минуту (Binance `fapi/v1/klines`, Bybit `v5/market/kline`; базовая валюта). Отношение записано / REST; 1,0000 — совпадение.
Числа размеров — строками как пришли; суммы — точные десятичные (`decimal`). Только чтение файлов окна и публичный REST.
"""
import argparse
import glob
import json
import os
import subprocess
import urllib.request
from collections import defaultdict
from decimal import Decimal


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "alpha-collector/klinecheck"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def rest_minutes(ex, sym, a, b):
    """{minute_start_ms: volume} за [a, b)."""
    out = {}
    if ex == "binance":
        for k in get(f"https://fapi.binance.com/fapi/v1/klines?symbol={sym}&interval=1m&startTime={a}&endTime={b - 1}&limit=1500"):
            out[int(k[0])] = Decimal(k[5])
    else:
        r = get(f"https://api.bybit.com/v5/market/kline?category=linear&symbol={sym}&interval=1&start={a}&end={b - 1}&limit=1000")
        for k in r["result"]["list"]:
            out[int(k[0])] = Decimal(k[5])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("exchange", choices=["binance", "bybit"])
    ap.add_argument("root")
    ap.add_argument("t0", type=int)
    ap.add_argument("--symbols", required=True)
    ap.add_argument("--covered-ms", type=int, default=0)
    ap.add_argument("--window-s", type=int, default=180)
    ap.add_argument("--out")
    a = ap.parse_args()
    syms = set(a.symbols.split(","))
    t_end = a.t0 + a.window_s * 1000
    first_min = -(-max(a.covered_ms, a.t0) // 60000) * 60000     # первая минута, целиком после покрытия
    mins = [m for m in range(first_min, t_end, 60000) if m + 60000 <= t_end]
    got = defaultdict(Decimal)
    for f in sorted(glob.glob(os.path.join(a.root, "20*", "*.tsv.zst"))):
        t = 0
        p = subprocess.run(["zstd", "-dc", f], capture_output=True)
        for ln in p.stdout.decode("utf-8", "replace").split("\n"):
            if not ln:
                continue
            r = ln.split("\t")
            try:
                t += int(r[1])
            except (ValueError, IndexError):
                continue
            if r[0] != "t" or len(r) < 8 or r[4] not in syms:
                continue
            try:
                trade_ms = t // 1_000_000 - int(float(r[2])) - int(float(r[3]))   # приём − lag − ET
                m = trade_ms // 60000 * 60000
                if m in mins:
                    got[(r[4], m)] += Decimal(r[7])
            except (ValueError, ArithmeticError):
                continue
    res, tot_rec, tot_rest = [], Decimal(0), Decimal(0)
    for s in sorted(syms):
        if not mins:
            break
        rest = rest_minutes(a.exchange, s, mins[0], mins[-1] + 60000)
        for m in mins:
            rv, gv = rest.get(m), got.get((s, m), Decimal(0))
            if rv is None:
                continue
            res.append({"symbol": s, "minute_ms": m, "rest": str(rv), "recorded": str(gv), "ratio": (float(gv / rv) if rv else None)})
            tot_rec += gv
            tot_rest += rv
    out = {"exchange": a.exchange, "t0_ms": a.t0, "minutes": len(mins), "cells": len(res), "recorded": str(tot_rec), "rest": str(tot_rest),
           "ratio_total": float(tot_rec / tot_rest) if tot_rest else None,
           "cells_off_by_more_than_1pct": sum(1 for x in res if x["ratio"] is not None and abs(x["ratio"] - 1) > 0.01), "per_cell": res}
    print(json.dumps({k: v for k, v in out.items() if k != "per_cell"}))
    if a.out:
        json.dump(out, open(a.out, "w"))


if __name__ == "__main__":
    main()
