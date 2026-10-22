#!/usr/bin/env python3
"""TK-291 (П-19 §7 п.1, ворота 0): честный bounce-grid с lot_first=k против линейного пересчёта по B1-проходу.

tk291-lin.py <B1 out> <lf out> <k-метка=k>… [--months 2026-08,2026-09]
B1 out — /data/tk291/out (rounds.csv набора t-bid-btc4h-q1 со столбцом touch_index); lf out — /data/tk291/out-lf
(наборы t-bid-btc4h-q1-lf<метка>). Линейно: $(сделка) = k · $_1x для touch_index == 0, остальные = $_1x; $ = net_bps/1e4 · qty · entry_vwap.
Допуски §7 п.1 (месяц × клетка): (а) |Δ Σ$| ≤ 5 % Σ|$| клетки; (б) знак ΔSR_норм (Шарп по суткам, $_норм = $·m, m = N_B1/N_клетки)
у честного = у линейного; (в) число сделок честного = числу B1. Код выхода 1 — хоть одно красное."""
import collections
import csv
import glob
import math
import os
import sys

FORM = "ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800"


def load(root, setname, months):
    out = collections.defaultdict(dict)  # month -> key -> (day, $, touch)
    for p in sorted(glob.glob(os.path.join(root, "run-*", "out", "*", setname, "rounds.csv"))):
        day = p.split(os.sep)[-3]
        if day[:7] not in months:
            continue
        with open(p, newline="", encoding="utf-8") as f:
            lines = [l.rstrip("\r\n") for l in f if not l.startswith("#")]
        h, rows = lines[0].split(","), list(csv.reader(lines[1:]))
        c = {n: h.index(n) for n in ("symbol", "form", "t0_ns", "dir", "net_bps", "qty", "entry_vwap", "touch_index")}
        for r in rows:
            if r[c["form"]] != FORM:
                continue
            usd = float(r[c["net_bps"]]) / 1e4 * float(r[c["qty"]]) * float(r[c["entry_vwap"]])
            out[day[:7]][(r[c["symbol"]], r[c["t0_ns"]], r[c["dir"]])] = (day, usd, r[c["touch_index"]])
    return out


def sharpe(per_day, days):
    xs = [per_day.get(d, 0.0) for d in days]
    if len(xs) < 2:
        return 0.0
    m = sum(xs) / len(xs)
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))
    return m / sd if sd > 0 else 0.0


def by_day(trades, mult=lambda t: 1.0):
    d = collections.defaultdict(float)
    for day, usd, ti in trades:
        d[day] += usd * mult(ti)
    return d


a = sys.argv[1:]
months = "2026-08,2026-09"
if "--months" in a:
    i = a.index("--months")
    months = a[i + 1]
    del a[i:i + 2]
months = set(months.split(","))
b1_root, lf_root, cells = a[0], a[1], a[2:]
b1 = load(b1_root, "t-bid-btc4h-q1", months)
red = 0
for cell in cells:
    lab, k = cell.split("=")
    k = float(k)
    hon = load(lf_root, "t-bid-btc4h-q1-lf" + lab, months)
    for mon in sorted(months):
        B, H = b1[mon], hon[mon]
        days = sorted({v[0] for v in B.values()} | {v[0] for v in H.values()})
        lin = [(d, u * (k if t == "0" else 1.0), t) for (d, u, t) in B.values()]
        hon_t = list(H.values())
        s_lin, s_hon, s_b1 = sum(x[1] for x in lin), sum(x[1] for x in hon_t), sum(x[1] for x in B.values())
        abs_cell = sum(abs(x[1]) for x in hon_t) or 1.0
        da = abs(s_hon - s_lin) / abs_cell
        sr_b1 = sharpe(by_day(B.values()), days)
        m_lin = len(B) / len(lin) if lin else 1.0
        m_hon = len(B) / len(hon_t) if hon_t else 1.0
        d_lin = sharpe({d: v * m_lin for d, v in by_day(lin).items()}, days) - sr_b1
        d_hon = sharpe({d: v * m_hon for d, v in by_day(hon_t).items()}, days) - sr_b1
        ok_a, ok_b, ok_c = da <= 0.05, (d_lin > 0) == (d_hon > 0), len(hon_t) == len(B)
        only_h, only_b = len(set(H) - set(B)), len(set(B) - set(H))
        red += not (ok_a and ok_b and ok_c)
        print("%s lot_first=%s | Σ$ B1 %.2f линейно %.2f честно %.2f | (а) |Δ|/Σ|$| = %.3f %s | (б) ΔSR линейно %+.3f честно %+.3f %s | "
              "(в) сделок B1 %d честно %d (только честно %d, только B1 %d) %s" % (
                  mon, lab, s_b1, s_lin, s_hon, da, "ок" if ok_a else "КРАСНО", d_lin, d_hon, "ок" if ok_b else "КРАСНО",
                  len(B), len(hon_t), only_h, only_b, "ок" if ok_c else "КРАСНО"))
print("красных клеток×месяцев: %d" % red)
sys.exit(1 if red else 0)
