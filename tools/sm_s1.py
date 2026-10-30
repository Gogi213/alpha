"""TK-459 слой S, S1: сессия свипа A5 (1H, k=2, исход x2_a0, N=10) против остальных; два контроля (C1 из l2-таблиц TK-451; «то же без фактора» внутри дня).
Протокол docs/research/P-TK459-synthesis-S-2026-10-30.md §2–3. Вердикт — слово в слово §3 п.3 плана. Холм по K=84 (непосчитанные строки p=1).
python -I tools/sm_s1.py <каталог l2> <выход-префикс> [nx]"""
import sys, glob, os
from multiprocessing import Pool
import numpy as np, pandas as pd

SES = {"asia": lambda h: (h >= 19) | (h < 2), "frankfurt": lambda h: h == 2, "london": lambda h: (h >= 3) & (h < 8), "ny": lambda h: (h >= 8) & (h < 11)}
K = 84; B = 2000; rng = np.random.default_rng(459)


def one(path):
    sym = os.path.basename(path)[3:-7].split("-")[0]
    d = pd.read_csv(path, usecols=["role", "ev", "dir", "t", "brk", "u_a0", "x2_a0"])
    d = d[(d.brk == 0) & (d.u_a0 == 1) & (d.x2_a0 >= 0)]
    real = d[d.role == "real"][["ev", "dir", "t", "x2_a0"]].rename(columns={"x2_a0": "f0"})
    c1 = d[d.role == "C1"].groupby(["ev", "dir"], as_index=False).x2_a0.mean().rename(columns={"x2_a0": "c0"})
    r = real.merge(c1, on=["ev", "dir"], how="left")
    h = pd.to_datetime(r.t, unit="ms", utc=True).dt.tz_convert("America/New_York").dt.hour.values
    for s, f in SES.items(): r[s] = f(h).astype(int)
    r["coin"] = sym
    return r


def boot(m):
    if len(m) < 8: return (np.nan,) * 5 + (len(m),)
    bs = m[rng.integers(0, len(m), (B, len(m)))].mean(1)
    return m.mean(), np.percentile(bs, 2.5), np.percentile(bs, 97.5), 2 * min((bs <= 0).mean(), (bs >= 0).mean()) + 1 / B, 0, len(m)


def deltas(g, fl):
    z = g[(g[fl] == 1) & g.c0.notna()]
    c1 = (z.f0 - z.c0).groupby(z.day).mean()
    a = g[g[fl] == 1].groupby("day").f0.mean(); b = g[g[fl] == 0].groupby("day").f0.mean()
    return {"C1": c1.values, "без фактора": (a - b).dropna().values}


if __name__ == "__main__":
    src, out = sys.argv[1], sys.argv[2]; nx = int(sys.argv[3]) if len(sys.argv) > 3 else 15
    fs = sorted(glob.glob(f"{src}/l2-*-60-2.csv.gz"))
    with Pool(nx) as p: D = pd.concat(p.map(one, fs), ignore_index=True)
    D["day"] = D.t // 86400000; D["year"] = pd.to_datetime(D.t, unit="ms").dt.year
    first = D.groupby("coin").t.min(); early = set(first[first < 1577836800000 + 7 * 86400000].index)
    rows, yrs, subs, funnel = [], [], [], []
    for s in SES:
        for dr, g0 in D.groupby("dir"):
            row = f"S1 {s} | 1H/{dr}"
            funnel.append((row, len(g0), int(g0[s].sum()), round(float(g0[s].mean()), 3), g0[g0[s] == 1].coin.nunique()))
            for cn, m in deltas(g0, s).items(): rows.append((row, cn) + boot(m))
            for y, g in g0.groupby("year"):
                for cn, m in deltas(g, s).items(): yrs.append((row, cn, y) + boot(m)[:3] + (len(m), g[g[s] == 1].coin.nunique()))
            for sn, g in [("BTC", g0[g0.coin == "BTCUSDT"]), ("ETH", g0[g0.coin == "ETHUSDT"]), ("история с 2020", g0[g0.coin.isin(early)]), ("листинг позже", g0[~g0.coin.isin(early)])]:
                for cn, m in deltas(g, s).items():
                    if len(m): subs.append((row, cn, sn, m.mean(), len(m)))
            bc = g0.groupby("coin").apply(lambda g: pd.Series({k: v.mean() if len(v) else np.nan for k, v in deltas(g, s).items()}), include_groups=False)
            subs.append((row, "по монетам: доля Δ>0 (C1/без фактора)", "84 монеты", float((bc["C1"] > 0).sum()) / max(1, bc["C1"].notna().sum()), int(bc["C1"].notna().sum())))
    res = pd.DataFrame(rows, columns=["строка", "контроль", "dlt", "lo", "hi", "p", "_", "дней"])
    # Холм по K=84: p_gt0 / p_lt0 — двусторонний p, знак по dlt; непосчитанные строки (K - посчитанные) = p 1
    res["pg"] = np.where(res.dlt > 0, res.p / 2, 1 - res.p / 2); res["pl"] = np.where(res.dlt < 0, res.p / 2, 1 - res.p / 2)
    res = res.drop(columns="_")
    m_all = K * 2  # проверка = строка × 2 контроля
    for col in ("pg", "pl"):
        o = res[col].fillna(1).sort_values(); run = 0; adj = {}
        for k, (i, p) in enumerate(o.items()): run = max(run, min(1, p * (m_all - k))); adj[i] = run
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
    pd.DataFrame(funnel, columns=["строка", "событий", "с фактором", "доля", "монет"]).to_csv(out + "-funnel.csv", index=False)
    res.to_csv(out + "-total.csv", index=False); yr.to_csv(out + "-years.csv", index=False)
    pd.DataFrame(subs, columns=["строка", "контроль", "срез", "dlt", "дней"]).to_csv(out + "-slices.csv", index=False)
    print(res.round(4).to_string()); print(pd.DataFrame(funnel).to_string())
