"""Окно данных arb-holding v4: начало и конец записи по биржам, пропуски сбора, покрытие пар. Пишет Markdown-таблицы для § «Окно».

Запуск: python3 hold_window.py <flagged.pkl> <rawgaps.pkl> <window.json> <dead.json> <выход.md>
Окно = [самая ранняя запись среди бирж; min по биржам последней записи − 2 мин]; покрытие пары = от позднейшего из двух начал до конца окна.
"""
import json
import pickle
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

import numpy as np


def fmt(ms, tz=0):
    return (datetime.fromtimestamp(ms / 1000, timezone.utc) + timedelta(hours=tz)).strftime("%d.%m %H:%M")


def main(flagp, rawp, winp, deadp, out):
    tab = pickle.load(open(flagp, "rb"))
    raw = pickle.load(open(rawp, "rb"))
    win = json.load(open(winp))
    dead = json.load(open(deadp))
    t0, t1 = win["t0"], win["t1"]
    L = [f"Окно: {fmt(t0)} → {fmt(t1)} UTC ({fmt(t0, 4)} → {fmt(t1, 4)} GMT+4), {(t1 - t0) / 3.6e6:.2f} ч.", ""]
    L += ["| биржа | первая запись (UTC) | последняя запись (UTC) | символов с записями | пропусков сбора ≥ 15 с | их суммарная длина, с |", "|---|---|---|---|---|---|"]
    nsym = Counter(ex for ex, _ in raw["sym"])
    for ex in ("binance", "bybit", "okx", "bingx", "gate"):
        f, l = raw["span"][ex]
        gaps = dead.get(ex, [])
        L.append(f"| {ex} | {fmt(f)} | {fmt(l)} | {nsym[ex]} | {len(gaps)} | {sum(b - a for a, b in gaps) / 1000:.0f} |")
    L.append("")
    cov = []
    for (coin, a, b), p in tab["pairs"].items():
        s = max(p["start"]["z"], t0)
        cov.append(((t1 - s) / 3.6e6, a, b))
    cov = np.array([c[0] for c in cov])
    edges = [0, 12, 24, 48, 72, 96, 1000]
    names = ["< 12 ч", "12–24 ч", "24–48 ч", "48–72 ч", "72–96 ч", "≥ 96 ч"]
    h, _ = np.histogram(cov, edges)
    L += ["| покрытие пары (общие данные двух бирж) | пар |", "|---|---|"] + [f"| {n} | {int(x)} |" for n, x in zip(names, h)]
    L.append("")
    byex = Counter()
    for (coin, a, b), p in tab["pairs"].items():
        byex[(a, b)] += 1
    L += ["| пара бирж | пар «монета × две биржи» | покрытие, ч (медиана) |", "|---|---|---|"]
    for (a, b), n in sorted(byex.items()):
        cc = [(t1 - max(p["start"]["z"], t0)) / 3.6e6 for (c, aa, bb), p in tab["pairs"].items() if (aa, bb) == (a, b)]
        L.append(f"| {a}–{b} | {n} | {np.median(cc):.1f} |")
    open(out, "w", encoding="utf-8").write("\n".join(L))
    print("\n".join(L[:12]))


if __name__ == "__main__":
    main(*sys.argv[1:6])
