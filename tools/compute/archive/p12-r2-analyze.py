#!/usr/bin/env python3
"""TK-063: П-12 R2 (клетки волны TK-065) — Δ$ к базе по суткам, блочный бутстреп, max-T WY по семье, Холм по 39, KPI-часы, С2 (двое суток).
Вход data/p12r2/closes-{v,vt}-cap{0,3}-<мес>.json (portfolio-sim, closes-out); выход docs/findings/p12-r2-kpi-<режим>-2026-10-08.csv + печать.
Режим: 'free' (без потолка, cap0) или 'B2' (потолок 3). База: v — B1 (t-bid-btc4h-q1, стоп pct2 тейк tr1x1), vt — B3 (1to1, семья e117).
Метод — как tk083-kpi-analyze.py (П-12 §6–§9): сделка на сутки UTC закрытия; месяц клетки «определён» при >= 10 сделок и >= 10 суток со сделкой;
блоки ceil(n^(1/3)) суток общие для клеток; T=Δ̄/se; step-down WY внутри семьи; p семьи = минимум; Холм по m=39 (семьи вне волны — p=1)."""
from p12_r2_head import *   # голова (данные, series) вынесена в модуль (TK-135, С-59); до маркера — как было

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
    defc = defined_of(S[c][1], S[c][2], S[c][4])
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
    dfc = defined_of(nt, dtr, S[c][4] if len(S[c]) > 4 else None)
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
                round(r["T"], 3), round(r["p1"], 4), round(r["pwy"], 4), round(r["pharm"], 4), round(kpi_mean(c), 4), w, n, round(m2, 3), r["n"], round(rawusd.get(S[c][4], float("nan")), 1) if NORM and CELLS[c][1] in ("v", "vg") else "", len(r["defc"])])
print("\nсемьи: p семьи (min WY) -> порог Холма α/2/(39-k) -> проходит")
for f, (p, thr, ok) in holm.items():
    print(f"{f}: p={p:.4f} thr={thr:.2e} {'ПРОХОДИТ п.1' if ok else 'нет'}")
print("доля Δ̄>0:", sum(res[c]["mean"] > 0 for c in cells), "из", len(cells), "; нижняя граница ДИ > 0:", sum(res[c]["lo"] > 0 for c in cells),
      "; верхняя < 0:", sum(res[c]["hi"] < 0 for c in cells))
with open(f"docs/findings/p12-r2-kpi-{MODE}-2026-10-08{TAG}.csv", "w", encoding="utf-8", newline="") as fh:
    w = csv.writer(fh, lineterminator="\n")
    w.writerow(["mode", "cell", "family", "total_usd", "base_total_usd", "d_mean_usd_day", "ci_lo", "ci_hi", "T", "p1", "p_wy", "p_harm_wy",
                "kpi_share_mean", "months_kpi_worse", "months_defined", "c2_wo_top2days", "n_days", "raw_total_usd", "months_defined_p8"])
    w.writerows(out)
with open(f"docs/findings/p12-r2-kpi-monthly-{MODE}-2026-10-08{TAG}.csv", "w", encoding="utf-8", newline="") as fh:
    w = csv.writer(fh, lineterminator="\n")
    w.writerow(["mode", "cell", "month", "trades", "usd_month", "kpi_share_gt5d", "defined"])
    w.writerows(rows)
