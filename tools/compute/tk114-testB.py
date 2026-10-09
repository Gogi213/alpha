#!/usr/bin/env python3
"""TK-114 ТЕСТ Б (§6 п.1): ОДНА клетка-победитель vs B1 на половине Б, без Холма, α=0,05 односторонний, ΔSR>0 + C3 по месяцам. Использование: tk114-testB.py free|B2 data/tk114/B
(копия tier2-select; ярус 2: П-13 §5: 10 клеток базы (B1 + g93 (9) + g94 (4), нормированные на равную экспозицию (vn-b)), v171c, янв–сен, free и B2.
Вход data/tk114/{ext,t1}/closes-cap{0,3}-<мес>.json (ext — /data/tk0113/drop2/ext, t1 — /data/tk0114: pct3/pct4).
Ворота и статистика — analyse() из p12-sharpe.py (В-208 в ред. Судьи); Холм m=9 (Q-5), семьи: стоп/дедлайн/вход.
Выход docs/findings/p13-tier1-<free|B2>-2026-10-09.csv + печать. Использование: tk114-tier2-select.py [free|B2]"""
import csv, datetime as dt, json, math, os, sys
from collections import defaultdict
import numpy as np

MODE = sys.argv[1] if len(sys.argv) > 1 else "free"
ROOT = sys.argv[2] if len(sys.argv) > 2 else "data/tk114/A"   # половина А (p13-split-2026-10-09.json)
os.environ["P12_POOL_FROM"] = "1"
from p12lib import analyse   # P12_POOL_FROM выставлен выше — до импорта

UTC = dt.timezone.utc
H5 = 120 * 3600 * 1000
U = {'1': 'u1d3', '2': 'u2d3', '3': 'u1'}
P0 = "ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800"
SETK = "t-bid-btc4h-q1@"
CELLS = {"B1": (SETK + P0, None)}
CELLS["g93-u1-K3"] = (f"{SETK}{P0}-pynw3u3", "g93")   # победитель яруса 2 на половине А
NAME = {v[0]: k for k, v in CELLS.items()}
MONTHS = [f"2026-{m:02d}" for m in range(1, 10)]
cal = [(dt.date(2026, 1, 1) + dt.timedelta(i)).isoformat() for i in range(273)]
di = {d: i for i, d in enumerate(cal)}
day_of = lambda ms: dt.datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d")
cap = "0" if MODE == "free" else "3"


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
    return bad / (t1 - t0), eq


cl = defaultdict(dict)
for d in ("r2",):
    for m in MONTHS:
        for form, per in json.load(open(f"{ROOT}/{d}/closes-cap{cap}-{m}.json")).items():
            if form not in NAME or (d == "t1" and NAME[form] == "B1" and False):
                continue
            for _p, caps in per.items():
                for _c, lst in caps.items():
                    new = [(int(a), float(b)) for a, b in lst]
                    if NAME[form] in cl and m in cl[NAME[form]]:
                        assert cl[NAME[form]][m] == new, ("расхождение", NAME[form], m)   # B1 в обоих деревьях
                    cl[NAME[form]][m] = new
P = {}
for c, (f, fam) in CELLS.items():
    a = np.zeros(len(cal))
    for m, lst in cl[c].items():
        for ms, v in lst:
            a[di[day_of(ms)]] += v
    dfn = {m for m in MONTHS if len(cl[c].get(m, [])) >= 10 and len({day_of(ms) for ms, _ in cl[c][m]}) >= 10}
    P[c] = dict(arr=a, lst=cl[c], dfn=dfn, fam="B1" if fam is None else fam, base=None if fam is None else "B1", cal=cal, months=MONTHS, kpi=kpi_share)
res, fams = analyse(P, 114)
for c, r in res.items():
    if not r: continue
    bad_m = [m for m, v in r["mon"].items() if v[1] < 0]
    g1 = bool(r["dsr"] > 0 and r["pwy"] <= 0.05); g3 = not bad_m
    print(f"B {MODE} {c}: SR{r['sr']:+.4f} (B1 {r['srb']:+.4f}) dSR{r['dsr']:+.4f}[{r['lo']:+.3f};{r['hi']:+.3f}] pWY(1-sided)={r['pwy']:.4f} k={r['k']}/{len(r['dm'])} "
          f"месяцы dSR-hi<0: {bad_m or 'нет'} C2 {r['c2'][0]:+.3f}/{r['c2'][1]:+.3f} -> ПОДТВЕРЖДЕНО={int(g1 and g3)}")
