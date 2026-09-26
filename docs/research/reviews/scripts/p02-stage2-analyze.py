import json, sys, numpy as np
sys.path.insert(0, r"C:\visual projects\alpha\.claude\skills\alpha-research\scripts")
from effective_n import effective_n_from_autocorr
d = json.load(open(sys.argv[1]))["days"]
def run(month, var, pop, a, b, nboot=20000, seed=7):
    days = sorted(k for k, v in d.items() if v["month"] == month)
    rows = []
    for k in days:
        c = d[k]["cnt"]; A = c.get(f"{var}|{pop}|{a}", [0, 0]); B = c.get(f"{var}|{pop}|{b}", [0, 0])
        rows.append(A + B)
    X = np.array(rows, float)
    tA, tB = X.sum(0)[[0, 1]], X.sum(0)[[2, 3]]
    diff = tA[0]/tA[1] - tB[0]/tB[1]
    rng = np.random.default_rng(seed); idx = rng.integers(0, len(X), (nboot, len(X))); S = X[idx].sum(1)
    bd = S[:, 0]/S[:, 1] - S[:, 2]/S[:, 3]
    lo, hi = np.percentile(bd, [2.5, 97.5]); p = min(1, 2*min((bd <= 0).mean(), (bd >= 0).mean()))
    dd = [r[0]/r[1] - r[2]/r[3] for r in rows if r[1] > 0 and r[3] > 0]
    ne = effective_n_from_autocorr(dd)
    # доля суток со знаком «в сторону гипотезы»
    pos = sum(1 for x in dd if x > 0)
    return f"{month} {var:5s} {pop:5s} days={len(days)} nA/nB={int(tA[1])}/{int(tB[1])} sh={tA[0]/tA[1]*100:.2f}/{tB[0]/tB[1]*100:.2f} diff={diff*100:+.2f} [{lo*100:+.2f};{hi*100:+.2f}] p={p:.4f} neff={ne:.1f} days+={pos}/{len(dd)}"
for month in ("2026-08", "2026-09"):
    for pop in ("notrx", "all"):
        print(run(month, "g07", pop, "top", "bottom"))
        print(run(month, "g88", pop, "clean", "spring"))
        print(run(month, "g07ps", pop, "top", "bottom"))
tot = {m: sum(v["cnt"]["n|notrx|all"][1] for v in d.values() if v["month"] == m) for m in ("2026-08", "2026-09")}
print("touches notrx:", tot)
miss = sorted({s for v in d.values() for s in v["nopersym"]}); print("sept syms w/o Aug persym:", len(miss), miss[:20])
nm = {m: sum(sum(v["cnt"].get(f"n|notrx|all",[0,0])[1] for _ in [0]) for v in d.values() if v["month"]==m) for m in ("2026-09",)}
