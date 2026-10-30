"""TK-459 слой S, S3: тройка A8 (свип без пробоя + имбаланс в окне K_I=16, щель >= 0,125 ATR + слом в окне K_B=4) x исход имбаланса: fulfill / инвалидация (G4, N=10).
Решения Судьи docs/research/reviews/TK-459-S3-S5-S6-2026-10-30.md; заявка docs/research/P-TK459-synthesis-S-2026-10-30.md §2, §9. Фактор = слом (fl=1 -> A8, fl=0 -> A6 \ A8).
Δ1 — контроль C2 G4.3 (щели случайного размера: тот же слот суток, ±30 сут, ATR/цена x1,25, псевдограница на расстоянии d, до 5 баров, seed 458); Δ2 — A6 \ A8 внутри дня.
Момент знания t0 = max(закрытие бара, где закрылась щель; бар слома); исход — бары t0+1..t0+10 (fulfill: high >= дальняя граница щели; инвалидация: close за ней). Если на t0 close уже за границей — событие вне (воронка).
Первая щель окна, дошедшая до порога g (не максимальная — максимальная известна только к концу окна). Варианты якоря: k=2 (Санчо), k=1 (bp).
python -I tools/sm_s3.py <каталог l2 (список монет)> <каталог 1m csv.gz> <выход-префикс> [nx]   (рядом sm_s1.py; sancho.py — /data/tk0451 или tools/compute)"""
import sys, os, glob, importlib.util
from multiprocessing import Pool
import numpy as np, pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
for p in (HERE, "/data/tk0451", os.path.join(HERE, "compute")):
    if os.path.isdir(p): sys.path.insert(0, p)
from sancho import bars, atr14, swings  # noqa: E402
_s = importlib.util.spec_from_file_location("sm_s1", os.path.join(HERE, "sm_s1.py")); S1 = importlib.util.module_from_spec(_s); _s.loader.exec_module(S1)

K = 84; KI = 16; KB = 4; GMIN = 0.125; N = 10; NC = 5; WIN = 30; RATIO = 1.25
TFS = {60: "1H", 240: "4H", 1440: "1D"}; VARS = {2: "Санчо k=2", 1: "bp k=1"}


def one(args):
    path, sym = args
    rows = []
    for tf, tfn in TFS.items():
        a = bars(path, tf); ok = a.ok.values; tms = a.t.values; slots = 1440 // tf
        for k in VARS:
            for dname, sgn in (("H", 1), ("L", -1)):
                if sgn == 1: h, l, c = (a[x].values.astype(float) for x in "hlc")
                else: h, l, c = -a.l.values.astype(float), -a.h.values.astype(float), -a.c.values.astype(float)
                n = len(h); atr = atr14(h, l, c); atrr = atr / np.where(c != 0, np.abs(c), np.nan)
                sh = swings(h, ok, k); sl = swings(-l, ok, k); slc = sl + k
                gapb = np.zeros(n, bool); gapb[2:] = ok[2:] & ok[1:-1] & ok[:-2] & (l[:-2] > h[2:])
                okc = np.r_[0, np.cumsum(ok)]
                allok = lambda i0, i1: okc[i1 + 1] - okc[i0] == i1 - i0 + 1   # ok[i0..i1] все
                rng = np.random.default_rng(458)
                for i in sh:
                    L = h[i]; s = i + k + 1
                    hit = np.flatnonzero(h[s:] > L)
                    if not len(hit): continue
                    j = s + hit[0]
                    if not (ok[j] and ok[j - 1]) or np.isnan(atr[j - 1]) or c[j] > L: continue
                    A = atr[j - 1]
                    if j + KI >= n or not allok(j, j + KI - 1): continue
                    m = np.searchsorted(slc, j, side="right")
                    if m == 0: continue
                    last = l[sl[m - 1]]
                    g = (l[j:j + KI - 2] - h[j + 2:j + KI]) / A
                    q = np.flatnonzero(g >= GMIN)
                    if not len(q): continue
                    q = j + int(q[0]); bb = np.flatnonzero(c[j + 1:j + 1 + KB] < last)
                    fl = int(len(bb) > 0); t0 = max(q + 2, j + 1 + int(bb[0])) if fl else q + 2
                    if t0 + N >= n or not allok(q, t0 + N): continue
                    far = l[q]; d = far - c[t0]
                    if d <= 0: continue
                    fful = float(h[t0 + 1:t0 + 1 + N].max() >= far); finv = float((c[t0 + 1:t0 + 1 + N] > far).any())
                    cand = np.arange(max(t0 - WIN * slots, 14), min(t0 + WIN * slots, n - N - 2) + 1); cand = cand[(cand - t0) % slots == 0]
                    cand = cand[(cand != t0)]
                    cand = cand[ok[cand] & ~gapb[cand] & ~np.isnan(atrr[cand])]
                    a0 = atrr[t0]
                    if np.isnan(a0) or not len(cand): continue
                    cand = cand[(atrr[cand] <= a0 * RATIO) & (atrr[cand] >= a0 / RATIO)]
                    if len(cand): cand = cand[np.array([allok(x + 1, x + N) for x in cand], bool)]
                    if not len(cand): cf = ci = np.nan
                    else:
                        cs = rng.choice(cand, min(NC, len(cand)), replace=False)
                        cf = float(np.mean([h[x + 1:x + 1 + N].max() >= c[x] + d for x in cs])); ci = float(np.mean([(c[x + 1:x + 1 + N] > c[x] + d).any() for x in cs]))
                    rows.append((int(tms[t0]), sym, tfn, dname, k, fl, fful, finv, cf, ci))
    return rows


if __name__ == "__main__":
    l2, src, outp = sys.argv[1:4]; nx = int(sys.argv[4]) if len(sys.argv) > 4 else 15
    jobs = []
    for f in sorted(glob.glob(f"{l2}/l2-*-60-2.csv.gz")):
        sym = os.path.basename(f)[3:-7].split("-")[0]
        if os.path.exists(f"{src}/{sym}.csv.gz"): jobs.append((f"{src}/{sym}.csv.gz", sym))
    with Pool(nx) as p: D = pd.DataFrame([x for rr in p.map(one, jobs) for x in rr], columns=["t", "coin", "tf", "dir", "k", "fl", "ful_f", "inv_f", "ful_c", "inv_c"])
    D["day"] = D.t // 86400000; D["year"] = pd.to_datetime(D.t, unit="ms").dt.year
    first = D.groupby("coin").t.min(); early = set(first[first < 1577836800000 + 7 * 86400000].index)
    rows, yrs, subs, funnel = [], [], [], []
    for tfn in TFS.values():
        for dr in ("H", "L"):
            for kk, vn in VARS.items():
                for oc, on in (("ful", "fulfill"), ("inv", "инвалидация")):
                    g0 = D[(D.tf == tfn) & (D.dir == dr) & (D.k == kk)].copy(); g0["f0"] = g0[oc + "_f"]; g0["c0"] = g0[oc + "_c"]
                    row = f"S3 {tfn}/{dr}/{vn}/{on}"
                    funnel.append((row, len(g0), int(g0.fl.sum()), round(float(g0.fl.mean()), 3) if len(g0) else np.nan, int(g0.c0.notna().sum()), g0[g0.fl == 1].coin.nunique()))
                    if len(g0) == 0: continue
                    for cn, m in S1.deltas(g0, "fl").items(): rows.append((row, cn) + S1.boot(m))
                    for y, g in g0.groupby("year"):
                        for cn, m in S1.deltas(g, "fl").items(): yrs.append((row, cn, y) + S1.boot(m)[:3] + (len(m), g[g.fl == 1].coin.nunique()))
                    for sn, g in [("BTC", g0[g0.coin == "BTCUSDT"]), ("ETH", g0[g0.coin == "ETHUSDT"]), ("история с 2020", g0[g0.coin.isin(early)]), ("листинг позже", g0[~g0.coin.isin(early)])]:
                        for cn, m in S1.deltas(g, "fl").items():
                            if len(m): subs.append((row, cn, sn, m.mean(), len(m)))
                    bc = g0.groupby("coin").apply(lambda g: pd.Series({kx: v.mean() if len(v) else np.nan for kx, v in S1.deltas(g, "fl").items()}), include_groups=False)
                    subs.append((row, "по монетам: доля Δ>0 (C1/без фактора)", f"{bc['C1'].notna().sum()} монет", float((bc["C1"] > 0).sum()) / max(1, bc["C1"].notna().sum()), int(bc["C1"].notna().sum())))
    res = pd.DataFrame(rows, columns=["строка", "контроль", "dlt", "lo", "hi", "p", "_", "дней"])
    res["pg"] = np.where(res.dlt > 0, res.p / 2, 1 - res.p / 2); res["pl"] = np.where(res.dlt < 0, res.p / 2, 1 - res.p / 2)
    res = res.drop(columns="_"); m_all = S1.K * 2
    for col in ("pg", "pl"):
        o = res[col].fillna(1).sort_values(); run = 0; adj = {}
        for kx, (i, pv) in enumerate(o.items()): run = max(run, min(1, pv * (m_all - kx))); adj[i] = run
        res["holm_" + ("gt0" if col == "pg" else "lt0")] = res.index.map(adj)
    yr = pd.DataFrame(yrs, columns=["строка", "контроль", "год", "dlt", "lo", "hi", "дней", "монет"])
    yr["даны"] = (yr.монет >= 5) & (yr.дней >= 8); yr["знак"] = np.sign(yr.dlt)
    ver = {}
    for r, g in res.groupby("строка"):
        up = dn = True
        for _, x in g.iterrows():
            z = yr[(yr.строка == r) & (yr.контроль == x.контроль) & yr.даны]; same = len(z) > 0 and z.знак.nunique() == 1
            up &= bool(x.holm_gt0 < .05 and same and z.знак.iloc[0] > 0); dn &= bool(x.holm_lt0 < .05 and same and z.знак.iloc[0] < 0)
        ver[r] = "подтверждено" if up else "оспорено" if dn else "не различимо"
    res["вердикт"] = res.строка.map(ver)
    pd.DataFrame(funnel, columns=["строка", "событий A6", "из них A8 (слом)", "доля", "с контролем C2", "монет"]).to_csv(outp + "-funnel.csv", index=False)
    res.to_csv(outp + "-total.csv", index=False); yr.to_csv(outp + "-years.csv", index=False)
    pd.DataFrame(subs, columns=["строка", "контроль", "срез", "dlt", "дней"]).to_csv(outp + "-slices.csv", index=False)
    print(res.round(4).to_string()); print(pd.DataFrame(funnel).to_string())
