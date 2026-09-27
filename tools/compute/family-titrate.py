#!/usr/bin/env python3
"""E31: «несколько живых семейств» вместо одного набора параметров (владелец 25.09) — семейства монет по
волатильности, у каждого свой стоп и выход; проверка крест-накрест между месяцами.

Семейство сделки — по RV монеты за сутки до входа: K = 2 (медиана) или 3 (трети); границы берутся по месяцу подбора и
переносятся на месяц проверки как есть. Выход каждой сделки пересчитывается по минутным свечам (`exit-sim.simulate`,
та же модель, что E28–E30) для 14 вариантов: стоп 1,5 / 2 / 3 % × {трейл 1/1, 0,5/0,25, 1,5/0,5, тейк 1,75 %} и
«люстра» 2σ4ч / 2,5σ4ч. Подбор на месяце A: лучший вариант на каждое семейство и один лучший общий; счёт на месяце B:
база (стоп 2 %, трейл 1/1), один общий, по семействам. Чтение на тех же данных, не вердикт.

    python3 family-titrate.py --epoch август=epochs/e-aug:b5/titrc-u500r \\
        --epoch сентябрь=epochs/e-archive:b5/titrc-u500r --epoch сентябрь=.:b5/titrc-u500r \\
        --klines август=epochs/e-aug/study/klines --klines сентябрь=study/klines \\
        --set t-bid-btc4h-q1 --form ladder3x2..20w2-pct2-tr1x1-14400-ttl1800 --drop TRXUSDT > study/family-titrate.txt
"""
import argparse
import importlib.util
import math
import os
from collections import defaultdict

_spec = importlib.util.spec_from_file_location("esim", os.path.join(os.path.dirname(os.path.abspath(__file__)), "exit-sim.py"))
esim = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(esim)
_vspec = importlib.util.spec_from_file_location("vest", os.path.join(os.path.dirname(os.path.abspath(__file__)), "vol-estimators.py"))
vest = importlib.util.module_from_spec(_vspec)
_vspec.loader.exec_module(vest)
DAY_MS = 86_400_000


def daily_grades(kdirs, homes, day_from, day_to, drop):
    """E33: доля монеты по сводной волатильности (среднее рангов 13 оценок E32) среди монет пула за каждые сутки."""
    btc = {}
    for h in homes:
        btc.update({m: b[3] for m, b in vest.load(os.path.join(h, "study", "regime", "ref-BTCUSDT-1m.csv")).items()})
    syms = sorted({f[4:-7] for d in kdirs for f in os.listdir(d) if f.startswith("ref-") and f.endswith("-1m.csv")} - drop - {"BTCUSDT"})
    per_day = defaultdict(dict)
    for s in syms:
        d = {}
        for kd in kdirs:
            d.update(vest.load(os.path.join(kd, f"ref-{s}-1m.csv")))
        keys = sorted(d)
        for day in range(day_from, day_to + 1, DAY_MS):
            e = vest.estimators(vest.window(d, keys, day + DAY_MS, 1440), btc)
            if e:
                per_day[day][s] = e
    out = {}
    for day, coins in per_day.items():
        rows = list(coins.values())
        vest.add_rank(rows)
        n = len(rows)
        out[day] = {s: (e["rank"] / (n - 1) if n > 1 else 0.5) for s, e in coins.items()}
    return out

EXITS = [("трейл 1/1", {"act": 0.01, "gap": 0.01}), ("трейл 0,5/0,25", {"act": 0.005, "gap": 0.0025}),
         ("трейл 1,5/0,5", {"act": 0.015, "gap": 0.005}), ("тейк 1,75 %", {"take": 0.0175})]
VARIANTS = [{"label": f"стоп {s * 100:g} %, {name}", "kind": "fix", "stop": s, **ex} for s in (0.015, 0.02, 0.03) for name, ex in EXITS] \
           + [{"label": f"люстра {k:g}σ4ч", "kind": "chandelier", "p": k} for k in (2, 2.5)]
BASE = "стоп 2 %, трейл 1/1"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--epoch", action="append", required=True)
    ap.add_argument("--klines", action="append", required=True)
    ap.add_argument("--set", required=True)
    ap.add_argument("--form", required=True)
    ap.add_argument("--drop", default="")
    ap.add_argument("--family-by", choices=("rv24h", "grade"), default="rv24h",
                    help="rv24h — RV монеты за сутки, границы по месяцу подбора (E31); grade — сводная волатильность за 7 суток, границы фиксированы (E33)")
    a = ap.parse_args()
    drop = set(x for x in a.drop.split(",") if x)
    months = defaultdict(list)
    for s in a.epoch:
        # T-21 batch 1: разбор по одной спеке через `_lib.epoch.parse_epoch` (у exit-sim, откуда
        # берётся `_lib` этой сессии, — та же причина в комментарии там: повтор имени с разным
        # домом, `parse_epochs` бы его дописал в чужой дом).
        e = esim._lib.parse_epoch(s)
        months[e.name].append((e.home, ",".join(e.runs)))
    kl = {s.split("=", 1)[0]: s.split("=", 1)[1].split(",") for s in a.klines}

    # pnl[month] = список (rv24h, {вариант: $})
    pnl = {}
    for month, parts in months.items():
        btc = esim.Bars([os.path.join(h, "study", "regime", "ref-BTCUSDT-1m.csv") for h, _ in parts])
        rows = []
        for h, run in parts:
            rows += [r for r in esim._lib.load_rounds(h, run, a.set, a.form) if r["sym"] not in drop]
        taken = esim._lib.one_per_coin(rows)
        coins, out = {}, []
        grades = None
        if a.family_by == "grade":
            d_first = min(r["t0"] for r in taken) // 1_000_000 // DAY_MS * DAY_MS
            d_last = max(r["t0"] for r in taken) // 1_000_000 // DAY_MS * DAY_MS
            grades = daily_grades(kl[month], [h for h, _ in parts], d_first - 7 * DAY_MS, d_last, drop)
        for r in taken:
            if r["sym"] not in coins:
                coins[r["sym"]] = esim.Bars([os.path.join(d, f"ref-{r['sym']}-1m.csv") for d in kl[month]])
            c = coins[r["sym"]]
            s24 = esim.rv_24h(c, r["t0"] // 1_000_000 // esim.MIN_MS * esim.MIN_MS - esim.MIN_MS)
            feat = s24
            if grades is not None:
                day = r["t0"] // 1_000_000 // DAY_MS * DAY_MS
                g = [grades[x][r["sym"]] for x in range(day - 7 * DAY_MS, day, DAY_MS) if x in grades and r["sym"] in grades[x]]
                feat = sum(g) / len(g) if g else None
            res = {}
            for v in VARIANTS:
                x = esim.simulate(r, c, btc, v, s24) or (r["net"], r["reason"], 0)
                res[v["label"]] = x[0] / 1e4 * r["usd"]
            out.append((feat, res))
        pnl[month] = out
        print(f"{month}: {len(out)} сд, база пересчёта {sum(x[1][BASE] for x in out):+.0f}$")

    def cuts(rows, k):
        if a.family_by == "grade":
            return [i / k for i in range(1, k)]
        v = sorted(s for s, _ in rows if s is not None)
        return [v[len(v) * i // k] for i in range(1, k)]

    def fam(s, cps):
        if s is None:
            return 0
        return sum(1 for c in cps if s > c)

    names = list(months)
    for k in (2, 3):
        print(f"\n######## семейств: {k} ({'сводная волатильность за 7 суток, границы фиксированы' if a.family_by == 'grade' else 'RV монеты за сутки до входа'})")
        for fit, test in ((names[0], names[1]), (names[1], names[0])):
            cps = cuts(pnl[fit], k)
            tot = defaultdict(lambda: defaultdict(float))
            for s, res in pnl[fit]:
                for lab, p in res.items():
                    tot[fam(s, cps)][lab] += p
            glob = defaultdict(float)
            for f_ in tot:
                for lab, p in tot[f_].items():
                    glob[lab] += p
            best_glob = max(glob, key=glob.get)
            best_fam = {f_: max(tot[f_], key=tot[f_].get) for f_ in tot}
            test_base = sum(res[BASE] for _, res in pnl[test])
            test_glob = sum(res[best_glob] for _, res in pnl[test])
            test_fam = sum(res[best_fam.get(fam(s, cps), BASE)] for s, res in pnl[test])
            n_test = defaultdict(int)
            for s, _ in pnl[test]:
                n_test[fam(s, cps)] += 1
            print(f"  подбор {fit} → проверка {test}: границы {', '.join((f'{c:.2f}' if a.family_by == 'grade' else f'{c * 1e4:.0f} bps') for c in cps)}; без оценки — {sum(1 for s_, _ in pnl[test] if s_ is None)} сд (в семействе 1)")
            for f_ in sorted(best_fam):
                print(f"     семейство {f_ + 1}: {best_fam[f_]:<28} (на подборе {tot[f_][best_fam[f_]]:+.0f}$ против базы "
                      f"{tot[f_][BASE]:+.0f}$; в проверке {n_test[f_]} сд)")
            print(f"     один общий на подборе: {best_glob} ({glob[best_glob]:+.0f}$ против базы {glob[BASE]:+.0f}$)")
            print(f"     В ПРОВЕРКЕ ({test}): база {test_base:+.0f}$ | один общий {test_glob:+.0f}$ ({test_glob - test_base:+.0f}) | "
                  f"по семействам {test_fam:+.0f}$ ({test_fam - test_base:+.0f})")
    print("\n######## вся сетка по семействам (K = 3, границы по каждому месяцу своим), $:")
    for m in names:
        cps = cuts(pnl[m], 3)
        tot = defaultdict(lambda: defaultdict(float))
        for s, res in pnl[m]:
            for lab, p in res.items():
                tot[fam(s, cps)][lab] += p
        print(f"  {m} (границы {', '.join((f'{c:.2f}' if a.family_by == 'grade' else f'{c * 1e4:.0f} bps') for c in cps)}):")
        for v in VARIANTS:
            print(f"     {v['label']:<28} " + " | ".join(f"сем.{f_ + 1} {tot[f_][v['label']]:+5.0f}" for f_ in sorted(tot)))


if __name__ == "__main__":
    main()
