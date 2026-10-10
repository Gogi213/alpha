#!/usr/bin/env python3
"""Замер для множителей `a`/`b` геометрии В-62 (владелец 2026-09-18: «А — надо
выяснить»): ход середины против отскока и за отскок внутри дедлайна, в
единицах `σ_H`, по касаниям пула.

Вход: каталог с `touches-<SYMBOL>.csv` (колонки `adverse_<H>s_bps`,
`favour_<H>s_bps`, `sigma_<H>s_bps`, `strength_w20_pct`, `size_at_touch`,
`price_tick`, `ended_by_death`) и `instruments.csv` (тик, для номинала).
Фильтр входа — как в `density-threshold-2026-09-18.md`: сила ≥ S % и номинал ≥ N $.

Печатает по каждому дедлайну H и по исходу (отскок / пробой) квантили
`adverse/σ_H` и `favour/σ_H` и долю касаний, у которых ход против не
превысил `a` для сетки a ∈ {0.25, 0.5, 1, 1.5, 2, 3} (это «стоп a×σ пережил
бы касание»), и долю, у которых ход за достиг `b` для той же сетки (тейк
b×σ был бы взят). Числа `a`/`b` из таблицы выбирает владелец.

Использование: excursion-sigma.py <каталог touches> <instruments.csv> [S%] [N$]
"""
import csv
import os
import sys
from collections import defaultdict

H_LIST = [60, 600, 3600, 7200]
MULTS = [0.25, 0.5, 1.0, 1.5, 2.0, 3.0]


def read_ticks(path):
    ticks = {}
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(l for l in f if not l.startswith("#")):
            sym = row.get("symbol", "")
            if sym and not sym.startswith("#"):
                try:
                    ticks[sym] = float(row["tick_size"])
                except (KeyError, ValueError):
                    pass
    return ticks


def quantiles(xs, qs=(0.5, 0.75, 0.9, 0.95)):
    if not xs:
        return [float("nan")] * len(qs)
    s = sorted(xs)
    out = []
    for q in qs:
        k = min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))
        out.append(s[k])
    return out


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(2)
    d, inst = sys.argv[1], sys.argv[2]
    s_min = float(sys.argv[3]) if len(sys.argv) > 3 else 300.0
    n_min = float(sys.argv[4]) if len(sys.argv) > 4 else 1000.0
    ticks = read_ticks(inst)
    # (H, outcome) -> list of adverse/σ ; favour/σ
    adv = defaultdict(list)
    fav = defaultdict(list)
    n_touch = 0
    n_kept = 0
    coins = 0
    for name in sorted(os.listdir(d)):
        if not (name.startswith("touches-") and name.endswith(".csv")):
            continue
        sym = name[len("touches-"):-len(".csv")]
        tick = ticks.get(sym)
        if tick is None:
            continue
        coins += 1
        with open(os.path.join(d, name), encoding="utf-8") as f:
            for row in csv.DictReader(l for l in f if not l.startswith("#")):
                n_touch += 1
                try:
                    strength = float(row["strength_w20_pct"]) if row["strength_w20_pct"] else None
                    notional = float(row["size_at_touch"]) * float(row["price_tick"]) * tick
                except (KeyError, ValueError):
                    continue
                if strength is None or strength < s_min or notional < n_min:
                    continue
                n_kept += 1
                outcome = "пробой" if row.get("ended_by_death") == "true" else "отскок"
                for h in H_LIST:
                    try:
                        sigma = float(row[f"sigma_{h}s_bps"])
                        a = float(row[f"adverse_{h}s_bps"])
                        b = float(row[f"favour_{h}s_bps"])
                    except (KeyError, ValueError):
                        continue
                    if sigma <= 0:
                        continue
                    adv[(h, outcome)].append(a / sigma)
                    fav[(h, outcome)].append(b / sigma)
    print(f"coins={coins} касаний={n_touch} после фильтра (сила ≥ {s_min:g} %, номинал ≥ ${n_min:g})={n_kept}")
    print()
    print("== ход ПРОТИВ отскока / σ_H (что должен пережить стоп a×σ): квантили p50 p75 p90 p95 и доля касаний с ходом ≤ a")
    hdr = "  H       исход    n      p50   p75   p90   p95 | " + " ".join(f"≤{m:g}σ" for m in MULTS)
    print(hdr)
    for h in H_LIST:
        for outcome in ("отскок", "пробой"):
            xs = adv[(h, outcome)]
            q = quantiles(xs)
            shares = [sum(1 for x in xs if x <= m) / len(xs) if xs else float("nan") for m in MULTS]
            print(f"  {h:5d}s  {outcome:6s} {len(xs):6d}  " + " ".join(f"{v:5.2f}" for v in q) + " | " + " ".join(f"{s:5.0%}" for s in shares))
    print()
    print("== ход ЗА отскок / σ_H (что мог взять тейк b×σ): квантили и доля касаний с ходом ≥ b")
    print(hdr.replace("≤", "≥"))
    for h in H_LIST:
        for outcome in ("отскок", "пробой"):
            xs = fav[(h, outcome)]
            q = quantiles(xs)
            shares = [sum(1 for x in xs if x >= m) / len(xs) if xs else float("nan") for m in MULTS]
            print(f"  {h:5d}s  {outcome:6s} {len(xs):6d}  " + " ".join(f"{v:5.2f}" for v in q) + " | " + " ".join(f"{s:5.0%}" for s in shares))
    print()
    print("Чтение: стоп a×σ переживает долю отскоков из первой таблицы (строки «отскок»); тейк b×σ берётся с долей из второй.")
    print("Секундная сетка режет внутрисекундные пики — обе оценки консервативны. Числа a/b назначает владелец.")


if __name__ == "__main__":
    main()
