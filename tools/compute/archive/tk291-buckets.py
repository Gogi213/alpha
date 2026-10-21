#!/usr/bin/env python3
"""TK-291 (П-19 §7 п.2): заселённость корзин номера касания на B1, половина А (sha256(symbol) чётный), по месяцам.
tk291-buckets.py <B1 out> <txt-выход>   — B1 out = /data/tk291/out (rounds.csv набора t-bid-btc4h-q1 с touch_index).
Корзины 0 / 1 / 2 / ≥3; корзина «заселена», если ≥ 10 сделок B1 в ≥ 6 из 9 месяцев. Печать: сделок по месяцам, число месяцев ≥ 10,
вердикт (слияние ≥3 ← 2 ← … по §7 п.2 — если корзина не прошла). Код выхода 0."""
import collections
import csv
import glob
import hashlib
import os
import sys

FORM = "ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800"
root, out = sys.argv[1:3]
isA = lambda s: int(hashlib.sha256(s.encode()).hexdigest(), 16) % 2 == 0
cnt = {h: collections.defaultdict(lambda: [0, 0, 0, 0]) for h in ("A", "B")}
for p in sorted(glob.glob(os.path.join(root, "run-*", "out", "*", "t-bid-btc4h-q1", "rounds.csv"))):
    mon = p.split(os.sep)[-3][:7]
    with open(p, newline="", encoding="utf-8") as f:
        lines = [l.rstrip("\r\n") for l in f if not l.startswith("#")]
    hd = lines[0].split(",")
    c = {n: hd.index(n) for n in ("symbol", "form", "touch_index")}
    for r in csv.reader(lines[1:]):
        if r[c["form"]] != FORM:
            continue
        cnt["A" if isA(r[c["symbol"]]) else "B"][mon][min(int(r[c["touch_index"]]), 3)] += 1
months = sorted(set(cnt["A"]) | set(cnt["B"]))
res = []
for half in ("A", "B"):
    res.append(f"половина {half}: сделок B1 по корзинам [n=0, 1, 2, ≥3]")
    for m in months:
        res.append(f"  {m}  {cnt[half][m]}  всего {sum(cnt[half][m])}")
    for b, name in enumerate(("0", "1", "2", "≥3")):
        ok = sum(1 for m in months if cnt[half][m][b] >= 10)
        res.append(f"  корзина {name}: месяцев с ≥10 сделок {ok} из {len(months)} → " + ("заселена" if ok >= 6 else "НЕ заселена"))
open(out, "w", encoding="utf-8").write("\n".join(res) + "\n")
print("\n".join(res))
