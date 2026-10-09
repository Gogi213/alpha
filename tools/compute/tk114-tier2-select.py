#!/usr/bin/env python3
"""TK-114 ярус 2 П-13 §5: 10 клеток базы (B1 + g93 (9) + g94 (4), нормированные на равную экспозицию (vn-b)), v171c, янв–сен, free и B2.
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
for kk in "123":
    for k in "123":
        CELLS[f"g93-{U[k]}-K{kk}"] = (f"{SETK}{P0}-pynw{kk}u{k}", "g93")
for n in (2, 3, 4, 5):
    CELLS[f"g94-n{n}"] = (f"{SETK}{P0}-pyeat{n}", "g94")
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
M = 9
holm = {}
for key, nm in (("pwy", "польза"), ("pharm", "вред")):
    store = {f: min(res[c][key] for c in cs if res[c]) for f, cs in fams.items()}
    alive = True
    for k, (f, p) in enumerate(sorted(store.items(), key=lambda kv: kv[1])):
        thr = 0.025 / (M - k); alive = alive and p <= thr; holm[(nm, f)] = (p, thr, alive)
rows = []
print(f"== П-13 ярус 2, режим {MODE}; клеток {len(res)-1}, семей {len(fams)}, m={M}")
for (nm, f), (p, thr, ok) in sorted(holm.items()):
    print(f"  {nm} {f}: p={p:.4f} thr={thr:.4f} {'ПРОХОДИТ' if ok else 'нет'}")
for c, r in sorted((kv for kv in res.items() if kv[1]), key=lambda kv: -kv[1]["T"]):
    g1 = bool(holm[("польза", r["fam"])][2] and r["pwy"] <= holm[("польза", r["fam"])][1])
    bad_m = [m for m, v in r["mon"].items() if v[1] < 0]
    g2 = not bad_m; g3 = bool(r["c2"][0] >= 0 and r["c2"][1] >= 0); g4 = len(r["dm"]) >= 6
    ok = g1 and g2 and g3 and g4
    refut = bool(holm[("вред", r["fam"])][2] and r["pharm"] <= holm[("вред", r["fam"])][1])
    n = sum(len(v) for v in cl[c].values())
    rows.append([MODE, c, r["fam"], n, round(sum(sum(v for _a, v in cl[c].get(m, [])) for m in MONTHS), 1), round(r["sr"], 4), round(r["srb"], 4), round(r["dsr"], 4),
                 round(r["lo"], 4), round(r["hi"], 4), round(r["T"], 3), round(r["pwy"], 4), round(r["pharm"], 4), r["k"], len(r["dm"]), round(r["K"], 4),
                 int(g1), int(g2), int(g3), int(g4), int(ok), int(refut), ";".join(bad_m), round(r["c2"][0], 4), round(r["c2"][1], 4)])
    print(f"{c}: n={n} SR{r['sr']:+.3f} (B1 {r['srb']:+.3f}) ΔSR{r['dsr']:+.3f}[{r['lo']:+.3f};{r['hi']:+.3f}] T{r['T']:.2f} pWY{r['pwy']:.3f} k={r['k']}/{len(r['dm'])} K={r['K']:.3f} ворота {int(g1)}{int(g2)}{int(g3)}{int(g4)} {'ПРОШЛА' if ok else ''}")
with open(f"docs/findings/p13-tier2A-{MODE}-2026-10-09.csv", "w", encoding="utf-8", newline="") as fh:
    w = csv.writer(fh, lineterminator="\n")
    w.writerow(["mode", "cell", "family", "trades", "usd_jan_sep", "sr_day", "sr_base", "d_sr", "ci_lo", "ci_hi", "T", "p_wy", "p_harm_wy", "k_months_sr_pos", "months_defined", "kpi_share",
                "gate1", "gate2", "gate3", "gate4", "passed", "refuted", "months_dsr_hi_neg", "c2_dsr", "c2_lo"])
    w.writerows(rows)
