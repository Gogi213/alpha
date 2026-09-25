#!/usr/bin/env python3
"""E28: пересчёт выхода сделок по минутным свечам — стоп под волатильность монеты и выход по BTC в позиции.

Входы не меняются: сделки счёта дашборда (`portfolio-sim.load_rounds`, монеты `--drop` вне пула, одна позиция на
монету). Позиция стартует с первой минуты от сигнала, где low монеты дошёл до средней цены входа (не позже 30 мин;
иначе — минута сигнала). Дальше поминутно, в порядке приоритета плана: стоп (low ≤ стоп; выход по стопу или по open,
если минута открылась ниже), выход по BTC (закрытие BTC ≤ старт × (1 − X) → выход по закрытию монеты следующей
минуты), трейл (лучший high ≥ вход × 1,01, затем цена ≤ лучший − 1 % от входа → выход по этой цене), дедлайн 4 ч.
Издержки сделки — как в бэктесте (`fee = gross − net`). Сначала база пересчёта сверяется с бэктестом: если не
воспроизводит итог и причины, цифрам вариантов не верим.

    python3 exit-sim.py --epoch август=epochs/e-aug:b5/titrc-u500r \\
        --epoch сентябрь=epochs/e-archive:b5/titrc-u500r --epoch сентябрь=.:b5/titrc-u500r \\
        --klines август=epochs/e-aug/study/klines --klines сентябрь=study/klines \\
        --set t-bid-btc4h-q1 --form ladder3x2..20w2-pct2-tr1x1-14400-ttl1800 --drop TRXUSDT > study/exit-sim-btc4h.txt
"""
import argparse
import bisect
import csv
import importlib.util
import math
import os
from collections import defaultdict

_spec = importlib.util.spec_from_file_location("psim", os.path.join(os.path.dirname(os.path.abspath(__file__)), "portfolio-sim.py"))
psim = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(psim)

MIN_MS, NS = 60_000, 1_000_000_000
TRAIL_ACT, TRAIL_GAP, DEADLINE_MIN, FILL_WAIT_MIN = 0.01, 0.01, 240, 30


class Bars:
    def __init__(self, paths):
        self.d = {}
        for p in paths:
            if os.path.exists(p):
                with open(p, encoding="utf-8") as f:
                    for r in csv.DictReader(f):
                        self.d[int(r["minute_ms"])] = (float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]))
        self.k = sorted(self.d)

    def span(self, m0, m1):
        lo, hi = bisect.bisect_left(self.k, m0), bisect.bisect_right(self.k, m1)
        return [(m, self.d[m]) for m in self.k[lo:hi]]

    def close_at(self, m):
        i = bisect.bisect_right(self.k, m) - 1
        return self.d[self.k[i]][3] if i >= 0 else None


def rv_24h(bars, m):
    cl = [b[3] for _, b in bars.span(m - 1440 * MIN_MS, m)]
    if len(cl) < 720:
        return None
    return math.sqrt(sum(math.log(b / a) ** 2 for a, b in zip(cl, cl[1:]) if a > 0 and b > 0))


def simulate(tr, coin, btc, stop_pct, btc_x):
    """Выход одной сделки; возвращает (net_bps, причина, минута выхода)."""
    entry, fee = tr["entry"], tr["fee"]
    m_sig = tr["t0"] // 1_000_000 // MIN_MS * MIN_MS
    bars = coin.span(m_sig, m_sig + (FILL_WAIT_MIN + DEADLINE_MIN + 5) * MIN_MS)
    if not bars:
        return None
    start = next((i for i, (m, b) in enumerate(bars) if b[2] <= entry and m <= m_sig + FILL_WAIT_MIN * MIN_MS), 0)
    m_start = bars[start][0]
    stop_px = entry * (1 - stop_pct)
    b0 = btc.close_at(m_start)
    best = entry
    net = lambda px: (px / entry - 1) * 1e4 - fee
    for i in range(start, len(bars)):
        m, (o, h, l, c) = bars[i]
        if m - m_start >= DEADLINE_MIN * MIN_MS:
            return net(o), "deadline", m
        if l <= stop_px:
            return net(min(stop_px, o) if i > start else stop_px), "stop", m
        if btc_x and b0:
            bc = btc.close_at(m)
            if bc and bc <= b0 * (1 - btc_x):
                nxt = bars[i + 1][1][3] if i + 1 < len(bars) else c
                return net(nxt), "btc", m
        if best >= entry * (1 + TRAIL_ACT) and l <= best - TRAIL_GAP * entry:
            return net(min(best - TRAIL_GAP * entry, o) if o < best - TRAIL_GAP * entry else best - TRAIL_GAP * entry), "trail", m
        best = max(best, h)
    m, (o, h, l, c) = bars[-1]
    return net(c), "deadline", m


VARIANTS = [("база: стоп 2 %", "fix", 0.02, 0), ("стоп 2,5 %", "fix", 0.025, 0), ("стоп 3 %", "fix", 0.03, 0),
            ("стоп max(2 %, 2σ4ч)", "floor", 2, 0), ("стоп max(2 %, 3σ4ч)", "floor", 3, 0),
            ("стоп 2σ4ч", "vol", 2, 0), ("стоп 3σ4ч", "vol", 3, 0)] + \
           [(f"стоп 2 % + выход, если BTC −{x * 100:g} %", "fix", 0.02, x) for x in (0.005, 0.0075, 0.01, 0.0125, 0.015)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--epoch", action="append", required=True)
    ap.add_argument("--klines", action="append", required=True)
    ap.add_argument("--set", required=True)
    ap.add_argument("--form", required=True)
    ap.add_argument("--drop", default="")
    a = ap.parse_args()
    drop = set(x for x in a.drop.split(",") if x)
    months = defaultdict(list)
    for s in a.epoch:
        n, rest = s.split("=", 1)
        months[n].append(rest.split(":", 1))
    kl = {s.split("=", 1)[0]: s.split("=", 1)[1].split(",") for s in a.klines}

    for month, parts in months.items():
        btc = Bars([os.path.join(h, "study", "regime", "ref-BTCUSDT-1m.csv") for h, _ in parts])
        rows = []
        for h, run in parts:
            rows += [r for r in psim.load_rounds(h, run, a.set, a.form) if r["sym"] not in drop]
        taken, busy = [], {}
        for r in sorted(rows, key=lambda x: x["t0"]):
            if busy.get(r["sym"], 0) > r["t0"]:
                continue
            busy[r["sym"]] = r["t1"]
            taken.append(r)
        coins = {}
        actual = sum(r["net"] / 1e4 * r["usd"] for r in taken)
        print(f"\n######## {month}: {len(taken)} сд; бэктест {actual:+.0f}$, стопов {sum(r['reason'] == 'stop' for r in taken)}")
        results = {}
        for label, kind, par, bx in VARIANTS:
            out = []
            for r in taken:
                if r["sym"] not in coins:
                    coins[r["sym"]] = Bars([os.path.join(d, f"ref-{r['sym']}-1m.csv") for d in kl[month]])
                c = coins[r["sym"]]
                if kind == "fix":
                    sp = par
                else:
                    s24 = rv_24h(c, r["t0"] // 1_000_000 // MIN_MS * MIN_MS - MIN_MS)
                    s4 = s24 * math.sqrt(240 / 1440) if s24 else None
                    sp = 0.02 if s4 is None else (max(0.02, par * s4) if kind == "floor" else par * s4)
                res = simulate(r, c, btc, sp, bx)
                if res is None:
                    res = (r["net"], r["reason"], r["t1"] // 1_000_000)
                out.append((r, res, sp))
            results[label] = out
            pnl = [x[1][0] / 1e4 * x[0]["usd"] for x in out]
            by = defaultdict(lambda: [0, 0.0])
            for (r, res, _), p in zip(out, pnl):
                by[res[1]][0] += 1
                by[res[1]][1] += p
            day = defaultdict(float)
            for (r, res, _), p in zip(out, pnl):
                day[psim.day_of(res[2] * 1_000_000)] += p
            cum = peak = dd = 0.0
            for (r, res, _), p in sorted(zip(out, pnl), key=lambda z: z[0][1][2]):
                cum += p
                peak = max(peak, cum)
                dd = min(dd, cum - peak)
            stops = sorted(x[2] for x in out)
            print(f"  {label:<34} {sum(pnl):+6.0f}$ | стопов {by['stop'][0]:3d} ({by['stop'][1]:+.0f}$) | трейл {by['trail'][0]:3d} "
                  f"({by['trail'][1]:+.0f}$) | 4 ч {by['deadline'][0]:3d} ({by['deadline'][1]:+.0f}$) | по BTC {by['btc'][0]:3d} "
                  f"({by['btc'][1]:+.0f}$) | худший день {min(day.values()):+.0f}$ | просадка по закрытиям {dd:+.0f}$ | "
                  f"стоп: медиана {stops[len(stops) // 2] * 100:.1f} %")
            if label.startswith("база"):
                agree = sum(1 for r, res, _ in out if res[1] == r["reason"])
                per = [abs(res[0] - r["net"]) for r, res, _ in out]
                print(f"     сверка базы с бэктестом: итог {sum(pnl):+.0f}$ против {actual:+.0f}$; причина выхода совпала у "
                      f"{agree} из {len(out)} ({agree / len(out):.0%}); |Δ net| медиана {sorted(per)[len(per) // 2]:.0f} bps")


if __name__ == "__main__":
    main()
