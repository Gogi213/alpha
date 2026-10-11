#!/usr/bin/env python3
"""TK-113/В-210/В-211: ext (7 клеток П-12 + B1) по пулу v171c (portfolio-sim --drop, /data/tk0113/drop2/ext) в едином окне 01.01–30.09.
Вход data/tk113/ext/closes-cap{0,3}-<мес>.json; выход docs/findings/ext-v171c-2026-10-09.{csv,md}. Описание, не вердикт: ворота В-208 (Холм/WY) не прогонялись."""
import csv, datetime as dt, json
from collections import defaultdict
import numpy as np

D = "data/tk113/ext/"
UTC = dt.timezone.utc
B1 = "ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800"
CELLS = {"B1": B1,
         "x-stop-pct2.5": "ladder3x0..0.0409sw2-pct2.5-tr1x1-14400-ttl1800",
         "x-dl-21600": "ladder3x0..0.0409sw2-pct2-tr1x1-21600-ttl1800",
         "x-dl-28800": "ladder3x0..0.0409sw2-pct2-tr1x1-28800-ttl1800",
         "x-entry-n.1-t.5": "ladder3x0.00409..0.02045sw2-pct2-tr1x1-14400-ttl1800",
         "x-entry-n.1-t2": "ladder3x0.00409..0.0818sw2-pct2-tr1x1-14400-ttl1800",
         "x-entry-n.25-t.5": "ladder3x0.010225..0.02045sw2-pct2-tr1x1-14400-ttl1800",
         "x-entry-n.25-t2": "ladder3x0.010225..0.0818sw2-pct2-tr1x1-14400-ttl1800"}
NAME = {v: k for k, v in CELLS.items()}
MONTHS = [f"2026-{m:02d}" for m in range(1, 10)]
H5 = 120 * 3600 * 1000
cal = [(dt.date(2026, 1, 1) + dt.timedelta(i)).isoformat() for i in range(273)]   # 01.01–30.09
di = {d: i for i, d in enumerate(cal)}
day_of = lambda ms: dt.datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d")


def kpi_share(lst, m):
    y, mo = int(m[:4]), int(m[5:])
    t0 = int(dt.datetime(y, mo, 1, tzinfo=UTC).timestamp() * 1000)
    t1 = int(dt.datetime(y + (mo == 12), mo % 12 + 1, 1, tzinfo=UTC).timestamp() * 1000)
    eq = hi = 0.0; last = t0; bad = 0
    for ms, v in sorted(lst):
        eq += v
        if eq > hi:
            bad += max(0, ms - last - H5); hi = eq; last = ms
    bad += max(0, t1 - last - H5)
    return bad / (t1 - t0)


def sr(x):
    return float(x.mean() / x.std(ddof=1)) if len(x) > 2 and x.std(ddof=1) > 0 else 0.0


rows = []
for cap, mode in (("0", "free"), ("3", "B2")):
    cl = defaultdict(dict)
    for m in MONTHS:
        for form, per in json.load(open(f"{D}closes-cap{cap}-{m}.json")).items():
            for _p, caps in per.items():
                for _c, lst in caps.items():
                    cl[NAME[form]][m] = [(int(a), float(b)) for a, b in lst]
    dly = {}
    for c in CELLS:
        a = np.zeros(len(cal))
        for m, lst in cl[c].items():
            for ms, v in lst:
                a[di[day_of(ms)]] += v
        dly[c] = a
    mon = np.array([d[:7] for d in cal])
    for c in CELLS:
        n = sum(len(cl[c].get(m, [])) for m in MONTHS)
        msr = [sr(dly[c][mon == m]) for m in MONTHS]
        dfn = [m for m in MONTHS if len(cl[c].get(m, [])) >= 10 and len({day_of(ms) for ms, _ in cl[c][m]}) >= 10]
        kp = float(np.mean([kpi_share(cl[c][m], m) for m in dfn])) if dfn else float("nan")
        rows.append((mode, c, n, round(float(dly[c].sum()), 1), round(float(dly[c].sum() - dly["B1"].sum()), 1), round(sr(dly[c]), 3),
                     sum(x > 0 for x in msr), len(MONTHS), round(kp, 4), len(dfn)))
OUT = "docs/findings/ext-v171c-2026-10-09"
with open(OUT + ".csv", "w", encoding="utf-8", newline="") as fh:
    w = csv.writer(fh, lineterminator="\n")
    w.writerow(["mode", "cell", "trades", "usd_jan_sep", "d_usd_vs_B1", "sharpe_day", "months_sr_pos", "months", "kpi_share_mean", "months_defined"])
    w.writerows(rows)
L = ["# ext (7 клеток П-12) по пулу v171c, окно янв–сен 2026", "",
     "TK-113/В-210/В-211, **описание, не вердикт**. Пул v171c (без TRUMP/TRX/BCH; portfolio-sim --drop на готовых деревьях TK-083, потолок B2 применён заново). "
     "ВСЕ числа — в одном окне 01.01–30.09 (октябрь не входит; январь — калибровка П-12 §4, оговорка). Шарп — суточный ряд янв–сен, без годовой нормировки; "
     "мес. SR>0 — из 9; доля KPI — среднее по «определённым» месяцам (>= 10 сделок и >= 10 суток). Ворота В-208 (Холм/WY, C2) не прогонялись.", ""]
for mode in ("free", "B2"):
    L += [f"## {mode}", "", "| клетка | сделок | $ | Δ$ к B1 | Шарп | мес. SR>0 | доля KPI (опр. мес.) |", "|---|---|---|---|---|---|---|"]
    for r in sorted([r for r in rows if r[0] == mode], key=lambda r: -r[5]):
        L.append(f"| {r[1]} | {r[2]} | {r[3]:+.0f} | {r[4]:+.0f} | {r[5]:+.3f} | {r[6]}/{r[7]} | {r[8]:.3f} ({r[9]}) |")
    L.append("")
open(OUT + ".md", "w", encoding="utf-8", newline="\n").write("\n".join(L))
print("\n".join(L[4:]))
