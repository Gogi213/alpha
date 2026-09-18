#!/usr/bin/env python3
"""Исследование порога плотности (В-61, владелец 2026-09-18: «выяснить для
каждой монеты индивидуально и, если возможно, вывести общий агрегат»).

Вход: touches-<SYM>.csv (`lob touches --h3-mode floor` при `k = 1.0` — касания
ЛЮБОГО уровня) и instruments.csv (tick_size, qty_step) для номинала.
Для каждой монеты: касания раскладываются по корзинам номинала уровня
(price × size, USD, log2-корзины от $1k) и по корзинам силы «×соседи» в ±20 bps
(колонка strength_w20_pct); в корзине — число касаний, доля отскока
(ended_by_death == false), средний markout m_60s «в сторону отскока» (bps).

Критерий порога монеты (заранее, не по результату): N* — наименьший номинал,
при котором среди касаний с номиналом ≥ N* доля отскока превышает базовую
(долю по всем касаниям монеты) не меньше чем на ADV пунктов при n ≥ NMIN;
аналогично S* для силы. Агрегат: для сетки кандидатов N ∈ {5k … 500k} и
S ∈ {150 … 1000 %} — сколько монет проходят критерий при этом общем пороге
и сколько касаний в сутки остаётся. Числа ADV/NMIN — параметры отчёта, не
решения; печатаются в шапке.

Использование: threshold-study.py <dir-with-touches> <instruments.csv> <out.csv>
"""
import csv
import glob
import math
import os
import sys
from collections import defaultdict

ADV_PP = 10.0        # минимальное превышение доли отскока над базой, п.п.
NMIN = 30            # минимум касаний в хвосте ≥ порога
N_GRID = [5_000, 10_000, 20_000, 50_000, 100_000, 200_000, 300_000, 500_000]
S_GRID = [150, 200, 300, 500, 1000]


def load_instruments(path):
    inst = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.startswith("#") or line.startswith("symbol"):
                continue
            p = line.strip().split(",")
            if len(p) < 4:
                continue
            inst[p[0]] = (float(p[1]), float(p[3]))  # tick_size, qty_step
    return inst


def read_touches(path, tick, lot):
    rows = []
    with open(path, encoding="utf-8") as f:
        r = csv.DictReader(l for l in f if not l.startswith("#"))
        for x in r:
            try:
                notional = int(x["price_tick"]) * tick * int(x["size_at_touch"]) * lot
                bounced = x["ended_by_death"] != "true"
                m60 = float(x["m_60s"]) if x["m_60s"] not in ("", "none") else None
                s20 = float(x["strength_w20_pct"]) if x["strength_w20_pct"] else None
                days = x["day_utc"]
            except (KeyError, ValueError):
                continue
            rows.append((notional, bounced, m60, s20, days))
    return rows


def tail_stats(rows, key, thr):
    sel = [r for r in rows if key(r) is not None and key(r) >= thr]
    n = len(sel)
    if n == 0:
        return 0, None, None
    b = sum(1 for r in sel if r[1]) / n
    ms = [r[2] for r in sel if r[2] is not None]
    m = sum(ms) / len(ms) if ms else None
    return n, b, m


def coin_threshold(rows, key, grid, base):
    for thr in grid:
        n, b, _ = tail_stats(rows, key, thr)
        if n >= NMIN and b is not None and (b - base) * 100.0 >= ADV_PP:
            return thr
    return None


def main():
    d, inst_path, out_path = sys.argv[1], sys.argv[2], sys.argv[3]
    inst = load_instruments(inst_path)
    files = sorted(glob.glob(os.path.join(d, "touches-*.csv")))
    per = []
    agg_n = defaultdict(lambda: [0, 0, 0])  # thr -> [coins_pass, coins_with_data, touches_kept_total]
    agg_s = defaultdict(lambda: [0, 0, 0])
    agg_both = defaultdict(lambda: [0, 0, 0])
    total_days = 0
    for fp in files:
        sym = os.path.basename(fp)[len("touches-"):-len(".csv")]
        if sym not in inst:
            continue
        tick, lot = inst[sym]
        rows = read_touches(fp, tick, lot)
        if not rows:
            continue
        days = len({r[4] for r in rows}) or 1
        total_days += 0
        base = sum(1 for r in rows if r[1]) / len(rows)
        n_star = coin_threshold(rows, lambda r: r[0], N_GRID, base)
        s_star = coin_threshold(rows, lambda r: r[3], S_GRID, base)
        med_notional = sorted(r[0] for r in rows)[len(rows) // 2]
        p90 = sorted(r[0] for r in rows)[int(len(rows) * 0.9)]
        rec = {
            "symbol": sym, "touches_per_day": round(len(rows) / days),
            "base_bounce_pct": round(base * 100, 1),
            "median_notional_usd": round(med_notional), "p90_notional_usd": round(p90),
            "N_star_usd": n_star if n_star is not None else "",
            "S_star_pct": s_star if s_star is not None else "",
        }
        for thr in N_GRID:
            n, b, m = tail_stats(rows, lambda r: r[0], thr)
            rec[f"n_ge_{thr//1000}k"] = round(n / days, 1)
            rec[f"bounce_ge_{thr//1000}k"] = round(b * 100, 1) if b is not None else ""
            rec[f"m60_ge_{thr//1000}k"] = round(m, 2) if m is not None else ""
            a = agg_n[thr]; a[1] += 1; a[2] += n / days
            if n >= NMIN and b is not None and (b - base) * 100 >= ADV_PP:
                a[0] += 1
        for thr in S_GRID:
            n, b, m = tail_stats(rows, lambda r: r[3], thr)
            rec[f"n_s{thr}"] = round(n / days, 1)
            rec[f"bounce_s{thr}"] = round(b * 100, 1) if b is not None else ""
            a = agg_s[thr]; a[1] += 1; a[2] += n / days
            if n >= NMIN and b is not None and (b - base) * 100 >= ADV_PP:
                a[0] += 1
        for nt in (20_000, 50_000, 100_000):
            for st in (200, 300, 500):
                sel = [r for r in rows if r[0] >= nt and r[3] is not None and r[3] >= st]
                n = len(sel)
                b = (sum(1 for r in sel if r[1]) / n) if n else None
                key = (nt, st)
                a = agg_both[key]; a[1] += 1; a[2] += n / days
                if n >= NMIN and b is not None and (b - base) * 100 >= ADV_PP:
                    a[0] += 1
                rec[f"n_both_{nt//1000}k_s{st}"] = round(n / days, 1)
                rec[f"bounce_both_{nt//1000}k_s{st}"] = round(b * 100, 1) if b is not None else ""
        per.append(rec)

    if not per:
        print("нет касаний"); return
    cols = list(per[0].keys())
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        f.write(f"# threshold-study: ADV_PP={ADV_PP} NMIN={NMIN} coins={len(per)} touches from `lob touches --h3-mode floor` (k=1.0), outcome=ended_by_death, strength window 20 bps\n")
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in per:
            w.writerow(r)
    print(f"coins={len(per)}  ADV_PP={ADV_PP} NMIN={NMIN}")
    print("== per-coin N* (USD) distribution:")
    ns = sorted(r["N_star_usd"] for r in per if r["N_star_usd"] != "")
    print("  with N*:", len(ns), "of", len(per), " quantiles 10/50/90:", [ns[int(len(ns) * q)] for q in (0.1, 0.5, 0.9)] if ns else "-")
    ss = sorted(r["S_star_pct"] for r in per if r["S_star_pct"] != "")
    print("  with S*:", len(ss), "of", len(per), " quantiles 10/50/90:", [ss[int(len(ss) * q)] for q in (0.1, 0.5, 0.9)] if ss else "-")
    print("== common N: coins passing / coins, touches kept per coin-day (mean)")
    for thr in N_GRID:
        a = agg_n[thr]
        print(f"  N>={thr:>7}: {a[0]:3d}/{a[1]:<3d} pass  kept/day={a[2]/max(a[1],1):8.1f}")
    print("== common S (±20 bps):")
    for thr in S_GRID:
        a = agg_s[thr]
        print(f"  S>={thr:>5}%: {a[0]:3d}/{a[1]:<3d} pass  kept/day={a[2]/max(a[1],1):8.1f}")
    print("== both (N and S):")
    for (nt, st), a in sorted(agg_both.items()):
        print(f"  N>={nt:>6} & S>={st:>4}%: {a[0]:3d}/{a[1]:<3d} pass  kept/day={a[2]/max(a[1],1):8.1f}")


if __name__ == "__main__":
    main()
