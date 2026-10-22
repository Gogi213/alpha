import csv, random, sys, collections
P = sys.argv[1]; TH = 94.2
rows = []
for r in csv.DictReader(open(P, newline="")):
    k = float(r["qty"]) * float(r["entry_px"]) / 1e4
    rows.append((r["mon"], r["day"], float(r["sigma240"]), float(r["net_bps"]) * k))
mons = sorted({m for m, *_ in rows})
out = ["| мес | n | n(σ<94,2) | Σ$ без | Σ$ Q1 (отсечено) | Σ$ с фильтром |", "|---|---|---|---|---|---|"]
c1 = c2 = 0
for m in mons:
    R = [x for x in rows if x[0] == m]
    q1 = [x for x in R if x[2] < TH]
    a = sum(x[3] for x in R); q = sum(x[3] for x in q1); f = a - q
    c1 += q < 0; c2 += f > a
    out.append(f"| {m} | {len(R)} | {len(q1)} | {a:.0f} | {q:.0f} | {f:.0f} |")
A = sum(x[3] for x in rows); Q = sum(x[3] for x in rows if x[2] < TH)
nq = sum(x[2] < TH for x in rows)
out.append(f"| всего | {len(rows)} | {nq} | {A:.0f} | {Q:.0f} | {A-Q:.0f} |")
days = collections.defaultdict(float)
for m, d, s, v in rows:
    if s < TH: days[d] -= v  # diff (with - without) = -Q1 pnl
alld = sorted({d for _, d, _, _ in rows})
vals = [days.get(d, 0.0) for d in alld]
random.seed(23); B = []
for _ in range(10000):
    B.append(sum(random.choice(vals) for _ in vals))
B.sort()
lo, hi = B[500], B[9500]
print("\n".join(out))
print(f"\nc1 (Σ$Q1<0 мес.) = {c1}/{len(mons)}; c2 (с фильтром > без) = {c2}/{len(mons)}; агрегат Q1 = {Q:.0f}")
print(f"bootstrap по суткам ({len(alld)}), 90%: [{lo:.0f}; {hi:.0f}], разность = {-Q:.0f}")
print("вердикт: ", "ДЕРЖИТСЯ" if (c1>=6 and Q<0 and c2>=6 and lo>0) else "не подтверждено")
