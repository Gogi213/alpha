#!/usr/bin/env python3
"""TK-359 гейт (в): Σ tb_usd / ts_usd / nb / ns ряда `lob seconds` = floor(точная сумма) по `lob trades` тех же суток.
Номинал сделки — price_tick·tick_e9·qty_lots·step_e9 (10⁻¹⁸ $), tick/step — из заголовка бинлога (offset 5/13, LE i64);
берутся сделки без block/rpi, цена и объём > 0, exch_ts внутри суток (остальные — `dropped` строки seconds).
  tk359-sums.py <binlog> <seconds.csv> <trades.csv> <день YYYY-MM-DD>   rc 0 = равно, 1 = расхождение
"""
import csv
import struct
import sys
from datetime import datetime, timezone

binlog, secs, trades, day = sys.argv[1:5]
with open(binlog, "rb") as f:
    h = f.read(21)
tick_e9, step_e9 = struct.unpack("<qq", h[5:21])
t0 = int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()) * 1000
usd = {"buy": 0, "sell": 0}
cnt = {"buy": 0, "sell": 0}
out_of_day = 0
with open(trades, newline="") as f:
    for r in csv.DictReader(f):
        if r["block"] not in ("0", "false") or r["rpi"] not in ("0", "false"):
            continue
        p, q = int(r["price_tick"]), int(r["qty_lots"])
        if p <= 0 or q <= 0:
            continue
        ts = int(r["exch_ts_ms"])
        if not t0 <= ts < t0 + 86_400_000:
            out_of_day += 1
            continue
        usd[r["side"]] += p * tick_e9 * q * step_e9
        cnt[r["side"]] += 1
tb = ts_ = nb = ns = 0
with open(secs, newline="") as f:
    for r in csv.DictReader(f):
        tb += int(r["tb_usd"]); ts_ += int(r["ts_usd"]); nb += int(r["nb"]); ns += int(r["ns"])
want = (usd["buy"] // 10**18, usd["sell"] // 10**18, cnt["buy"], cnt["sell"])
got = (tb, ts_, nb, ns)
print(f"sums tb,ts,nb,ns got={got} want={want} out_of_day={out_of_day} {'OK' if got == want else 'RASHOZHDENIE'}")
sys.exit(0 if got == want else 1)
