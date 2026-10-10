"""TK-063: П-12 R1 (155 клеток TK-064, пороги до исходов) — Δ$ к B1 по суткам, блочный бутстреп, max-T WY внутри семьи, Холм по 39, KPI-часы, С2.
Вход data/tk063r1/closes-cap{0,3}-<мес>.json (p12-r1-psim.py, portfolio-sim --closes-out); выход docs/findings/p12-r1-kpi-<режим>-2026-10-08.csv + печать.
Режим 'free' (cap0) или 'B2' (cap3). Метод — как p12-r2-analyze.py (П-12 §6–§9). Семья = префикс имени клетки до первого '-'. Месяцы фев…окт
(январь — калибровка порогов). Фильтр R1 только убирает сделки: экспозиция не растёт, нормировка §6(2) не нужна.
Холм по m=39: p семей R2 — из p12-r2-kpi-free; не посчитанные семьи (e112/e116/e106/e133/e65/g95) — p=1 (консервативно)."""
import csv, datetime as dt, json, math, os, sys
from collections import defaultdict
import numpy as np

MODE = sys.argv[1] if len(sys.argv) > 1 else "free"
CAP = "0" if MODE == "free" else "3"
JAN = os.environ.get("R1_JAN") == "1"   # TK-120/В-211: v171c, окно янв–сен — data/tk120-r1/ (closes drop2/r1), январь со своими порогами
D = os.environ.get("R1_DIR", "data/tk063r1/")
MS = (["jan"] if JAN else []) + ["feb", "mar", "apr", "may", "jun", "jul", "aug", "sep"] + ([] if JAN else ["oct"])
MONTHS = [f"2026-{i:02d}" for i in range(1 if JAN else 2, 10 if JAN else 11)]
M_FAM = 39
H5 = 120 * 3600 * 1000
UTC = dt.timezone.utc
raw = defaultdict(dict)  # клетка -> месяц -> [(мс,$)]
for mn, m in zip(MS, MONTHS):
    j = json.load(open(f"{D}closes-cap{CAP}-{mn}.json"))
    for cell, per in j.items():
        for _p, caps in per.items():
            for _c, lst in caps.items():
                raw[cell][m] = [(int(a), float(b)) for a, b in lst]
cal = [(dt.date(2026, 1 if JAN else 2, 1) + dt.timedelta(i)).isoformat() for i in range((dt.date(2026, 10, 2) - dt.date(2026, 1 if JAN else 2, 1)).days + 1)]
di = {d: i for i, d in enumerate(cal)}


def day_of(ms):
    return dt.datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d")


def series(cell):
    arr = np.zeros(len(cal)); nt = defaultdict(int); dtr = defaultdict(set)
    for m, lst in raw.get(cell, {}).items():
        for ms, v in lst:
            d = day_of(ms)
            if d in di:
                arr[di[d]] += v
            nt[m] += 1; dtr[m].add(d)
    return arr, nt, dtr


def kpi_share(lst, m):
    y, mo = int(m[:4]), int(m[5:])
    t0 = int(dt.datetime(y, mo, 1, tzinfo=UTC).timestamp() * 1000)
    t1 = min(int(dt.datetime(y + (mo == 12), mo % 12 + 1, 1, tzinfo=UTC).timestamp() * 1000), int(dt.datetime(2026, 10, 3, tzinfo=UTC).timestamp() * 1000))
    eq = hi = 0.0; last = t0; bad = 0
    for ms, v in sorted(lst):
        eq += v
        if eq > hi:
            bad += max(0, ms - last - H5); hi = eq; last = ms
    bad += max(0, t1 - last - H5)
    return bad / (t1 - t0), eq


def defined_of(nt, dtr):
    return [m for m in MONTHS if nt[m] >= 10 and len(dtr[m]) >= 10]


