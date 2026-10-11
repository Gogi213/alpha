#!/usr/bin/env python3
"""H5: «монета считается монетой» — подготовка решения владельца (handoff-2026-09-20.md).

Из [touches-tail-2026-09-19.md](../findings/touches-tail-2026-09-19.md): 23 монеты пула дают меньше
100 касаний за двое суток, и хвост в 1–3 круга формирует крайние значения в по-монетных таблицах.
Здесь считается, **сколько монет и сколько кругов остаётся** при порогах касаний/сутки
{10, 30, 100}: по касаниям ночного H3 (`study/touches/<сутки>/`, трекер `notional $10k`) и по кругам
из `forms.csv` любой сетки (число кругов на монету — карта, не P&L: одни и те же сигналы входят в
разные формы).

    python3 tools/compute/pool-tail.py --touches-root study/touches \\
        --forms b5/v68lat-base-any/forms.csv --thresholds 10,30,100

Число порога называет владелец — скрипт даёт цену каждого варианта в монетах и кругах.
"""
import argparse
import collections
import csv
import glob
import os
import statistics
import sys


def load_touches(root):
    """(монета, сутки) -> касаний; список суток."""
    paths = glob.glob(os.path.join(root, "touches-*.csv"))
    if not paths:
        paths = glob.glob(os.path.join(root, "*", "touches-*.csv"))
    per = collections.Counter()
    days = set()
    for f in sorted(paths):
        for r in csv.DictReader(open(f, encoding="utf-8")):
            sym = r.get("symbol") or os.path.basename(f)[8:-4]
            per[(sym, r["day_utc"])] += 1
            days.add(r["day_utc"])
    return per, sorted(days)


def load_circles(path):
    """монета -> кругов по всем формам сетки (карта, не P&L)."""
    if not path:
        return {}
    per = collections.Counter()
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(l for l in f if not l.startswith("#")):
            per[r["symbol"]] += int(float(r["n_fills"] or 0))
    return per


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--touches-root", required=True, help="study/touches (по суткам) или каталог прогона")
    p.add_argument("--forms", help="b5/<метка>/forms.csv — чтобы показать цену порога в кругах")
    p.add_argument("--thresholds", default="10,30,100", help="пороги касаний/сутки через запятую")
    p.add_argument("--out", help="csv: монета, касаний/сутки, кругов")
    a = p.parse_args()

    per, days = load_touches(a.touches_root)
    if not per:
        print(f"touches: строк нет в {a.touches_root}")
        return 0
    nd = len(days)
    coins = sorted({s for s, _ in per})
    per_day = {s: [per[(s, d)] for d in days if (s, d) in per] for s in coins}
    med = {s: statistics.median(v) for s, v in per_day.items() if v}
    circles = load_circles(a.forms)
    total_circles = sum(circles.values())
    thresholds = [int(x) for x in a.thresholds.split(",") if x.strip()]

    print(f"# pool-tail: суток {nd} ({days[0]}…{days[-1]}), монет с касаниями {len(coins)}, "
          f"касаний всего {sum(per.values())}, кругов в сетке {total_circles}"
          + (f" ({os.path.basename(os.path.dirname(a.forms))})" if a.forms else " (сетка не задана)"))
    print(f"{'порог':>7}{'монет':>8}{'доля монет':>12}{'касаний/сутки':>15}{'кругов':>9}{'доля кругов':>13}")
    for t in thresholds:
        keep = [s for s in coins if med.get(s, 0) >= t]
        sig = sum(med.get(s, 0) for s in keep)
        cir = sum(circles.get(s, 0) for s in keep)
        share = cir / total_circles if total_circles else 0.0
        print(f"{t:>7}{len(keep):>8}{len(keep)/len(coins):>11.0%}{sig:>15.0f}{cir:>9}{share:>12.0%}")
    zero = [s for s in coins if med.get(s, 0) == 0]
    if zero:
        print(f"# монет без касаний ни в одни сутки: {len(zero)} — {', '.join(zero[:12])}"
              + (" …" if len(zero) > 12 else ""))
    top = sorted(coins, key=lambda s: -med.get(s, 0))
    print("# самые частые по касаниям/сутки: "
          + ", ".join(f"{s[:-4]} {med[s]:.0f}" for s in top[:8]))
    print("# самые редкие: " + ", ".join(f"{s[:-4]} {med[s]:.0f}" for s in top[-8:]))
    if a.out:
        with open(a.out, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["symbol", "touches_per_day_median", "circles_all_forms"])
            for s in coins:
                w.writerow([s, f"{med.get(s, 0):.1f}", circles.get(s, 0)])
        print(f"csv: {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
