"""TK-459 слой S, S5: снятие границы Азии (G5, 02–11 NY) × свип A5 1H (k=2, brk=0) в тот же час и в ту же сторону; исход — касание противоположной границы при глубине <= d (0,25/0,5/1/без предела).
События G5 — код tools/bp_g5.py (one/minutes импортом, логика не переписана), добавлено направление первого снятия; контроль C1 — сдвиги +8/+16 ч (как в G5); «без фактора» — те же G5-события без свипа в час снятия.
Протокол docs/research/P-TK459-synthesis-S-2026-10-30.md §2–3. python -I tools/sm_s5.py <каталог l2> <каталог 1m csv.gz> <выход-префикс> [nx]"""
import sys, os, glob, importlib.util, datetime as dt
from multiprocessing import Pool
import numpy as np, pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(n):
    s = importlib.util.spec_from_file_location(n, os.path.join(HERE, n + ".py")); m = importlib.util.module_from_spec(s); s.loader.exec_module(m); return m


S1 = _load("sm_s1"); G5 = _load("bp_g5")
K = 84; D_NAMES = ["0,25", "0,5", "1", "без предела"]


def coin(args):
    path, l2f = args
    sym = os.path.basename(path).split(".")[0]
    ev = pd.read_csv(l2f, usecols=["role", "dir", "t", "brk"]); ev = ev[(ev.role == "real") & (ev.brk == 0)]
    sw = set(zip(ev.dir, ev.t // 3600000))                      # (сторона снятия, час-бар свипа)
    m0, h, l = G5.minutes(path); rows = []
    for day in pd.date_range(pd.Timestamp(m0 * 60, unit="s").date(), pd.Timestamp((m0 + len(h)) * 60, unit="s").date()):
        b = (day - pd.Timedelta(days=1)).date()
        s0 = int(dt.datetime(b.year, b.month, b.day, 19, tzinfo=G5.NY).astimezone(G5.UTC).timestamp() // 60)
        s = s0 - m0
        r = [G5.one(h, l, s + 60 * k) for k in G5.SHIFTS]
        if r[0] is None or r[0][0] == 0: continue
        c = [x[1] for x in r[1:] if x is not None and x[0] == 1]
        if not c: continue
        H, L = h[s:s + G5.R + G5.T], l[s:s + G5.R + G5.T]
        ah, al = H[:G5.R].max(), L[:G5.R].min()
        hit = np.flatnonzero((H[G5.R:] > ah) | (L[G5.R:] < al)); i = hit[0]
        d = "H" if H[G5.R + i] > ah else "L"; tmin = s0 + G5.R + int(i)
        fl = int((d, tmin // 60) in sw); cm = np.mean(c, axis=0)
        rows.append((s0 * 60000, sym, d, fl) + tuple(r[0][1]) + tuple(cm))
    return rows


if __name__ == "__main__":
    l2, src, outp = sys.argv[1:4]; nx = int(sys.argv[4]) if len(sys.argv) > 4 else 15
    jobs = [(f"{src}/{os.path.basename(f)[3:-7].split('-')[0]}.csv.gz", f) for f in sorted(glob.glob(f"{l2}/l2-*-60-2.csv.gz"))]
    jobs = [j for j in jobs if os.path.exists(j[0])]
    with Pool(nx) as p: R = [x for rr in p.map(coin, jobs) for x in rr]
    D = pd.DataFrame(R, columns=["t", "coin", "dir", "fl"] + [f"f{i}" for i in range(4)] + [f"c{i}" for i in range(4)])
    D["day"] = D.t // 86400000; D["year"] = pd.to_datetime(D.t, unit="ms").dt.year
    first = D.groupby("coin").t.min(); early = set(first[first < 1577836800000 + 7 * 86400000].index)
    rows, yrs, subs, funnel = [], [], [], []
    for dr in ("H", "L"):
        for di, dn in enumerate(D_NAMES):
            g0 = D[D.dir == dr].copy(); g0["f0"] = g0[f"f{di}"]; g0["c0"] = g0[f"c{di}"]
            row = f"S5 Азия {dr} d={dn}"
            funnel.append((row, len(g0), int(g0.fl.sum()), round(float(g0.fl.mean()), 3), g0[g0.fl == 1].coin.nunique()))
            for cn, m in S1.deltas(g0, "fl").items(): rows.append((row, cn) + S1.boot(m))
            for y, g in g0.groupby("year"):
                for cn, m in S1.deltas(g, "fl").items(): yrs.append((row, cn, y) + S1.boot(m)[:3] + (len(m), g[g.fl == 1].coin.nunique()))
            for sn, g in [("BTC", g0[g0.coin == "BTCUSDT"]), ("ETH", g0[g0.coin == "ETHUSDT"]), ("история с 2020", g0[g0.coin.isin(early)]), ("листинг позже", g0[~g0.coin.isin(early)])]:
                for cn, m in S1.deltas(g, "fl").items():
                    if len(m): subs.append((row, cn, sn, m.mean(), len(m)))
            bc = g0.groupby("coin").apply(lambda g: pd.Series({k: v.mean() if len(v) else np.nan for k, v in S1.deltas(g, "fl").items()}), include_groups=False)
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
    pd.DataFrame(funnel, columns=["строка", "событий G5", "со свипом в час", "доля", "монет"]).to_csv(outp + "-funnel.csv", index=False)
    res.to_csv(outp + "-total.csv", index=False); yr.to_csv(outp + "-years.csv", index=False)
    pd.DataFrame(subs, columns=["строка", "контроль", "срез", "dlt", "дней"]).to_csv(outp + "-slices.csv", index=False)
    print(res.round(4).to_string()); print(pd.DataFrame(funnel).to_string())
