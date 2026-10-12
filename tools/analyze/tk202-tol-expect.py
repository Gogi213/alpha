#!/usr/bin/env python3
"""TK-202: ожидание Python для сверки Rust-допуска usd_min/behind_min на сутках: ключи B1 (symbol,t0_ms,price_tick) с usd>=T_usd / behind>=T_behind.
Запуск: tk202-tol-expect.py <мес jan> <сутки> <T_usd> <T_behind> <instruments.csv> <выход.csv>. Поля — как thr3 (usd=price_tick·tick·size_at_arm·qty_step; behind=depth_behind·100/size_at_arm)."""
import csv, sys
mon, day, tu, tb, inst_f, out = sys.argv[1], sys.argv[2], float(sys.argv[3]), float(sys.argv[4]), sys.argv[5], sys.argv[6]
pool, t46 = "/data/tk064/pool", "/data/tk046"
inst = {r["symbol"]: (float(r["tick_size"]), float(r["qty_step"])) for r in csv.DictReader(open(inst_f, encoding="utf-8"))}
sig = {}
for sub in ("signals", "signals-retry"):
    try:
        for r in csv.DictReader(l for l in open(f"{pool}/m-{mon}/{sub}/{day}/t-bid-btc4h-q1/signals.csv", encoding="utf-8") if not l.startswith("#")):
            sig[(r["symbol"], int(r["t0_ns"]) // 1000000)] = int(float(r["price_tick"]))
    except FileNotFoundError:
        pass
lv = {(r["symbol"], int(float(r["arm_ms"])), int(float(r["price_tick"]))): float(r["size_at_arm"] or 0) for r in csv.DictReader(open(f"{pool}/m-{mon}/lvl/{day}.csv", encoding="utf-8"))}
ap = {}
for s in {s for s, _ in sig}:
    try:
        for r in csv.DictReader(open(f"{t46}/{mon}/home/alpha/epochs/e-{mon}/study/approaches/D20/{day}/approaches-{s}.csv", encoding="utf-8")):
            if r["side"] == "bid":
                ap[(s, int(r["arm_ms"]), int(r["price_tick"]))] = float(r["depth_behind_lots"] or 0)
    except FileNotFoundError:
        pass
n = [0, 0, 0]
with open(out, "w", newline="") as f:
    w = csv.writer(f); w.writerow(["symbol", "t0_ms", "price_tick", "size", "usd", "behind_pct", "usd_ok", "behind_ok"])
    for (s, t), pt in sorted(sig.items()):
        sz = lv.get((s, t, pt), 0)
        usd = pt * inst[s][0] * sz * inst[s][1] if s in inst else 0
        bh = ap.get((s, t, pt))
        bp = bh * 100 / sz if bh is not None and sz > 0 else ""
        uo, bo = int(usd >= tu), int(bp != "" and bp >= tb)
        n[0] += 1; n[1] += uo; n[2] += bo
        w.writerow([s, t, pt, sz, round(usd, 2), bp, uo, bo])
print("b1", n[0], "usd_ok", n[1], "behind_ok", n[2])
