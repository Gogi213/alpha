#!/usr/bin/env python3
"""TK-083: П-12 расширение (7 клеток) — KPI-часы, Δ$ на $500 по суткам к B1, блочный бутстреп, max-T WY по семье, С2.
Вход data/tk083/kpi/{closes[B2]-<мес>.json, rounds-all.csv}; выход docs/findings/p12-ext-kpi[-B2]-2026-10-08.csv + печать.
Сделка относится к суткам UTC закрытия. Месяц клетки «определён»: сделок >= 10 и суток со сделкой >= 10 (П-10 §10.1)."""
from tk083_head import *   # голова (данные, series) вынесена в модуль (TK-135, С-59); до маркера — как было

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
