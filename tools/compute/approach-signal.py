#!/usr/bin/env python3
"""Свод подходов (F1 этапа F): читает `study/approaches/D<D>/<сутки>/approaches-*.csv`
и печатает таблицу замера полосы `D` — подходов на сутки, доля дошедших до
касания, время от взвода до снятия, причины снятия и расстояние взвода.

Только чтение; ничего не пишет. Числа — для `docs/findings/approach-signal-*`.
"""
import csv
import glob
import os
import statistics as st
import sys


def quant(sorted_vals, q):
    if not sorted_vals:
        return None
    i = min(len(sorted_vals) - 1, max(0, int(round(q * (len(sorted_vals) - 1)))))
    return sorted_vals[i]


def read_dir(path):
    """Все строки approaches-*.csv каталога суток: (symbol, row)."""
    out = []
    for f in sorted(glob.glob(os.path.join(path, "approaches-*.csv"))):
        sym = os.path.basename(f)[len("approaches-"):-len(".csv")]
        with open(f, newline="") as fh:
            for row in csv.DictReader(fh):
                out.append((sym, row))
    return out


def fmt(v, nd=0):
    if v is None:
        return "—"
    return f"{v:.{nd}f}"


def main(dirs):
    for d in dirs:
        base = d.rstrip("/")
        label = os.path.basename(base)
        total = 0
        days = sorted(x for x in os.listdir(base) if os.path.isdir(os.path.join(base, x)))
        per_day = []
        touch_all, dist_all, dur_touch, dur_death, dur_left = [], [], [], [], []
        reasons = {"touch": 0, "level_death": 0, "price_left": 0}
        arms = 0
        for day in days:
            rows = read_dir(os.path.join(base, day))
            per_day.append((day, len(rows)))
            total += len(rows)
            for _, r in rows:
                arms += 1
                dist_all.append(int(r["arm_dist_bps"]))
                reason = r["disarm_reason"]
                reasons[reason] = reasons.get(reason, 0) + 1
                if r["touch_start_ms"]:
                    touch_all.append(0)
                    dur_touch.append(int(r["disarm_ms"]) - int(r["arm_ms"]))
                else:
                    touch_all.append(1)
                    (dur_death if reason == "level_death" else dur_left).append(
                        int(r["disarm_ms"]) - int(r["arm_ms"]))
        if arms == 0:
            print(f"== {label}: подходов нет")
            continue
        n_touch = len(dur_touch)
        dist_s = sorted(dist_all)
        dur_ts = sorted(dur_touch)
        print(f"== {label}: подходов {arms}, суток {len(days)}, монет "
              f"{len(glob.glob(os.path.join(base, days[0], 'approaches-*.csv'))) if days else 0}")
        print("  сутки: " + ", ".join(f"{day} {n}" for day, n in per_day))
        print(f"  на сутки: {arms / max(1, len(days)):.0f}")
        print(f"  до касания: {n_touch} ({100.0 * n_touch / arms:.1f} %) · "
              f"снял уровень {reasons.get('level_death', 0)} ({100.0 * reasons.get('level_death', 0) / arms:.1f} %) · "
              f"цена ушла {reasons.get('price_left', 0)} ({100.0 * reasons.get('price_left', 0) / arms:.1f} %)")
        print(f"  расстояние взвода, bps: p10 {fmt(quant(dist_s, .1))} · p50 {fmt(quant(dist_s, .5))} · "
              f"p90 {fmt(quant(dist_s, .9))}")
        for name, vals in (("до касания, с", dur_ts), ("до смерти уровня, с", sorted(dur_death)),
                           ("до ухода цены, с", sorted(dur_left))):
            if vals:
                print(f"  время {name}: p25 {fmt(quant(vals, .25) / 1000, 1)} · "
                      f"p50 {fmt(st.median(vals) / 1000, 1)} · p90 {fmt(quant(vals, .9) / 1000, 1)} "
                      f"(n={len(vals)})")
        print()


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        args = sorted(glob.glob("study/approaches/D*"))
    if not args:
        sys.exit("нет каталогов study/approaches/D*")
    main(args)
