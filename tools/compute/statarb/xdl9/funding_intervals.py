#!/usr/bin/env python3
"""TK-493 S2 (Steam Deck, только stdlib; П-37 r3.2 §5 D2, D5 i, L3-01): медианный интервал выплат фандинга каждого контракта на ФОРМИРОВАНИИ.

Вход: ~/xdl9/funding_form/<биржа>/<SYM>.csv.gz - фандинг ТОЛЬКО формирования (time_ms < 15.10 00:00 UTC; sel_deck_prep.py fundcut). Торговое окно не читается.
Для D2 ориентация пары: Y - нога с БОЛЬШИМ медианным интервалом выплат (иначе ряд d = r_Y - r_X на метках Y - «пила», П-37 §5).
Интервал - медиана часов между соседними выплатами (округление до 0,001 ч: метки бирж дрожат на миллисекунды); рядом - медиана колонки interval_h.
Выход: ~/xdl9/sel/funding_interval_form.csv: exchange, stem, n_pay, median_gap_h, median_interval_h (median_gap_h пусто при < 2 выплатах).
"""
import csv, gzip, os, statistics, sys

HOME = os.path.expanduser("~/xdl9")
SRC = os.path.join(HOME, "funding_form")
OUT = os.path.join(HOME, "sel", "funding_interval_form.csv")


def main():
    rows = []
    for ex in sorted(os.listdir(SRC)):
        d = os.path.join(SRC, ex)
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".csv.gz"):
                continue
            ts, itv = [], []
            with gzip.open(os.path.join(d, fn), "rt", encoding="utf-8", newline="") as fh:
                rd = csv.reader(fh)
                next(rd, None)
                for r in rd:
                    ts.append(int(r[0]))
                    if len(r) > 2:
                        try:
                            itv.append(float(r[2]))
                        except ValueError:
                            pass
            ts.sort()
            gaps = [round((b - a) / 3_600_000.0, 3) for a, b in zip(ts, ts[1:])]
            rows.append([ex, fn[:-7], len(ts), "" if not gaps else "%g" % statistics.median(gaps), "" if not itv else "%g" % statistics.median(itv)])
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["exchange", "stem", "n_pay", "median_gap_h", "median_interval_h"])
        w.writerows(rows)
    print("контрактов %d, с интервалом %d -> %s" % (len(rows), sum(1 for r in rows if r[3]), OUT))


if __name__ == "__main__":
    sys.exit(main())
