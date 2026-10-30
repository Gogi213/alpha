"""TK-459 слой S, S2: свип A5 (k=2, исход x2_a0) × положение в диапазоне (G2): нога k=2 до свипа, p>0,5 — премиум; 2 варианта диапазона (а: ТФ свипа; б: старший ТФ).
Решение Судьи docs/research/reviews/TK-459-S2-range-2026-10-30.md; протокол docs/research/P-TK459-synthesis-S-2026-10-30.md §2–3. Фактор = хай снят в премиум / лой в дискаунт против обратного.
python -I tools/sm_s2.py <каталог l2> <каталог 1m csv.gz> <выход-префикс> [nx]   (рядом лежат sm_s1.py; sancho.py — /data/tk0451 или tools/compute)"""
import sys, os, glob, importlib.util
from multiprocessing import Pool
import numpy as np, pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
for p in (HERE, "/data/tk0451", os.path.join(HERE, "compute")):
    if os.path.isdir(p): sys.path.insert(0, p)
from sancho import bars, swings  # noqa: E402
_s = importlib.util.spec_from_file_location("sm_s1", os.path.join(HERE, "sm_s1.py")); S1 = importlib.util.module_from_spec(_s); _s.loader.exec_module(S1)

K = 84; KS = 2
TFS = {60: ("1H", 240), 240: ("4H", 1440), 1440: ("1D", 10080)}   # ТФ свипа -> (имя, старший)


def prep(a):
    """зеркало (знак -1: лой-свип) -> h, l, ok, позиции свингов хая и лоя (в зеркальной системе), начальный номер бара"""
    ok = a.ok.values
    return a.index.min(), ok, a


def legs(h, l, ok):
    return swings(h, ok, KS), swings(-l, ok, KS)


def pos_flag(hh, ll, sh, sl, conf_max, hj, lvl):
    """последняя подтверждённая (idx+KS <= conf_max) нога; свинги-хаи, снятые свипом (уровень < hj), исключены. -> p или nan"""
    ih = sh[(sh + KS <= conf_max)]; ih = ih[hh[ih] >= hj]
    il = sl[(sl + KS <= conf_max)]
    if len(ih) == 0 or len(il) == 0: return np.nan
    a, b = ih[-1], il[-1]
    if a > b:
        z = il[il < a]
        if len(z) == 0: return np.nan
        hi, lo = hh[a], ll[z[-1]]
    else:
        z = ih[ih < b]
        if len(z) == 0: return np.nan
        hi, lo = hh[z[-1]], ll[b]
    return (lvl - lo) / (hi - lo) if hi > lo else np.nan


def one(args):
    l2f, src = args
    sym = os.path.basename(l2f)[3:-7].split("-")[0]; tf = int(os.path.basename(l2f).split("-")[2])
    d = pd.read_csv(l2f, usecols=["role", "ev", "dir", "t", "age", "brk", "u_a0", "x2_a0"])
    d = d[(d.brk == 0) & (d.u_a0 == 1) & (d.x2_a0 >= 0)]
    real = d[d.role == "real"][["ev", "dir", "t", "age", "x2_a0"]].rename(columns={"x2_a0": "f0"})
    if real.empty: return pd.DataFrame()
    c1 = d[d.role == "C1"].groupby(["ev", "dir"], as_index=False).x2_a0.mean().rename(columns={"x2_a0": "c0"})
    r = real.merge(c1, on=["ev", "dir"], how="left")
    path = f"{src}/{sym}.csv.gz"; hname, tf2 = TFS[tf]
    A = bars(path, tf); B = bars(path, tf2); a0 = A.index.min(); b0 = B.index.min()
    out = {"a": np.full(len(r), np.nan), "b": np.full(len(r), np.nan)}
    for dr in ("H", "L"):
        sg = 1 if dr == "H" else -1
        def mir(X): return (X.h.values.astype(float), X.l.values.astype(float)) if sg == 1 else (-X.l.values.astype(float), -X.h.values.astype(float))
        hA, lA = mir(A); hB, lB = mir(B); shA, slA = legs(hA, lA, A.ok.values); shB, slB = legs(hB, lB, B.ok.values)
        for n, (i, e) in enumerate(r.iterrows()):
            if e.dir != dr: continue
            j = int(e.t // (tf * 60000) - a0); ii = j - int(e.age)
            lvl, hj = hA[ii], hA[j]
            out["a"][n] = pos_flag(hA, lA, shA, slA, j - 1, hj, lvl)
            pb = int(e.t // (tf2 * 60000) - b0) - 1       # последний закрытый старший бар до открытия бара свипа
            if pb >= 0: out["b"][n] = pos_flag(hB, lB, shB, slB, pb, hj, lvl)
    r["pa"], r["pb"] = out["a"], out["b"]; r["coin"] = sym; r["tf"] = hname
    return r


if __name__ == "__main__":
    l2, src, outp = sys.argv[1:4]; nx = int(sys.argv[4]) if len(sys.argv) > 4 else 15
    fs = [(f, src) for tf in TFS for f in sorted(glob.glob(f"{l2}/l2-*-{tf}-2.csv.gz"))]
    with Pool(nx) as p: D = pd.concat(p.map(one, fs), ignore_index=True)
    D["day"] = D.t // 86400000; D["year"] = pd.to_datetime(D.t, unit="ms").dt.year
    first = D.groupby("coin").t.min(); early = set(first[first < 1577836800000 + 7 * 86400000].index)
    rows, yrs, subs, funnel = [], [], [], []
    for tfn in ("1H", "4H", "1D"):
        for dr in ("H", "L"):
            for v in ("a", "b"):
                g0 = D[(D.tf == tfn) & (D.dir == dr)].copy(); p = g0["p" + v]
                g0 = g0[p.notna() & (p != 0.5)]
                # фактор: хай снят в премиум (p>0,5) / лой снят в дискаунт; p — в зеркальной системе, поэтому «выше 0,5» для обеих сторон
                g0["prem"] = (g0["p" + v] > 0.5).astype(int)
                row = f"S2 {tfn}/{dr}/{'ТФ свипа' if v == 'a' else 'старший ТФ'}"
                nev = int((D[(D.tf == tfn) & (D.dir == dr)]).shape[0])
                funnel.append((row, nev, len(g0), int(g0.prem.sum()), round(float(g0.prem.mean()), 3) if len(g0) else np.nan, g0[g0.prem == 1].coin.nunique()))
                if len(g0) == 0: continue
                for cn, m in S1.deltas(g0, "prem").items(): rows.append((row, cn) + S1.boot(m))
                for y, g in g0.groupby("year"):
                    for cn, m in S1.deltas(g, "prem").items(): yrs.append((row, cn, y) + S1.boot(m)[:3] + (len(m), g[g.prem == 1].coin.nunique()))
                for sn, g in [("BTC", g0[g0.coin == "BTCUSDT"]), ("ETH", g0[g0.coin == "ETHUSDT"]), ("история с 2020", g0[g0.coin.isin(early)]), ("листинг позже", g0[~g0.coin.isin(early)])]:
                    for cn, m in S1.deltas(g, "prem").items():
                        if len(m): subs.append((row, cn, sn, m.mean(), len(m)))
                bc = g0.groupby("coin").apply(lambda g: pd.Series({k: x.mean() if len(x) else np.nan for k, x in S1.deltas(g, "prem").items()}), include_groups=False)
                subs.append((row, "по монетам: доля Δ>0 (C1/без фактора)", f"{bc['C1'].notna().sum()} монет", float((bc["C1"] > 0).sum()) / max(1, bc["C1"].notna().sum()), int(bc["C1"].notna().sum())))
    res = pd.DataFrame(rows, columns=["строка", "контроль", "dlt", "lo", "hi", "p", "_", "дней"])
    res["pg"] = np.where(res.dlt > 0, res.p / 2, 1 - res.p / 2); res["pl"] = np.where(res.dlt < 0, res.p / 2, 1 - res.p / 2)
    res = res.drop(columns="_"); m_all = K * 2
    for col in ("pg", "pl"):
        o = res[col].fillna(1).sort_values(); run = 0; adj = {}
        for k, (i, pv) in enumerate(o.items()): run = max(run, min(1, pv * (m_all - k))); adj[i] = run
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
    pd.DataFrame(funnel, columns=["строка", "событий", "с флагом", "премиум/дискаунт-флаг=1", "доля", "монет"]).to_csv(outp + "-funnel.csv", index=False)
    res.to_csv(outp + "-total.csv", index=False); yr.to_csv(outp + "-years.csv", index=False)
    pd.DataFrame(subs, columns=["строка", "контроль", "срез", "dlt", "дней"]).to_csv(outp + "-slices.csv", index=False)
    print(res.round(4).to_string()); print(pd.DataFrame(funnel).to_string())
