#!/usr/bin/env python3
"""TK-063: П-12 §9 (В-208) ярусы/порядок среди прошедших ворота + печать DSR и PBO для g93 (cap0). Ярус: парный блочный бутстреп SR_лидер−SR_i (B=20000, seed 64, блоки как в analyse R2), Холм по сравнениям шага, α=0,05 одностор.
DSR (Bailey–López de Prado) на дневном SR яруса 1, N испытаний = число клеток пакета (194) и число семей (42) — справочно; PBO — CSCV по 8 мес. × 9 клеток g93 (SR_мес), все C(8,4)=70 разбиений."""
import itertools, math, os, sys, csv
from statistics import NormalDist
import numpy as np
MODE = os.environ.get("P12_MODE", "free")   # free | B2
CSV = os.environ.get("P12_SHARPE_CSV", "docs/findings/p12-sharpe-free-2026-10-08.csv")
NTR = tuple(int(x) for x in os.environ.get("P12_NTR", "194,42").split(","))
src = open("tools/compute/p12-sharpe.py", encoding="utf-8").read().rsplit("\nmain()", 1)[0]
sys.argv = ["x", MODE]
exec(compile(src, "p12-sharpe.py", "exec"))   # окружение P12_DIR/P12_POOL_FROM — как у p12-sharpe.py
P = pack(load("tools/compute/p12-r2-analyze.py", MODE), False)
cal0 = next(iter(P.values()))["cal"]; keep = np.array([d[:7] in POOL for d in cal0]); cal = [d for d, k in zip(cal0, keep) if k]; n = len(cal)
mon = np.array([d[:7] for d in cal]); months = sorted(POOL)
for p in P.values():
    p["arr"] = p["arr"][keep]; p["dfn"] = p["dfn"] & POOL
idx = block_idx(np.random.default_rng(64), n)
N = NormalDist()
rows = {r["cell"]: r for r in csv.DictReader(open(CSV, encoding="utf-8"))}
passed = [c for c, r in rows.items() if r["passed"] == "1"]
mk = {c: np.isin(mon, sorted(P[c]["dfn"] & P["B1"]["dfn"])).astype(float) for c in passed}
srd = {c: float(sr(P[c]["arr"], mk[c])) for c in passed}
rem = sorted(passed, key=lambda c: -srd[c]); tier = {}; t = 1
while rem:
    lead = rem[0]; tier[lead] = t; cmp = []
    for c in rem[1:]:
        m = mk[lead] * mk[c]
        d = sr(P[lead]["arr"][idx], m[idx]) - sr(P[c]["arr"][idx], m[idx]); d0 = float(sr(P[lead]["arr"], m) - sr(P[c]["arr"], m))
        se = d.std(ddof=1); p = float((((d - d0) / se) >= d0 / se).mean()); cmp.append((p, c, d0))
    cmp.sort(); keepc = []; alive = True
    for k, (p, c, d0) in enumerate(cmp):
        alive = alive and p <= 0.05 / (len(cmp) - k)
        print(f"  ярус {t}: лидер {lead} − {c}: ΔSR {d0:+.4f}, p {p:.3f}, Холм-порог {0.05/(len(cmp)-k):.3f} → {'значимо ниже' if alive else 'не значимо ниже'}")
        (tier.__setitem__(c, t + 1) if alive else tier.__setitem__(c, t))
    rem = [c for c in rem[1:] if tier[c] != t]; t += 1
order = sorted(passed, key=lambda c: (tier[c], -int(rows[c]["k_months_sr_pos"]), float(rows[c]["kpi_share"]), -srd[c]))
print("порядок (ярус, k_c, K_c, SR):")
for c in order:
    print(f"  {c}: ярус {tier[c]}, k={rows[c]['k_months_sr_pos']}/{rows[c]['months_defined']}, K={rows[c]['kpi_share']}, SR_день {srd[c]:+.4f}, SR_год {srd[c]*math.sqrt(365):+.2f}")
print("Лучшая из пакета:", order[0])
# DSR (дневной SR яруса 1)
def dsr(c, Ntr):
    x = P[c]["arr"][mk[c] > 0]; T = len(x); s = srd[c]; g3 = float(((x - x.mean()) ** 3).mean() / x.std() ** 3); g4 = float(((x - x.mean()) ** 4).mean() / x.std() ** 4)
    allsr = [float(sr(P[o]["arr"], np.isin(mon, sorted(P[o]["dfn"] & P[P[o]["base"]]["dfn"])).astype(float))) for o in P if P[o]["base"]]
    v = float(np.var(allsr, ddof=1)); em = 0.5772156649
    s0 = math.sqrt(v) * ((1 - em) * N.inv_cdf(1 - 1 / Ntr) + em * N.inv_cdf(1 - 1 / (Ntr * math.e)))
    return N.cdf((s - s0) * math.sqrt(T - 1) / math.sqrt(1 - g3 * s + (g4 - 1) / 4 * s * s)), s0, T
for c in [c for c in order if tier[c] == 1]:
    for Ntr in NTR:
        d, s0, T = dsr(c, Ntr); print(f"DSR {c} N={Ntr}: {d:.3f} (SR0 {s0:+.4f}, T={T} суток)")
# PBO по 9 клеткам g93, SR_мес
g93 = [c for c in P if P[c]["fam"] == "g93"]
M = np.array([[float(sr(P[c]["arr"][mon == m], np.ones((mon == m).sum()))) if (mon == m).sum() else 0.0 for m in months] for c in g93])
cnt = tot = 0
for tr in itertools.combinations(range(len(months)), 4):
    te = [i for i in range(len(months)) if i not in tr]
    best = int(np.argmax(M[:, tr].mean(1))); rk = (M[:, te].mean(1) < M[best, te].mean()).sum() + 1
    w = rk / (len(g93) + 1); cnt += math.log(w / (1 - w)) <= 0; tot += 1
print(f"PBO (CSCV, {len(g93)} клеток g93 × {len(months)} мес., {tot} разбиений): {cnt/tot:.3f}")
