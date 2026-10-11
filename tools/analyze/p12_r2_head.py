"""TK-063: П-12 R2 (клетки волны TK-065) — Δ$ к базе по суткам, блочный бутстреп, max-T WY по семье, Холм по 39, KPI-часы, С2 (двое суток).
Вход data/p12r2/closes-{v,vt}-cap{0,3}-<мес>.json (portfolio-sim, closes-out); выход docs/findings/p12-r2-kpi-<режим>-2026-10-08.csv + печать.
Режим: 'free' (без потолка, cap0) или 'B2' (потолок 3). База: v — B1 (t-bid-btc4h-q1, стоп pct2 тейк tr1x1), vt — B3 (1to1, семья e117).
Метод — как tk083-kpi-analyze.py (П-12 §6–§9): сделка на сутки UTC закрытия; месяц клетки «определён» при >= 10 сделок и >= 10 суток со сделкой;
блоки ceil(n^(1/3)) суток общие для клеток; T=Δ̄/se; step-down WY внутри семьи; p семьи = минимум; Холм по m=39 (семьи вне волны — p=1)."""
import csv
import datetime as dt
import json
import math
import os
import sys
from collections import defaultdict

import numpy as np

MODE = sys.argv[1] if len(sys.argv) > 1 else "free"
CAP = "0" if MODE == "free" else "3"
D = os.environ.get("P12_DIR", "data/p12r2/")   # TK-113/В-210: data/p12r2-v171c/ — пул без TRUMP/TRX/BCH
TAG = os.environ.get("P12_TAG", "")           # суффикс файлов выхода ("-v171c")
SET = "t-bid-btc4h-q1@"
B1 = "ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800"
B3 = "ladder3x0..0.0409sw2-pct2-1to1-14400-ttl1800"
U = {"1": "u1d3", "2": "u2d3", "3": "u1"}
CELLS = {}   # имя -> (семья, вид, суффикс формы)
for n in (3, 4, 5):
    for k in "123":
        CELLS[f"g92-N{n}-{U[k]}"] = ("g92", "v", f"-pyre{n}u{k}")
for kk in "123":
    for k in "123":
        CELLS[f"g93-{U[k]}-K{kk}"] = ("g93", "v", f"-pynw{kk}u{k}")
for n in (2, 3, 4, 5):
    CELLS[f"g94-n{n}"] = ("g94", "v", f"-pyeat{n}")
    CELLS[f"g87-n{n}"] = ("g87", "vg", f"-pyfresh{n}")
for t in (0, 1, 2):
    CELLS[f"e119-tol{t}"] = ("e119", "v", f"-conv{t}a20")
CELLS["e114-halfstop-f1d2"] = ("e114", "v", "-halfstop")
CELLS["e114-halflevel-f1d2"] = ("e114", "v", "-halflevel")
# TK-120: волна a TK-115 — доля f=1/4,3/4 (суффикс f1|f3) и g95-sw3 (полное имя формы без набора через «@»)
for h in ("halfstop", "halflevel"):
    for fs, fn in (("f1", "f1d4"), ("f3", "f3d4")):
        CELLS[f"e114-{h}-{fn}"] = ("e114", "v", f"-{h}{fs}")
CELLS["g95-sw3"] = ("g95", "v", "@ladder3x0..0.0409sw3-pct2-tr1x1-14400-ttl1800")
for gi, g in (("5", "0.5"), ("10", "1"), ("20", "2")):
    for ti, t in (("1", "1d4"), ("2", "1d2"), ("4", "1")):
        CELLS[f"e117-g{g}-T{t}"] = ("e117", "vt", f"-tsl{gi}t{ti}")
M_FAM = 39
MONTHS = [f"2026-{m:02d}" for m in range(1, 11)]
H5 = 120 * 3600 * 1000
UTC = dt.timezone.utc
BASE = {"v": SET + B1, "vt": SET + B3, "vg": "g87-fresh@" + B1}
BN = {"v": "B1", "vt": "B3", "vg": "B1g"}


def day_of(ms):
    return dt.datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d")


# NORM=1 (по умолчанию): $ семей g92/g93/g94/g87 на равной экспозиции §6(2) (closes-vn-*, p12-r2-expo.py); NORM=0 — сырые $
NORM = os.environ.get("NORM", "1") == "1"
EXPO = json.load(open(D + "expo.json"))["fired"] if NORM else {}
NEEDF = 10   # §8 R2: >= 10 срабатываний механики клетки в месяце
raw = {"v": defaultdict(dict), "vt": defaultdict(dict)}   # вид -> форма -> месяц -> [(мс, $)]
rawusd = defaultdict(float)   # форма -> сырые $ (справочно)
for kind in raw:
    for m in MONTHS:
        for pre in (("vn",) if NORM and kind == "v" else (kind,)):
            j = json.load(open(f"{D}closes-{pre}-cap{CAP}-{m}.json"))
            for form, per in j.items():
                for _p, caps in per.items():
                    for _c, lst in caps.items():
                        raw[kind][form][m] = [(int(a), float(b)) for a, b in lst]
        if NORM and kind == "v" and os.path.exists(f"{D}closes-v-cap{CAP}-{m}.json"):   # v171c: сырых нет — raw_total пуст
            j = json.load(open(f"{D}closes-v-cap{CAP}-{m}.json"))
            for form, per in j.items():
                for _p, caps in per.items():
                    for _c, lst in caps.items():
                        rawusd[form] += sum(float(b) for _a, b in lst)
cal = [(dt.date(2026, 1, 1) + dt.timedelta(i)).isoformat() for i in range((dt.date(2026, 10, 2) - dt.date(2026, 1, 1)).days + 1)]
di = {d: i for i, d in enumerate(cal)}


def series(kind, form):
    arr = np.zeros(len(cal))
    nt, dtr = defaultdict(int), defaultdict(set)
    for m, lst in raw[kind].get(form, {}).items():
        for ms, v in lst:
            d = day_of(ms)
            if d in di:
                arr[di[d]] += v
            nt[m] += 1
            dtr[m].add(d)
    return arr, nt, dtr, raw[kind].get(form, {})


def kpi_share(lst, m):
    y, mo = int(m[:4]), int(m[5:])
    t0 = int(dt.datetime(y, mo, 1, tzinfo=UTC).timestamp() * 1000)
    t1 = int(dt.datetime(y + (mo == 12), mo % 12 + 1, 1, tzinfo=UTC).timestamp() * 1000)
    t1 = min(t1, int(dt.datetime(2026, 10, 3, tzinfo=UTC).timestamp() * 1000))
    eq = hi = 0.0
    last = t0
    bad = 0
    for ms, v in sorted(lst):
        eq += v
        if eq > hi:
            bad += max(0, ms - last - H5)
            hi = eq
            last = ms
    bad += max(0, t1 - last - H5)
    return bad / (t1 - t0), eq


def defined_of(nt, dtr, form=None):
    ex = EXPO.get(form[len(SET):] if form and form.startswith(SET) else form)   # ключи expo.json — без префикса набора (TK-120: иначе §8 не работал)
    return [m for m in MONTHS if nt[m] >= 10 and len(dtr[m]) >= 10 and (ex is None or ex.get(m, 0) >= NEEDF)]


raw["vg"] = raw["v"]
S = {}   # имя -> series; базы 'B1','B3'
for nm, kind in (("B1", "v"), ("B3", "vt"), ("B1g", "vg")):
    S[nm] = series(kind, BASE[kind])
for c, (f, kind, suf) in CELLS.items():
    form = SET + suf[1:] if suf.startswith("@") else BASE[kind] + suf
    if form in raw[kind]:
        S[c] = series(kind, form)
        S[c] = S[c] + (form,)
    else:
        print("НЕТ формы", c, form)
cells = [c for c in CELLS if c in S]
