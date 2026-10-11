#!/usr/bin/env python3
"""TK-120: символы сделок R2-B и B1 для coin-C2 (§9 п.3). Читает на calc деревья r-b/ (R2-B) и b1-b/ (B1 v171c) + closes cap0, сопоставляет close->строка rounds
по (форма, exit_ms, $ ±6e-4) как p12-coin-c2.py; печатает csv month,form,symbol,t0_ms,exit_ms,usd (только сделки, вошедшие в closes). Только чтение, В-213.
Запуск на calc через alsched submit: python3 p12-r2b-coinrows.py > coin-rows-r2b.csv"""
import csv, glob, json, sys
from collections import defaultdict
O = "/data/p12r3a"
w = csv.writer(sys.stdout, lineterminator="\n"); w.writerow(["month", "form", "symbol", "t0_ms", "exit_ms", "usd"])
for m in [f"2026-{i:02d}" for i in range(1, 10)]:
    for tree, closes, tag in ((f"{O}/r-b/{m}", f"{O}/closes-r2bd-cap0-{m}.json", None), (f"{O}/b1-b/{m}", f"{O}/closes-b1-cap0-{m}.json", "B1")):
        pool = defaultdict(list)   # (форма, exit_ms) -> [(usd, sym, t0)]
        for f in glob.glob(f"{tree}/*/t-bid-btc4h-q1/rounds.csv"):
            for r in csv.DictReader(l for l in open(f, encoding="utf-8") if not l.startswith("#")):
                usd = float(r["qty"]) * float(r["entry_vwap"]) * float(r["net_bps"]) / 1e4
                pool[(r["form"], int(r["exit_ns"]) // 1000000)].append((usd, r["symbol"], int(r["t0_ns"]) // 1000000))
        j = json.load(open(closes))
        for form, per in j.items():
            short = form.split("@", 1)[1]
            for l in (l for p in per.values() for l in p.values()):
                for ms, v in l:
                    c = pool.get((short, int(ms)), [])
                    h = min(range(len(c)), key=lambda i: abs(c[i][0] - float(v))) if c else None
                    if h is None or abs(c[h][0] - float(v)) > 6e-4:
                        sys.exit(f"нет пары {m} {short} {ms} {v}")
                    usd, sym, t0 = c.pop(h)
                    w.writerow([m, tag or short, sym, t0, ms, f"{usd:.6f}"])
