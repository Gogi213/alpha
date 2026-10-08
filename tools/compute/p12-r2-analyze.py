#!/usr/bin/env python3
"""TK-063: П-12 R2 (клетки волны TK-065) — Δ$ к базе по суткам, блочный бутстреп, max-T WY по семье, Холм по 39, KPI-часы, С2 (двое суток).
Вход data/p12r2/closes-{v,vt}-cap{0,3}-<мес>.json (portfolio-sim, closes-out); выход docs/findings/p12-r2-kpi-<режим>-2026-10-08.csv + печать.
Режим: 'free' (без потолка, cap0) или 'B2' (потолок 3). База: v — B1 (t-bid-btc4h-q1, стоп pct2 тейк tr1x1), vt — B3 (1to1, семья e117).
Метод — как tk083-kpi-analyze.py (П-12 §6–§9): сделка на сутки UTC закрытия; месяц клетки «определён» при >= 10 сделок и >= 10 суток со сделкой;
блоки ceil(n^(1/3)) суток общие для клеток; T=Δ̄/se; step-down WY внутри семьи; p семьи = минимум; Холм по m=39 (семьи вне волны — p=1)."""
import csv
import datetime as dt
import json
import math
import sys
from collections import defaultdict

import numpy as np

MODE = sys.argv[1] if len(sys.argv) > 1 else "free"
CAP = "0" if MODE == "free" else "3"
D = "data/p12r2/"
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


raw = {"v": defaultdict(dict), "vt": defaultdict(dict)}   # вид -> форма -> месяц -> [(мс, $)]
for kind in raw:
    for m in MONTHS:
        j = json.load(open(f"{D}closes-{kind}-cap{CAP}-{m}.json"))
        for form, per in j.items():
            for _p, caps in per.items():
                for _c, lst in caps.items():
                    raw[kind][form][m] = [(int(a), float(b)) for a, b in lst]
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


def defined_of(nt, dtr):
    return [m for m in MONTHS if nt[m] >= 10 and len(dtr[m]) >= 10]


raw["vg"] = raw["v"]
S = {}   # имя -> series; базы 'B1','B3'
for nm, kind in (("B1", "v"), ("B3", "vt"), ("B1g", "vg")):
    S[nm] = series(kind, BASE[kind])
for c, (f, kind, suf) in CELLS.items():
    form = BASE[kind] + suf
    if form in raw[kind]:
        S[c] = series(kind, form)
    else:
        print("НЕТ формы", c, form)
cells = [c for c in CELLS if c in S]
rng = np.random.default_rng(63)
B = 20000


def block_idx(n, size):
    L = max(1, math.ceil(n ** (1 / 3)))
    nb = math.ceil(n / L)
    st = rng.integers(0, n, size=(size, nb))
    return ((st[:, :, None] + np.arange(L)) % n).reshape(size, -1)[:, :n]


idx = block_idx(len(cal), B)
dfn = {}
res = {}
for c in cells:
    f, kind, _ = CELLS[c]
    b = BN[kind]
    defc = defined_of(S[c][1], S[c][2])
    defb = defined_of(S[b][1], S[b][2])
    mk = np.array([d[:7] in defc and d[:7] in defb for d in cal])
    dl = S[c][0] - S[b][0]
    mean = dl[mk].mean()
    bm = np.where(mk[idx], dl[idx], 0).sum(1) / np.maximum(mk[idx].sum(1), 1)
    se = bm.std(ddof=1)
    res[c] = dict(n=int(mk.sum()), mean=mean, se=se, lo=np.percentile(bm, 2.5), hi=np.percentile(bm, 97.5), T=mean / se,
                  Ts=(bm - mean) / se, dl=dl, mk=mk, defc=defc, b=b, tot=sum(v for l in S[c][3].values() for _, v in l),
                  totb=sum(v for l in S[b][3].values() for _, v in l))
fam = defaultdict(list)
for c in cells:
    fam[CELLS[c][0]].append(c)
famp = {}
for f, cs in fam.items():
    order = sorted(cs, key=lambda c: -res[c]["T"])
    prev = 0.0
    for k, c in enumerate(order):
        mx = np.max([res[o]["Ts"] for o in order[k:]], axis=0)
        p = max(prev, float((mx >= res[c]["T"]).mean()))
        prev = p
        res[c]["pwy"] = p
        res[c]["p1"] = float((res[c]["Ts"] >= res[c]["T"]).mean())
    # зеркально: вред — min T (нижний хвост), p той же формы
    order2 = sorted(cs, key=lambda c: res[c]["T"])
    prev = 0.0
    for k, c in enumerate(order2):
        mn = np.min([res[o]["Ts"] for o in order2[k:]], axis=0)
        p = max(prev, float((mn <= res[c]["T"]).mean()))
        prev = p
        res[c]["pharm"] = p
    famp[f] = min(res[c]["pwy"] for c in cs)
# Холм по m=39: семьи вне волны — p=1
ps = sorted(famp.items(), key=lambda kv: kv[1])
holm = {}
alive = True
for k, (f, p) in enumerate(ps):
    thr = 0.05 / 2 / (M_FAM - k)
    alive = alive and p <= thr
    holm[f] = (p, thr, alive)
# KPI и С2
rows = []
for c in ["B1", "B3", "B1g"] + cells:
    nt, dtr, lsts = S[c][1], S[c][2], S[c][3]
    dfc = defined_of(nt, dtr)
    for m in MONTHS:
        s, eq = kpi_share(lsts.get(m, []), m)
        rows.append([MODE, c, m, nt[m], round(eq, 1), round(s, 4), int(m in dfc)])


def kpi_mean(c):
    xs = [r[5] for r in rows if r[1] == c and r[6]]
    return float(np.mean(xs)) if xs else float("nan")


def kpi_worse(c):
    b = res[c]["b"]
    bk = {r[2]: r[5] for r in rows if r[1] == b}
    w = [r[5] > bk[r[2]] for r in rows if r[1] == c and r[6]]
    return sum(w), len(w)


print(f"== режим {MODE}; суток {len(cal)}; базы: B1 итог$ {sum(v for l in S['B1'][3].values() for _, v in l):.0f}, "
      f"B3 итог$ {sum(v for l in S['B3'][3].values() for _, v in l):.0f}; KPI-доля B1 {kpi_mean('B1'):.3f} B3 {kpi_mean('B3'):.3f} B1(g87-fresh) {kpi_mean('B1g'):.3f}; итог$ B1g {sum(v for l in S['B1g'][3].values() for _, v in l):.0f}")
print("клетка | итог$ (база) | Δ̄ $/сут [ДИ95] | T | p1 | pWY | pharm(WY) | KPI-доля | мес хуже базы | С2 без 2 суток")
out = []
for c in cells:
    r = res[c]
    mk = r["mk"]
    top2 = np.argsort(-np.where(mk, r["dl"], -1e18))[:2]
    mk2 = mk.copy()
    mk2[top2] = False
    m2 = r["dl"][mk2].mean()
    w, n = kpi_worse(c)
    print(f"{c} | {r['tot']:+.0f} ({r['totb']:+.0f}) | {r['mean']:+.2f} [{r['lo']:+.2f};{r['hi']:+.2f}] | {r['T']:.2f} | {r['p1']:.3f} | "
          f"{r['pwy']:.3f} | {r['pharm']:.3f} | {kpi_mean(c):.3f} | {w}/{n} | {m2:+.2f}")
    out.append([MODE, c, CELLS[c][0], round(r["tot"], 1), round(r["totb"], 1), round(r["mean"], 3), round(r["lo"], 3), round(r["hi"], 3),
                round(r["T"], 3), round(r["p1"], 4), round(r["pwy"], 4), round(r["pharm"], 4), round(kpi_mean(c), 4), w, n, round(m2, 3), r["n"]])
print("\nсемьи: p семьи (min WY) -> порог Холма α/2/(39-k) -> проходит")
for f, (p, thr, ok) in holm.items():
    print(f"{f}: p={p:.4f} thr={thr:.2e} {'ПРОХОДИТ п.1' if ok else 'нет'}")
print("доля Δ̄>0:", sum(res[c]["mean"] > 0 for c in cells), "из", len(cells), "; нижняя граница ДИ > 0:", sum(res[c]["lo"] > 0 for c in cells),
      "; верхняя < 0:", sum(res[c]["hi"] < 0 for c in cells))
with open(f"docs/findings/p12-r2-kpi-{MODE}-2026-10-08.csv", "w", encoding="utf-8", newline="") as fh:
    w = csv.writer(fh, lineterminator="\n")
    w.writerow(["mode", "cell", "family", "total_usd", "base_total_usd", "d_mean_usd_day", "ci_lo", "ci_hi", "T", "p1", "p_wy", "p_harm_wy",
                "kpi_share_mean", "months_kpi_worse", "months_defined", "c2_wo_top2days", "n_days"])
    w.writerows(out)
with open(f"docs/findings/p12-r2-kpi-monthly-{MODE}-2026-10-08.csv", "w", encoding="utf-8", newline="") as fh:
    w = csv.writer(fh, lineterminator="\n")
    w.writerow(["mode", "cell", "month", "trades", "usd_month", "kpi_share_gt5d", "defined"])
    w.writerows(rows)
