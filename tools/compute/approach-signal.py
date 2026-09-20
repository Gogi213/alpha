#!/usr/bin/env python3
"""Свод подходов (F1 этапа F): читает `study/approaches/D<D>/<сутки>/approaches-*.csv`
и печатает замер полосы `D` — подходов на сутки, доля дошедших до касания,
время от взвода до снятия, причины снятия и расстояние взвода, **отдельно по
полам возраста В-71** (любой / ≥ 15 мин / ≥ 45 мин): без пола подход ловит
любой уровень в полосе `D` от чужой лучшей цены, и это видно по числам.

Только чтение; ничего не пишет. Числа — для `docs/findings/approach-signal-*`.
"""
import csv
import glob
import os
import statistics as st
import sys

# Полы возраста (В-71 — 15 мин; 45 мин — семья «старых стен» из floors-balance).
FLOORS = [(0, "любой"), (900_000, "≥15 мин"), (2_700_000, "≥45 мин")]


def quant(sorted_vals, q):
    if not sorted_vals:
        return None
    i = min(len(sorted_vals) - 1, max(0, int(round(q * (len(sorted_vals) - 1)))))
    return sorted_vals[i]


def fmt(v, nd=0):
    return "—" if v is None else f"{v:.{nd}f}"


class Acc:
    def __init__(self):
        self.n = 0
        self.bid = 0
        self.touch = 0
        self.death = 0
        self.left = 0
        self.dist = []
        self.dur_touch = []
        self.dur_death = []
        self.dur_left = []

    def add(self, side, dist, dur, reason, touched):
        self.n += 1
        self.bid += side == "bid"
        self.dist.append(dist)
        if touched:
            self.touch += 1
            self.dur_touch.append(dur)
        elif reason == "level_death":
            self.death += 1
            self.dur_death.append(dur)
        else:
            self.left += 1
            self.dur_left.append(dur)


def read_dir(path, accs):
    """Один проход по файлам суток: колонки — по именам шапки."""
    for f in sorted(glob.glob(os.path.join(path, "approaches-*.csv"))):
        with open(f, newline="") as fh:
            r = csv.reader(fh)
            head = next(r)
            i_side, i_age = head.index("side"), head.index("age_ms")
            i_dist, i_arm = head.index("arm_dist_bps"), head.index("arm_ms")
            i_dis, i_tch = head.index("disarm_ms"), head.index("touch_start_ms")
            i_rs = head.index("disarm_reason")
            for row in r:
                age, dist = int(row[i_age]), int(row[i_dist])
                dur = int(row[i_dis]) - int(row[i_arm])
                touched = row[i_tch] != ""
                for floor, acc in accs:
                    if age >= floor:
                        acc.add(row[i_side], dist, dur, row[i_rs], touched)


def report(base):
    days = sorted(x for x in os.listdir(base) if os.path.isdir(os.path.join(base, x)))
    per_day, per_day_floor = [], None
    accs = [(floor, Acc()) for floor, _ in FLOORS]
    for day in days:
        acc_before = [a.n for _, a in accs]
        read_dir(os.path.join(base, day), accs)
        per_day.append((day, accs[0][1].n - acc_before[0]))
    label = os.path.basename(base)
    total = accs[0][1].n
    per_day_floor = accs
    print(f"== {label}: подходов всего {total}, суток {len(days)}, монет {len(days) and len(glob.glob(os.path.join(base, days[0], 'approaches-*.csv')))}")
    print("  сутки: " + ", ".join(f"{d} {n}" for d, n in per_day))
    for floor, acc in accs:
        if acc.n == 0:
            print(f"  пол {dict(FLOORS)[floor]}: пусто")
            continue
        nd = sorted(acc.dist)
        dts = sorted(acc.dur_touch)
        ddd = sorted(acc.dur_death)
        print(f"  пол {dict(FLOORS)[floor]}: подходов {acc.n} ({acc.n / max(1, len(days)):.0f}/сутки, "
              f"bid {100.0 * acc.bid / acc.n:.1f} %), до касания {acc.touch} ({100.0 * acc.touch / acc.n:.1f} %) · "
              f"снял уровень {100.0 * acc.death / acc.n:.1f} % · цена ушла {100.0 * acc.left / acc.n:.1f} %")
        print(f"      расстояние взвода, bps: p10 {fmt(quant(nd, .1))} · p50 {fmt(quant(nd, .5))} · p90 {fmt(quant(nd, .9))}; "
              f"время до касания, с: p25 {fmt(quant(dts, .25) / 1000, 1)} · p50 {fmt(st.median(dts) / 1000, 1) if dts else '—'} · "
              f"p90 {fmt(quant(dts, .9) / 1000, 1)}; до смерти уровня, с: p50 "
              f"{fmt(st.median(ddd) / 1000, 1) if ddd else '—'}")
    print()


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        args = sorted(glob.glob("study/approaches/D*"))
    if not args:
        sys.exit("нет каталогов study/approaches/D*")
    for d in args:
        report(d.rstrip("/"))
