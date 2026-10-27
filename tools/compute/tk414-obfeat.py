#!/usr/bin/env python3
"""TK-414: признаки стакана ob200 для событий Bybit linear (окно B). Протокол (заморожен коммитом до счёта): docs/research/P-TK414-ob200-2026-10-27.md.
Использование: tk414-obfeat.py <oifund-events.csv> <каталог срезов tk414-obmin> <выход-events.csv>
Срез события — конец минуты floor(t)−1 (строго до метки минуты события, без заглядывания вперёд); база — срезы [t−минB−360 мин, t−минB), только прошлое.
Колонки: ob_dz — глубина ≤25 бп стороны, которую ест нога (аски для пампа, биды для дампа), в долях медианы базы; ob_imb — знак·(dB−dA)/(dB+dA);
ob_sp — спред в долях медианы спреда базы. Пусто — нет срезов (нет файла/суток, база < 60 срезов или последний срез старше 5 мин)."""
import csv, os, sys
from bisect import bisect_left, bisect_right
from datetime import datetime, timezone

ev_f, root, out_f = sys.argv[1:4]
_c = {}


def minutes(sym):
    if sym not in _c:
        a = []
        d = os.path.join(root, sym)
        if os.path.isdir(d):
            for fn in sorted(os.listdir(d)):
                with open(os.path.join(d, fn), newline="") as f:
                    for line in f:
                        p = line.split(",")
                        a.append((int(p[0]), float(p[1]), float(p[2]), float(p[3]), float(p[4])))
        a.sort()
        _c[sym] = ([x[0] for x in a], a)
    return _c[sym]


def med(x):
    y = sorted(x)
    return y[len(y) // 2]


def feat(sym, t, mins, s):
    ks, a = minutes(sym)
    te = int(datetime.strptime(t, "%Y-%m-%dT%H:%M").replace(tzinfo=timezone.utc).timestamp())
    te = te // 60 * 60 - 60  # последний срез: минута перед меткой
    j = bisect_right(ks, te) - 1
    if j < 0 or te - ks[j] > 300:
        return "", "", ""
    lo = bisect_left(ks, te - mins * 60 - 360 * 60)
    hi = bisect_left(ks, te - mins * 60)
    base = a[lo:hi]
    if len(base) < 60:
        return "", "", ""
    _, bb, ba, db, da = a[j]
    opp = da if s > 0 else db
    mo = med([x[4] if s > 0 else x[3] for x in base])
    msp = med([(x[2] - x[1]) / ((x[1] + x[2]) / 2) for x in base])
    if mo <= 0 or msp <= 0 or db + da <= 0:
        return "", "", ""
    return "%.3f" % (opp / mo), "%.3f" % (s * (db - da) / (db + da)), "%.3f" % (((ba - bb) / ((ba + bb) / 2)) / msp)


n = nof = 0
with open(ev_f, newline="", encoding="utf-8") as fi, open(out_f, "w", newline="", encoding="utf-8") as fo:
    rd = csv.DictReader(fi)
    w = csv.DictWriter(fo, rd.fieldnames + ["ob_dz", "ob_imb", "ob_sp"])
    w.writeheader()
    for r in rd:
        r["ob_dz"], r["ob_imb"], r["ob_sp"] = feat(r["инструмент"], r["t_событие"], int(r["минB"]) if r["минB"] else 0, int(r["знак"]))
        n += 1
        nof += r["ob_dz"] == ""
        w.writerow(r)
print("событий", n, "без признака стакана", nof, flush=True)
