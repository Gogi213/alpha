"""TK-083: П-12 расширение (7 клеток) — KPI-часы, Δ$ на $500 по суткам к B1, блочный бутстреп, max-T WY по семье, С2.
Вход data/tk083/kpi/{closes[B2]-<мес>.json, rounds-all.csv}; выход docs/findings/p12-ext-kpi[-B2]-2026-10-08.csv + печать.
Сделка относится к суткам UTC закрытия. Месяц клетки «определён»: сделок >= 10 и суток со сделкой >= 10 (П-10 §10.1)."""
import csv
import datetime as dt
import math
import sys
import json
from collections import defaultdict

import numpy as np

SUF = sys.argv[1] if len(sys.argv) > 1 else ""      # "" — без потолка, "B2" — потолок 3
B1 = "ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800"
CELLS = {
    "x-stop-pct2.5": ("x-stop", "ladder3x0..0.0409sw2-pct2.5-tr1x1-14400-ttl1800"),
    "x-dl-21600": ("x-deadline", "ladder3x0..0.0409sw2-pct2-tr1x1-21600-ttl1800"),
    "x-dl-28800": ("x-deadline", "ladder3x0..0.0409sw2-pct2-tr1x1-28800-ttl1800"),
    "x-entry-n.1-t.5": ("x-entry", "ladder3x0.00409..0.02045sw2-pct2-tr1x1-14400-ttl1800"),
    "x-entry-n.1-t2": ("x-entry", "ladder3x0.00409..0.0818sw2-pct2-tr1x1-14400-ttl1800"),
    "x-entry-n.25-t.5": ("x-entry", "ladder3x0.010225..0.02045sw2-pct2-tr1x1-14400-ttl1800"),
    "x-entry-n.25-t2": ("x-entry", "ladder3x0.010225..0.0818sw2-pct2-tr1x1-14400-ttl1800"),
}
NAME = {v[1]: k for k, v in CELLS.items()}
NAME[B1] = "B1"
H5 = 120 * 3600 * 1000
MONTHS = [f"2026-{m:02d}" for m in range(1, 11)]
D = "data/tk083/kpi/"
UTC = dt.timezone.utc


def day_of(ms):
    return dt.datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d")


cl = defaultdict(dict)   # клетка -> месяц -> [(мс закрытия, $)]
for m in MONTHS:
    j = json.load(open(f"{D}closes{SUF}-{m}.json"))
    for form, per in j.items():
        if form in NAME:
            for _p, caps in per.items():
                for _c, lst in caps.items():
                    cl[NAME[form]][m] = [(int(a), float(b)) for a, b in lst]
cal = [(dt.date(2026, 1, 1) + dt.timedelta(i)).isoformat() for i in range((dt.date(2026, 10, 2) - dt.date(2026, 1, 1)).days + 1)]
di = {d: i for i, d in enumerate(cal)}
daily = {c: np.zeros(len(cal)) for c in cl}
ntr = {c: defaultdict(int) for c in cl}
dtr = {c: defaultdict(set) for c in cl}
for c in cl:
    for m, lst in cl[c].items():
        for ms, v in lst:
            d = day_of(ms)
            if d in di:
                daily[c][di[d]] += v
            ntr[c][m] += 1
            dtr[c][m].add(d)


def kpi_share(lst, m):
    """доля часов месяца с ожиданием нового максимума > 5 сут; старт месяца — максимум 0; хвост входит."""
    y, mo = int(m[:4]), int(m[5:])
    t0 = int(dt.datetime(y, mo, 1, tzinfo=UTC).timestamp() * 1000)
    t1 = int(dt.datetime(y + (mo == 12), mo % 12 + 1, 1, tzinfo=UTC).timestamp() * 1000)
    t1 = min(t1, int(dt.datetime(2026, 10, 3, tzinfo=UTC).timestamp() * 1000))
    eq = hi = 0.0
    last = t0
    bad = longest = 0
    for ms, v in sorted(lst):
        eq += v
        if eq > hi:
            gap = ms - last
            longest = max(longest, gap)
            bad += max(0, gap - H5)
            hi = eq
            last = ms
    gap = t1 - last
    longest = max(longest, gap)
    bad += max(0, gap - H5)
    return bad / (t1 - t0), longest / 3.6e6, eq


defined = {c: [m for m in MONTHS if ntr[c][m] >= 10 and len(dtr[c][m]) >= 10] for c in cl}
rows = []
for c in cl:
    for m in MONTHS:
        s, lg, eq = kpi_share(cl[c].get(m, []), m)
        rows.append([SUF or "free", c, m, ntr[c][m], round(eq, 1), round(s, 4), round(lg, 1), int(m in defined[c])])
