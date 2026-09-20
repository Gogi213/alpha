#!/usr/bin/env python3
"""Свод подходов (F1 этапа F): читает `study/approaches/D<D>/<сутки>/approaches-*.csv`
и печатает замер полосы `D` — подходов на сутки, доля дошедших до касания,
время от взвода до снятия, причины снятия и расстояние взвода, **отдельно по
полам возраста В-71** (любой / ≥ 15 мин / ≥ 45 мин): без пола подход ловит
любой уровень в полосе `D` от чужой лучшей цены, и это видно по числам.

Память — O(число корзин), не O(число строк): на 10 млн подходов списки
значений не помещаются. Расстояние взвода — целые bps (корзина = 1 bps),
время — целые секунды (корзина = 1 с): квантили по гистограмме **точные**, без
выборки. Только чтение; ничего не пишет.
"""
import csv
import glob
import os
import sys

# Полы возраста (В-71 — 15 мин; 45 мин — семья «старых стен» из floors-balance).
FLOORS = [(0, "любой"), (900_000, "≥15 мин"), (2_700_000, "≥45 мин")]


def quant(hist, total, q):
    """Квантиль по гистограмме {значение: count} — без списка значений."""
    if total <= 0:
        return None
    target = q * (total - 1)
    seen = 0
    for v in sorted(hist):
        seen += hist[v]
        if seen - 1 >= target:
            return v
    return max(hist)


def hist_line(hist, total, unit=1.0, nd=1):
    if total <= 0:
        return "—"
    q = lambda x: quant(hist, total, x) / unit
    return f"p25 {q(.25):.{nd}f} · p50 {q(.5):.{nd}f} · p90 {q(.9):.{nd}f}"


class Acc:
    """Счётчики и гистограммы одного пола возраста."""

    def __init__(self):
        self.n = self.bid = self.touch = self.death = self.left = 0
        self.dist = {}
        self.dur_touch = {}
        self.dur_death = {}
        self.dur_left = {}

    def add(self, side, dist, dur, reason, touched):
        self.n += 1
        self.bid += side == "bid"
        self.dist[dist] = self.dist.get(dist, 0) + 1
        sec = max(0, dur // 1000)
        if touched:
            self.touch += 1
            self.add_dur(self.dur_touch, sec)
        elif reason == "level_death":
            self.death += 1
            self.add_dur(self.dur_death, sec)
        else:
            self.left += 1
            self.add_dur(self.dur_left, sec)

    @staticmethod
    def add_dur(h, sec):
        h[sec] = h.get(sec, 0) + 1


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
    accs = [(floor, Acc()) for floor, _ in FLOORS]
    per_day = []
    for day in days:
        before = accs[0][1].n
        read_dir(os.path.join(base, day), accs)
        per_day.append((day, accs[0][1].n - before))
    label = os.path.basename(base)
    total = accs[0][1].n
    coins = len(glob.glob(os.path.join(base, days[0], "approaches-*.csv"))) if days else 0
    print(f"== {label}: подходов всего {total}, суток {len(days)}, монет {coins}")
    print("  сутки: " + ", ".join(f"{d} {n}" for d, n in per_day))
    for floor, acc in accs:
        name = dict(FLOORS)[floor]
        if acc.n == 0:
            print(f"  пол {name}: пусто")
            continue
        print(
            f"  пол {name}: подходов {acc.n} ({acc.n / max(1, len(days)):.0f}/сутки, "
            f"bid {100.0 * acc.bid / acc.n:.1f} %), до касания {acc.touch} "
            f"({100.0 * acc.touch / acc.n:.1f} %) · снял уровень "
            f"{100.0 * acc.death / acc.n:.1f} % · цена ушла {100.0 * acc.left / acc.n:.1f} %"
        )
        print(
            f"      расстояние взвода, bps: {hist_line(acc.dist, acc.n, 1.0, 0)}; "
            f"время до касания, с: {hist_line(acc.dur_touch, acc.touch)}; "
            f"до смерти уровня, с: {hist_line(acc.dur_death, acc.death)}; "
            f"до ухода цены, с: {hist_line(acc.dur_left, acc.left)}"
        )
    print()


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        args = sorted(glob.glob("study/approaches/D*"))
    if not args:
        sys.exit("нет каталогов study/approaches/D*")
    for d in args:
        report(d.rstrip("/"))
