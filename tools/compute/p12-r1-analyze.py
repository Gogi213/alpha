#!/usr/bin/env python3
"""TK-063: П-12 R1 (155 клеток TK-064, пороги до исходов) — Δ$ к B1 по суткам, блочный бутстреп, max-T WY внутри семьи, Холм по 39, KPI-часы, С2.
Вход data/tk063r1/closes-cap{0,3}-<мес>.json (p12-r1-psim.py, portfolio-sim --closes-out); выход docs/findings/p12-r1-kpi-<режим>-2026-10-08.csv + печать.
Режим 'free' (cap0) или 'B2' (cap3). Метод — как p12-r2-analyze.py (П-12 §6–§9). Семья = префикс имени клетки до первого '-'. Месяцы фев…окт
(январь — калибровка порогов). Фильтр R1 только убирает сделки: экспозиция не растёт, нормировка §6(2) не нужна.
Холм по m=39: p семей R2 — из p12-r2-kpi-free; не посчитанные семьи (e112/e116/e106/e133/e65/g95) — p=1 (консервативно)."""
from p12_r1_head import *   # голова (данные, series) вынесена в модуль (TK-135, С-59); до маркера — как было

S = {c: series(c) for c in raw}
cells = sorted(c for c in S if c != "B1")
fam_of = lambda c: c.split("-")[0]
rng = np.random.default_rng(63)
n = len(cal); L = max(1, math.ceil(n ** (1 / 3))); nb = math.ceil(n / L)
idx = ((rng.integers(0, n, size=(20000, nb))[:, :, None] + np.arange(L)) % n).reshape(20000, -1)[:, :n]
defb = defined_of(S["B1"][1], S["B1"][2])
res = {}
for c in cells:
    defc = defined_of(S[c][1], S[c][2])
    mk = np.array([d[:7] in defc and d[:7] in defb for d in cal])
    dl = S[c][0] - S["B1"][0]
    if mk.sum() == 0:
        res[c] = None; continue
    mean = dl[mk].mean()
    bm = np.where(mk[idx], dl[idx], 0).sum(1) / np.maximum(mk[idx].sum(1), 1)
    se = bm.std(ddof=1) or 1e-9
    res[c] = dict(n=int(mk.sum()), mean=mean, se=se, lo=np.percentile(bm, 2.5), hi=np.percentile(bm, 97.5), T=mean / se, Ts=(bm - mean) / se,
                  dl=dl, mk=mk, defc=defc, tot=sum(v for l in raw[c].values() for _, v in l))
tot_b1 = sum(v for l in raw["B1"].values() for _, v in l)
ok_cells = [c for c in cells if res[c]]
fam = defaultdict(list)
for c in ok_cells:
    fam[fam_of(c)].append(c)
famp, famh = {}, {}
for f, cs in fam.items():
    for key, sgn, store in (("pwy", -1, famp), ("pharm", 1, famh)):
        order = sorted(cs, key=lambda c: sgn * res[c]["T"]); prev = 0.0
        for k, c in enumerate(order):
            ts = np.array([res[o]["Ts"] for o in order[k:]])
            p = float(((ts.max(0) >= res[c]["T"]) if sgn < 0 else (ts.min(0) <= res[c]["T"])).mean())
            prev = max(prev, p); res[c][key] = prev
        store[f] = min(res[c][key] for c in cs)
    for c in cs:
        res[c]["p1"] = float((res[c]["Ts"] >= res[c]["T"]).mean())
# p семей R2
r2 = "docs/findings/p12-r2-kpi-free-2026-10-08.csv"
if MODE == "free" and os.path.exists(r2):
    for r in csv.DictReader(open(r2, encoding="utf-8")):
        famp[r["family"]] = min(famp.get(r["family"], 1.0), float(r["p_wy"])); famh[r["family"]] = min(famh.get(r["family"], 1.0), float(r["p_harm_wy"]))
holm = {}
for store, nm in ((famp, "польза"), (famh, "вред")):
    alive = True
    for k, (f, p) in enumerate(sorted(store.items(), key=lambda kv: kv[1])):
        thr = 0.025 / (M_FAM - k); alive = alive and p <= thr
        holm[(nm, f)] = (p, thr, alive)
rows = []
for c in ["B1"] + cells:
    for m in MONTHS:
        s, eq = kpi_share(raw[c].get(m, []), m)
        rows.append([MODE, c, m, S[c][1][m], round(eq, 1), round(s, 4), int(m in defined_of(S[c][1], S[c][2]))])
kpi_mean = lambda c: float(np.mean([r[5] for r in rows if r[1] == c and r[6]]) or 0) if any(r[1] == c and r[6] for r in rows) else float("nan")
bk = {r[2]: r[5] for r in rows if r[1] == "B1"}
print(f"== R1 режим {MODE}; суток {n}; B1 итог$ {tot_b1:.0f}; KPI-доля B1 {kpi_mean('B1'):.3f}; клеток {len(cells)}, посчитано {len(ok_cells)}, без определённых месяцев {len(cells)-len(ok_cells)}")
out = []
for c in ok_cells:
    r = res[c]; mk = r["mk"]
    top2 = np.argsort(-np.where(mk, r["dl"], -1e18))[:2]; mk2 = mk.copy(); mk2[top2] = False
    w = sum(rr[5] > bk[rr[2]] for rr in rows if rr[1] == c and rr[6]); nn = sum(1 for rr in rows if rr[1] == c and rr[6])
    out.append([MODE, c, fam_of(c), round(r["tot"], 1), round(tot_b1, 1), round(r["mean"], 3), round(r["lo"], 3), round(r["hi"], 3), round(r["T"], 3),
                round(r["p1"], 4), round(r["pwy"], 4), round(r["pharm"], 4), round(kpi_mean(c), 4), w, nn, round(r["dl"][mk2].mean(), 3), r["n"], len(r["defc"])])
for rr in sorted(out, key=lambda x: -x[8])[:12]:
    print("топ T:", rr[1], f"итог${rr[3]:+.0f} Δ̄{rr[5]:+.2f}[{rr[6]:+.2f};{rr[7]:+.2f}] T{rr[8]:.2f} pWY{rr[10]:.3f} KPI{rr[12]:.3f} хуже{rr[13]}/{rr[14]} С2{rr[15]:+.2f}")
for rr in sorted(out, key=lambda x: x[8])[:6]:
    print("низ T:", rr[1], f"итог${rr[3]:+.0f} Δ̄{rr[5]:+.2f}[{rr[6]:+.2f};{rr[7]:+.2f}] T{rr[8]:.2f} pharm{rr[11]:.3f}")
print("Холм (α/2/(39-k)):")
for (nm, f), (p, thr, ok) in sorted(holm.items(), key=lambda kv: (kv[0][0], kv[1][0])):
    if p < 0.2 or f in fam:
        print(f"  {nm} {f}: p={p:.4f} thr={thr:.2e} {'ПРОХОДИТ' if ok else 'нет'}")
print("Δ̄>0:", sum(r[5] > 0 for r in out), "из", len(out), "; ДИ-низ>0:", sum(r[6] > 0 for r in out), "; ДИ-верх<0:", sum(r[7] < 0 for r in out))
with open(f"docs/findings/p12-r1-kpi-{MODE}-2026-10-08.csv", "w", encoding="utf-8", newline="") as fh:
    w = csv.writer(fh, lineterminator="\n")
    w.writerow(["mode", "cell", "family", "total_usd", "b1_total_usd", "d_mean_usd_day", "ci_lo", "ci_hi", "T", "p1", "p_wy", "p_harm_wy", "kpi_share_mean", "months_kpi_worse", "months_defined", "c2_wo_top2days", "n_days", "n_months_defined"])
    w.writerows(out)
with open(f"docs/findings/p12-r1-kpi-monthly-{MODE}-2026-10-08.csv", "w", encoding="utf-8", newline="") as fh:
    w = csv.writer(fh, lineterminator="\n")
    w.writerow(["mode", "cell", "month", "trades", "usd_month", "kpi_share_gt5d", "defined"])
    w.writerows(rows)
