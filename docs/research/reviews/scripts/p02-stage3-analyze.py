import sys, numpy as np
sys.path.insert(0, r"C:\visual projects\alpha\.claude\skills\alpha-research\scripts")
from effective_n import effective_n_from_autocorr
SP = sys.argv[1]
g36 = {}
for line in open(SP + "/judge-g36.txt"):
    p = line.split()
    if p[0] != "G36": continue
    v = list(map(int, p[2:14]))  # unflag k1 b,t k2 b,t k3 b,t ; flag k1 b,t k2 b,t k3 b,t
    g36[p[1]] = v
g46 = {}
for line in open(SP + "/judge-g46.txt"):
    p = line.split(); g46[p[1]] = list(map(int, p[2:6]))  # b1 t1 b0 t0
def an(rows, label, nboot=20000, seed=11, drop_empty=True):
    X = np.array(rows, float)
    if drop_empty: X = X[(X[:,1] > 0) & (X[:,3] > 0)]
    S = X.sum(0); d = S[0]/S[1] - S[2]/S[3]
    rng = np.random.default_rng(seed); idx = rng.integers(0, len(X), (nboot, len(X))); B = X[idx].sum(1)
    with np.errstate(invalid="ignore", divide="ignore"):
        bd = B[:,0]/B[:,1] - B[:,2]/B[:,3]
    bd = bd[~np.isnan(bd)]
    lo, hi = np.percentile(bd, [2.5, 97.5]); p = min(1, 2*min((bd<=0).mean(), (bd>=0).mean()))
    dd = [r[0]/r[1] - r[2]/r[3] for r in X]
    ne = effective_n_from_autocorr(dd)
    print(f"{label:28s} days={len(X)} n={int(S[1])}/{int(S[3])} sh={S[0]/S[1]*100:.1f}/{S[2]/S[3]*100:.1f} d={d*100:+.2f} [{lo*100:+.2f};{hi*100:+.2f}] p={p:.4f} neff={ne:.1f} pos={sum(x>0 for x in dd)}/{len(dd)}")
for m in ("2026-08", "2026-09"):
    days = sorted(k for k in g36 if k.startswith(m))
    rows = [[v[6]+v[8]+v[10], v[7]+v[9]+v[11], v[0]+v[2]+v[4], v[1]+v[3]+v[5]] for v in (g36[k] for k in days)]
    an(rows, f"G36 main {m}")
    for kk, (fi, ui) in enumerate(((6,0),(8,2),(10,4)), 1):
        an([[g36[k][fi], g36[k][fi+1], g36[k][ui], g36[k][ui+1]] for k in days], f"  G36 k={kk if kk<3 else '3+'} {m}")
    # Mantel-Haenszel-подобная разница внутри k (веса = гармоническое среднее размеров), только для чтения
    tot = np.zeros(12)
    for k in days: tot += np.array(g36[k])
    w = []; dsum = 0; wsum = 0
    for fi, ui in ((6,0),(8,2),(10,4)):
        nf, nu = tot[fi+1], tot[ui+1]
        if nf == 0: continue
        wt = nf*nu/(nf+nu); dsum += wt*(tot[fi]/nf - tot[ui]/nu); wsum += wt
    print(f"  G36 {m} k-standardized diff = {dsum/wsum*100:+.2f} pp; days with >=1 flag = {sum(1 for k in days if g36[k][7]+g36[k][9]+g36[k][11]>0)}/{len(days)}")
    d46 = sorted(k for k in g46 if k.startswith(m))
    an([g46[k] for k in d46], f"G46 60s main {m}")
