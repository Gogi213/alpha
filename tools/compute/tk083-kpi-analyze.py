#!/usr/bin/env python3
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
rng = np.random.default_rng(83)
B = 20000
mask_m = {c: np.array([d[:7] in defined[c] for d in cal]) for c in cl}
base = {c: mask_m[c] & mask_m["B1"] for c in CELLS}   # парные сутки: определены у клетки и у B1


def block_idx(n, size):
    L = max(1, math.ceil(n ** (1 / 3)))
    nb = math.ceil(n / L)
    st = rng.integers(0, n, size=(size, nb))
    return ((st[:, :, None] + np.arange(L)) % n).reshape(size, -1)[:, :n]


idx = block_idx(len(cal), B)
res = {}
for c in CELLS:
    dl = daily[c] - daily["B1"]
    mk = base[c]
    mean = dl[mk].mean()
    bm = np.where(mk[idx], dl[idx], 0).sum(1) / np.maximum(mk[idx].sum(1), 1)
    se = bm.std(ddof=1)
    res[c] = dict(n=int(mk.sum()), mean=mean, se=se, lo=np.percentile(bm, 2.5), hi=np.percentile(bm, 97.5), T=mean / se,
                  Ts=(bm - mean) / se, dl=dl, mk=mk)
fam = defaultdict(list)
for c, (f, _) in CELLS.items():
    fam[f].append(c)
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
    famp[f] = min(res[c]["pwy"] for c in cs)
for c in CELLS:   # по месяцам: Δ$ и ДИ (блоки внутри месяца)
    r = res[c]
    r["months"] = []
    for m in MONTHS:
        mk = np.array([d.startswith(m) for d in cal]) & base[c]
        if mk.sum() < 10 or m not in defined[c]:
            r["months"].append((m, None))
            continue
        x = r["dl"][mk]
        s = x[block_idx(len(x), B)].sum(1)
        r["months"].append((m, (x.sum(), np.percentile(s, 2.5), np.percentile(s, 97.5))))
# С2: без двух суток с наибольшим вкладом и без монеты с наибольшим вкладом (по rounds, без потолка)
rr = defaultdict(lambda: defaultdict(float))
for row in csv.DictReader(open(D + "rounds-all.csv", encoding="utf-8")):
    c = NAME.get(row["form"])
    if c:
        rr[c][(day_of(int(row["exit_ns"]) // 10**6), row["symbol"])] += float(row["qty"]) * float(row["entry_vwap"]) * float(row["net_bps"]) / 1e4
for c in CELLS:
    r = res[c]
    mk = r["mk"]
    dl = np.where(mk, r["dl"], -1e18)
    top2 = np.argsort(-dl)[:2]
    mk2 = mk.copy()
    mk2[top2] = False
    m2 = r["dl"][mk2].mean()
    bm = np.where(mk2[idx], r["dl"][idx], 0).sum(1) / np.maximum(mk2[idx].sum(1), 1)
    sym = defaultdict(float)
    for src, sg in ((c, 1), ("B1", -1)):
        for (d, s), v in rr[src].items():
            if d in di and mk[di[d]]:
                sym[s] += sg * v
    top = max(sym, key=sym.get)
    r["c2"] = (cal[top2[0]], cal[top2[1]], m2, np.percentile(bm, 2.5), top, sym[top], (r["dl"][mk].sum() - sym[top]) / mk.sum())
print(f"== режим {SUF or 'без потолка'}; суток {len(cal)}; B1 определён: {defined['B1']}")
for c in ["B1"] + list(CELLS):
    print(c, "опр.мес.", len(defined[c]), "сделок", sum(ntr[c].values()), "итог$", round(sum(v for m in cl[c].values() for _, v in m), 0))
print("\n-- KPI доля часов >5 сут / $ за месяц: ")
for m in MONTHS:
    print(m, " ".join(f"{r[1]}:{r[5]:.2f}/{r[4]:+.0f}/{r[3]}" for r in rows if r[2] == m))
print("\n-- Δ$ к B1 по суткам (определённые месяцы), ДИ 95 %, T, p без поправки, WY, p семьи")
for c in CELLS:
    r = res[c]
    print(f"{c}: n={r['n']} Δ̄={r['mean']:+.2f}$/сут [{r['lo']:+.2f};{r['hi']:+.2f}] T={r['T']:.2f} p1={r['p1']:.3f} pWY={r['pwy']:.3f} семья {CELLS[c][0]} p={famp[CELLS[c][0]]:.3f}")
    print("   мес Δ$:", " ".join(f"{m[5:]}:" + ("—" if v is None else f"{v[0]:+.0f}[{v[1]:+.0f};{v[2]:+.0f}]") for m, v in r["months"]))
    a, b, m2, lo2, top, tv, mt = r["c2"]
    print(f"   С2: без {a},{b}: Δ̄={m2:+.2f} нижн.{lo2:+.2f}; без монеты {top}({tv:+.0f}$): Δ̄={mt:+.2f}")
with open(f"docs/findings/p12-ext-kpi{'-' + SUF if SUF else ''}-2026-10-08.csv", "w", encoding="utf-8", newline="") as fh:
    w = csv.writer(fh, lineterminator="\n")
    w.writerow(["mode", "cell", "month", "trades", "usd_month", "kpi_share_gt5d", "longest_wait_h", "defined"])
    w.writerows(rows)
