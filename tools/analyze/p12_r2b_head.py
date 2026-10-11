"""TK-120: П-12 R2-B (31 клетка: e112 tape W15/30/60 × q33/50/67/80, e116 cxl, e133 chase, e65 wall2, g95 sw4/sw5) — голова для p12lib.pack(…, False).
Сделки R2-B: data/p12r2b/closes-r2bd-cap0-<мес>.json (psim v171c, drop TRUMP/TRX/BCH). Имена форм месячные (Q из порогов месяца) -> клетка по порядку cells.txt.
База B1 (free, cap0): coin-rows.csv (data/j120/), отбор по b1keys-cap0.csv (2750 сделок после busy-replay), без TRUMP/TRX/BCH и без трёх выпавших символо-суток R2-B (r2b-failed-symdays.csv); $ = qty*entry_vwap*net_bps/1e4,
ряд по суткам закрытия (exit_ms), как p12-r2b-b1keys.py. B2 (cap3): базы B1 нет — режим только free. g95: сырые $ без равной экспозиции §6(2) (следующий шаг)."""
import csv, datetime as dt, json, os, sys
from collections import defaultdict
import numpy as np

MODE = sys.argv[1] if len(sys.argv) > 1 else "free"
if MODE != "free":
    sys.exit("p12_r2b_head: только free (cap0)")
D = os.environ.get("P12_DIR", "data/p12r2b/")
MONTHS = [f"2026-{m:02d}" for m in range(1, 10)]
UTC = dt.timezone.utc
H5 = 120 * 3600 * 1000
DROP = {"TRUMPUSDT", "TRXUSDT", "BCHUSDT"}
CUT = {("2026-08-31", "ADAUSDT"), ("2026-08-31", "GRAMUSDT"), ("2026-09-14", "STXUSDT")}
BN = {"v": "B1"}
QS = (33, 50, 67, 80)
ORDER = []   # порядок клеток = cells.txt
for fam, pre in (("e112", "tape"), ("e116", "cxl")):
    for w in (15, 30, 60):
        for q in QS:
            ORDER.append((f"{fam}-W{w}-q{q}", fam))
for n in ("6040", "12080", "24160"):
    ORDER.append((f"e133-chase{n}", "e133"))
ORDER += [("e65-wall2", "e65"), ("e65-wall2x", "e65"), ("g95-sw4", "g95"), ("g95-sw5", "g95")]
CELLS = {n: (f, "v", None) for n, f in ORDER}
M_FAM = 39


def day_of(ms):
    return dt.datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d")


cal = [(dt.date(2026, 1, 1) + dt.timedelta(i)).isoformat() for i in range((dt.date(2026, 10, 2) - dt.date(2026, 1, 1)).days + 1)]
di = {d: i for i, d in enumerate(cal)}
raw = {}   # имя -> месяц -> [(мс, $)]
for m in MONTHS:
    j = json.load(open(f"{D}closes-r2bd-cap0-{m}.json"))
    forms = list(j)
    assert len(forms) == len(ORDER), (m, len(forms))
    for (name, _), f in zip(ORDER, forms):
        out = []
        for per in j[f].values():
            for l in per.values():
                out += [(int(a), float(b)) for a, b in l]
        raw.setdefault(name, {})[m] = out
b1 = defaultdict(list)
KEYS = {(r["month"], r["symbol"], int(r["t0_ms"]), int(r["exit_ms"])) for r in csv.DictReader(open("data/j120/b1keys-cap0.csv", encoding="utf-8"))}   # 2750 сделок B1 после busy-replay (Летопись)
for r in csv.DictReader(open("data/j120/coin-rows.csv", encoding="utf-8")):
    if r["form"] != "B1" or r["symbol"] in DROP or r["month"] not in MONTHS:
        continue
    ex = int(r["exit_ms"])
    if (r["month"], r["symbol"], int(r["t0_ms"]), ex) not in KEYS:
        continue
    if (day_of(int(r["t0_ms"])), r["symbol"]) in CUT:
        continue
    b1[r["month"]].append((ex, float(r["qty"]) * float(r["entry_vwap"]) * float(r["net_bps"]) / 1e4))
raw["B1"] = b1


def series(name):
    arr = np.zeros(len(cal)); nt, dtr = defaultdict(int), defaultdict(set)
    for m, lst in raw[name].items():
        for ms, v in lst:
            d = day_of(ms)
            if d in di:
                arr[di[d]] += v
            nt[m] += 1; dtr[m].add(d)
    return arr, nt, dtr, raw[name]


S = {n: series(n) for n in ["B1"] + [c for c, _ in ORDER]}


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


def defined_of(nt, dtr, form=None):
    return [m for m in MONTHS if nt[m] >= 10 and len(dtr[m]) >= 10]
