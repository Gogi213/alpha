"""TK-459 слой S, S4: свип A5 (k=2, исход x2_a0) × цель-фрактал старшего ТФ (G7.1): последний подтверждённый (k=1, 3-св.) фрактал противоположного типа; пары 1H→D1,W1; 4H→D1,W1; 1D→W1.
Протокол docs/research/P-TK459-synthesis-S-2026-10-30.md §2–3. Фактор = последний фрактал старшего ТФ противоположного типа (хай снят -> лой), против того же типа. Подтверждение фрактала = закрытие бара b+1, использован только если <= открытия бара свипа.
python -I tools/sm_s4.py <каталог l2> <каталог 1m csv.gz> <выход-префикс> [nx]   (рядом лежат sm_s1.py; sancho.py — /data/tk0451 или tools/compute)"""
import sys, os, glob, importlib.util
from multiprocessing import Pool
import numpy as np, pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
for p in (HERE, "/data/tk0451", os.path.join(HERE, "compute")):
    if os.path.isdir(p): sys.path.insert(0, p)
from sancho import bars, swings  # noqa: E402
_s = importlib.util.spec_from_file_location("sm_s1", os.path.join(HERE, "sm_s1.py")); S1 = importlib.util.module_from_spec(_s); _s.loader.exec_module(S1)

K = 84
TFS = {60: ("1H", (1440, 10080)), 240: ("4H", (1440, 10080)), 1440: ("1D", (10080,))}   # ТФ свипа -> (имя, старшие ТФ)
HN = {1440: "D1", 10080: "W1"}


def fractals(path, tf):
    """-> (время подтверждения мс, тип +1 хай/-1 лой) по возрастанию"""
    B = bars(path, tf); h = B.h.values.astype(float); l = B.l.values.astype(float); ok = B.ok.values; b0 = B.index.min(); ct = []; ty = []
    for p in range(1, len(h) - 1):
        if not (ok[p - 1] and ok[p] and ok[p + 1]): continue
        hi = h[p] > h[p - 1] and h[p] > h[p + 1]; lo = l[p] < l[p - 1] and l[p] < l[p + 1]
        if hi == lo: continue
        ct.append((b0 + p + 2) * tf * 60000); ty.append(1 if hi else -1)
    return np.array(ct), np.array(ty)


def one(args):
    l2f, src = args
    sym = os.path.basename(l2f)[3:-7].split("-")[0]; tf = int(os.path.basename(l2f).split("-")[2])
    d = pd.read_csv(l2f, usecols=["role", "ev", "dir", "t", "age", "brk", "u_a0", "x2_a0"])
    d = d[(d.brk == 0) & (d.u_a0 == 1) & (d.x2_a0 >= 0)]
    real = d[d.role == "real"][["ev", "dir", "t", "age", "x2_a0"]].rename(columns={"x2_a0": "f0"})
    if real.empty: return pd.DataFrame()
    c1 = d[d.role == "C1"].groupby(["ev", "dir"], as_index=False).x2_a0.mean().rename(columns={"x2_a0": "c0"})
    r = real.merge(c1, on=["ev", "dir"], how="left")
    path = f"{src}/{sym}.csv.gz"; hname, tf2s = TFS[tf]; r["coin"] = sym; r["tf"] = hname
    for t2 in tf2s:
        ct, ty = fractals(path, t2); k = np.searchsorted(ct, r.t.values, side="right") - 1
        last = np.where(k >= 0, ty[np.maximum(k, 0)], 0)
        want = np.where(r.dir.values == "H", -1, 1)          # снят хай -> цель лой (-1); снят лой -> цель хай (+1)
        r["p" + HN[t2]] = np.where(last == 0, np.nan, (last == want).astype(float))   # 1 = цель-фрактал противоположного типа
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
            for v in [HN[x] for x in dict(TFS.values())[tfn]]:
                g0 = D[(D.tf == tfn) & (D.dir == dr)].copy(); p = g0["p" + v]
                g0 = g0[p.notna()]
                g0["prem"] = g0["p" + v].astype(int)
                row = f"S4 {tfn}→{v}/{dr}"
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
    pd.DataFrame(funnel, columns=["строка", "событий", "с флагом", "фактор=1 (цель-фрактал противоп. типа)", "доля", "монет"]).to_csv(outp + "-funnel.csv", index=False)
    res.to_csv(outp + "-total.csv", index=False); yr.to_csv(outp + "-years.csv", index=False)
    pd.DataFrame(subs, columns=["строка", "контроль", "срез", "dlt", "дней"]).to_csv(outp + "-slices.csv", index=False)
    print(res.round(4).to_string()); print(pd.DataFrame(funnel).to_string())
