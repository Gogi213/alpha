#!/usr/bin/env python3
"""E28/E29: пересчёт выхода сделок по минутным свечам — стоп под волатильность, выход по BTC (E28) и выходы на
быстрой и медленной realized volatility (E29).

Входы не меняются: сделки счёта дашборда (`portfolio-sim.load_rounds`, монеты `--drop` вне пула, одна позиция на
монету). Позиция стартует с первой минуты от сигнала, где low монеты дошёл до средней цены входа (не позже 30 мин;
иначе — минута сигнала). Дальше поминутно, в порядке приоритета плана: стоп (low ≤ стоп; выход по стопу или по open,
если минута открылась ниже), выход по BTC / по разгону волатильности (решение по закрытым минутам, исполнение по
следующей), трейл (лучший high ≥ вход × 1,01, затем цена ≤ лучший − откат × вход), дедлайн 4 ч. Издержки сделки — как в
бэктесте (`fee = gross − net`). RV — корень суммы квадратов минутных лог-доходностей: быстрая — за 30 / 60 минут,
закрытых до текущей; медленная σ — RV за сутки до входа (σ4ч = σ × √(240/1440)). База пересчёта сверяется с бэктестом
(итог и причины выхода): если не воспроизводит, цифрам вариантов не верим; варианты сравниваются с базой пересчёта.

    python3 exit-sim.py --family e29 --epoch август=epochs/e-aug:b5/titrc-u500r \\
        --epoch сентябрь=epochs/e-archive:b5/titrc-u500r --epoch сентябрь=.:b5/titrc-u500r \\
        --klines август=epochs/e-aug/study/klines --klines сентябрь=study/klines \\
        --set t-bid-btc4h-q1 --form ladder3x2..20w2-pct2-tr1x1-14400-ttl1800 --drop TRXUSDT > study/exit-sim-e29.txt
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

MIN_MS = 60_000
STOP, TRAIL_ACT, TRAIL_GAP, DEADLINE_MIN, FILL_WAIT_MIN, PRE_MIN = 0.02, 0.01, 0.01, 240, 30, 60


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


def simulate(tr, coin, btc, v, s24):
    """Выход одной сделки по варианту `v`; возвращает (net_bps, причина, минута выхода) или None без свечей."""
    entry, fee = tr["entry"], tr["fee"]
    m_sig = tr["t0"] // 1_000_000 // MIN_MS * MIN_MS
    bars = coin.span(m_sig - PRE_MIN * MIN_MS, m_sig + (FILL_WAIT_MIN + DEADLINE_MIN + 5) * MIN_MS)
    i0 = next((i for i, (m, _) in enumerate(bars) if m >= m_sig), None)
    if i0 is None:
        return None
    sq = [0.0]
    for (_, a), (_, b) in zip(bars, bars[1:]):
        sq.append(sq[-1] + (math.log(b[3] / a[3]) ** 2 if a[3] > 0 and b[3] > 0 else 0.0))

    def rv_before(i, n):  # RV по n минутам, закрытым до минуты i
        return math.sqrt(sq[i - 1] - sq[i - 1 - n]) if i - 1 - n >= 0 else None

    start = next((i for i in range(i0, len(bars)) if bars[i][1][2] <= entry and bars[i][0] <= m_sig + FILL_WAIT_MIN * MIN_MS), i0)
    m_start = bars[start][0]
    kind, p = v["kind"], v.get("p")
    s4 = s24 * math.sqrt(240 / 1440) if s24 else None
    b0 = btc.close_at(m_start)
    best, tight = entry, False
    net = lambda px: (px / entry - 1) * 1e4 - fee
    for i in range(start, len(bars)):
        m, (o, h, l, c) = bars[i]
        t = (m - m_start) / MIN_MS
        if t >= DEADLINE_MIN:
            return net(o), "deadline", m
        if kind == "chandelier" and s4:
            stop = best - p * s4 * entry
        elif kind == "decay":
            stop = entry * (1 - (STOP - (STOP - p) * math.log(1 + t / 30) / math.log(1 + DEADLINE_MIN / 30)))
        else:
            stop = entry * (1 - (v.get("stop") or STOP))
        if l <= stop:
            px = min(stop, o) if i > start else stop
            return net(px), ("stop" if px < entry else "trail"), m
        if kind == "btc" and b0:
            bc = btc.close_at(m - MIN_MS)
            if bc and bc <= b0 * (1 - p):
                return net(o), "btc", m
        if kind == "volexp" and s24:
            fast = rv_before(i, 30)
            if fast is not None and fast >= p * s24 * math.sqrt(30 / 1440):
                if o < entry:
                    return net(o), "rv", m
                tight = True
        if kind != "chandelier":
            gap = TRAIL_GAP
            if kind == "trail_rv":
                r60 = rv_before(i, 60)
                gap = max(0.0025, p * r60) if r60 else TRAIL_GAP
            if tight:
                gap = 0.005
            lvl = best - gap * entry
            if best >= entry * (1 + TRAIL_ACT) and l <= lvl:
                return net(min(lvl, o)), "trail", m
        best = max(best, h)
    m, (o, h, l, c) = bars[-1]
    return net(c), "deadline", m


FAMILIES = {
    "e28": [{"label": "база: стоп 2 %", "kind": "fix"},
            {"label": "стоп 2,5 %", "kind": "fix", "stop": 0.025}, {"label": "стоп 3 %", "kind": "fix", "stop": 0.03},
            {"label": "стоп max(2 %, 2σ4ч)", "kind": "fix", "vol": ("floor", 2)}, {"label": "стоп max(2 %, 3σ4ч)", "kind": "fix", "vol": ("floor", 3)},
            {"label": "стоп 2σ4ч", "kind": "fix", "vol": ("pure", 2)}, {"label": "стоп 3σ4ч", "kind": "fix", "vol": ("pure", 3)}]
           + [{"label": f"выход, если BTC −{x * 100:g} %", "kind": "btc", "p": x} for x in (0.005, 0.0075, 0.01, 0.0125, 0.015)],
    "e29": [{"label": "база: стоп 2 %, трейл 1/1", "kind": "fix"}]
           + [{"label": f"трейл по быстрой RV: откат {k:g}×RV60", "kind": "trail_rv", "p": k} for k in (1, 1.5, 2)]
           + [{"label": f"люстра: максимум − {k:g}σ4ч", "kind": "chandelier", "p": k} for k in (1.5, 2, 2.5)]
           + [{"label": f"разгон RV30 ≥ {r:g}× медленной", "kind": "volexp", "p": r} for r in (2, 3)]
           + [{"label": f"стоп по логарифму времени, к 4 ч {s * 100:g} %", "kind": "decay", "p": s} for s in (0.01, 0.005)],
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--family", choices=sorted(FAMILIES), default="e29")
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
        coins, s24 = {}, {}
        for r in taken:
            if r["sym"] not in coins:
                coins[r["sym"]] = Bars([os.path.join(d, f"ref-{r['sym']}-1m.csv") for d in kl[month]])
            s24[id(r)] = rv_24h(coins[r["sym"]], r["t0"] // 1_000_000 // MIN_MS * MIN_MS - MIN_MS)
        actual = sum(r["net"] / 1e4 * r["usd"] for r in taken)
        print(f"\n######## {month}: {len(taken)} сд; бэктест {actual:+.0f}$, стопов {sum(r['reason'] == 'stop' for r in taken)}")
        base_usd = None
        for v in FAMILIES[a.family]:
            out = []
            for r in taken:
                vv = dict(v)
                if "vol" in v:
                    mode, k = v["vol"]
                    s4 = s24[id(r)] * math.sqrt(240 / 1440) if s24[id(r)] else None
                    vv["stop"] = STOP if s4 is None else (max(STOP, k * s4) if mode == "floor" else k * s4)
                res = simulate(r, coins[r["sym"]], btc, vv, s24[id(r)]) or (r["net"], r["reason"], r["t1"] // 1_000_000)
                out.append((r, res))
            pnl = [res[0] / 1e4 * r["usd"] for r, res in out]
            by = defaultdict(lambda: [0, 0.0])
            day = defaultdict(float)
            for (r, res), p in zip(out, pnl):
                by[res[1]][0] += 1
                by[res[1]][1] += p
                day[psim.day_of(res[2] * 1_000_000)] += p
            cum = peak = dd = 0.0
            for (r, res), p in sorted(zip(out, pnl), key=lambda z: z[0][1][2]):
                cum += p
                peak = max(peak, cum)
                dd = min(dd, cum - peak)
            total = sum(pnl)
            if base_usd is None:
                base_usd = total
            parts_s = " | ".join(f"{k} {n_} ({s_:+.0f}$)" for k, (n_, s_) in sorted(by.items(), key=lambda kv: kv[0]))
            print(f"  {v['label']:<40} {total:+6.0f}$ (Δ {total - base_usd:+4.0f}) | {parts_s} | худший день {min(day.values()):+.0f}$ | "
                  f"просадка по закрытиям {dd:+.0f}$")
            if v is FAMILIES[a.family][0]:
                agree = sum(1 for r, res in out if res[1] == r["reason"])
                per = sorted(abs(res[0] - r["net"]) for r, res in out)
                print(f"     сверка базы с бэктестом: итог {total:+.0f}$ против {actual:+.0f}$; причина выхода совпала у "
                      f"{agree} из {len(out)} ({agree / len(out):.0%}); |Δ net| медиана {per[len(per) // 2]:.0f} bps")


if __name__ == "__main__":
    main()
