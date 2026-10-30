#!/usr/bin/env python3
"""TK-462 шаг 4а (P-TK462 §3 п.4): контроль «случайные входы» для строк «объясняет (предв.)». Контроль = сделки C1/C2 из кэша TK-455
(те же правила выхода на случайных касаниях/псевдо-уровнях той же монеты и ТФ; role C1/C2 в /data/tk0455/run/*.csv.gz), якорные ячейки.
python tk462_step4.py <entries.csv.gz> <tk455_run_dir> <минутки_dir> <cal_dir> <out.csv>
Строки: C7 (O2, комиссии/риск средняя|высокая терциль; границы — по реальным O2), C5 (O2, BTC L=20 по тренду), FOMC SPY<0 W=60 (O1).
Вклад = (mean0-mean1)*доля(z=1); интервал/SE — бутстрап по монетам (B=4000, seed 462). Реальные − контроль: SE = sqrt(se_r^2+se_c^2).
Причина «объясняет» остаётся, только если (реальные − контроль) имеет нижнюю границу > 0 (95 %); иначе это свойство окна/правил, не сигнала."""
import glob, sys
from math import erf, sqrt
import numpy as np, pandas as pd
from zoneinfo import ZoneInfo

MK, TK, B = 1.26, 3.15, 4000
ent, rund, mdir, cal, out = sys.argv[1:6]
rng = np.random.default_rng(462)


def contrib(coin, net, z):
    c = pd.DataFrame({"coin": coin, "net": net, "z": z})
    c["n1"] = c.z.astype(int); c["s1"] = np.where(c.z, c.net, 0.0)
    c = c.groupby("coin").agg(n=("net", "size"), s=("net", "sum"), n1=("n1", "sum"), s1=("s1", "sum"))
    n, s, n1, s1 = (c[x].values.astype(float) for x in ["n", "s", "n1", "s1"])

    def f(w):
        N, S, N1, S1 = w @ n, w @ s, w @ n1, w @ s1
        with np.errstate(all="ignore"):
            return ((S - S1) / (N - N1) - S1 / N1) * N1 / N
    k = len(c); pt = f(np.ones((1, k)))[0]
    bs = f(rng.multinomial(k, np.ones(k) / k, size=B).astype(float)); bs = bs[np.isfinite(bs)]
    return pt, bs.std(ddof=1), int(n1.sum()), int(len(c))


# --- реальные
a = pd.read_csv(ent, usecols=["sym", "tf", "form", "t0", "side", "gross", "n_mk", "fund", "risk", "year"])
a["obj"] = np.where(a.form.astype(str).str.upper().str.startswith("B"), "O2", "O1")
a["coin"] = a.sym; a["fee"] = a.n_mk * MK + (2 - a.n_mk) * TK; a["role"] = "real"
a = a[a.fund.notna()]
# --- контроли C1/C2 из кэша TK-455 (якорь: A — N=10 E0; B — E0·S1·TS·N=10)
cs = []
for p in sorted(glob.glob(rund + "/*USDT-*.csv.gz")):
    if "-agg" in p:
        continue
    d = pd.read_csv(p, usecols=["sym", "tf", "role", "t0", "side", "form", "E", "S", "T", "N", "c3", "reason", "gross", "fund", "risk"])
    d = d[d.role.isin(["C1", "C2"]) & (d.N == 10) & (d.E == "E0") & (d.c3 == 0) & d.gross.notna() & d.fund.notna()]
    d = d[(d.form == "A") | ((d.form == "B") & (d.S == "S1") & (d["T"] == "TS"))]
    cs.append(d)
c = pd.concat(cs, ignore_index=True)
c["obj"] = np.where(c.form == "B", "O2", "O1"); c["coin"] = c.sym
c["fee"] = TK + np.where(c.reason == "target", MK, TK)
c["year"] = pd.to_datetime(c.t0, unit="ms", utc=True).dt.year
cols = ["coin", "obj", "tf", "t0", "side", "gross", "fee", "fund", "risk", "year", "role"]
al = pd.concat([a[cols], c[cols]], ignore_index=True)
al["net"] = al.gross - al.fee - al.fund
print(al.groupby(["obj", "role"]).size().to_dict(), flush=True)


# --- BTC L=20 тренд (как step2c.slope_sign)
def bars(m, tf):
    k = tf * 60000; g = m.groupby(m.t // k)
    return pd.DataFrame({"o": g.t.min() // k * k, "h": g.h.max(), "l": g.l.min(), "c": g.c.last()}).reset_index(drop=True)


btcm = pd.read_csv(f"{mdir}/BTCUSDT.csv.gz", usecols=["t_open_ms", "h", "l", "c"]).rename(columns={"t_open_ms": "t"})
al["c5"] = np.nan
for T in np.unique(al.tf):
    b = bars(btcm, int(T)); ix = np.where(al.tf.values == T)[0]; t = al.t0.values[ix]
    j = np.searchsorted(b.o.values, t - int(T) * 60000, side="right") - 1; ok = j >= 20
    cc = b.c.values; jj = np.where(ok, j, 20); s = np.full(len(ix), np.nan)
    s[ok] = np.sign(cc[jj] - cc[jj - 20])[ok]
    al.iloc[ix, al.columns.get_loc("c5")] = s

# --- FOMC / SPY<0 (как step2d)
NY = ZoneInfo("America/New_York")
read = lambda n: pd.read_csv(f"{cal}/P-TK462-{n}-2026-10-30.csv")
spy = read("spyqqq-daily"); fo = read("fomc"); fo = fo[fo.kind.isin(["scheduled", "unscheduled"])]
spyd = spy[spy.sym == "SPY"].drop_duplicates("date").set_index("date")
fo_ms = np.array([int(pd.Timestamp(f"{d} 14:00", tz=NY).timestamp() * 1000) for d in fo.statement_date])
neg = np.array([float(spyd.close[d] < spyd.open[d]) if d in spyd.index else np.nan for d in fo.statement_date])
o = np.argsort(fo_ms); fo_ms, neg = fo_ms[o], neg[o]
i = np.searchsorted(fo_ms, al.t0.values, side="right") - 1
ok = (i >= 0) & (al.t0.values < fo_ms[np.clip(i, 0, None)] + 60 * 60000)
al["fomc_neg"] = np.where(ok, neg[np.clip(i, 0, None)], np.nan)

# --- границы терциля комиссий/риск — по реальным O2
r2 = al[(al.obj == "O2") & (al.role == "real") & al.risk.notna()]
q = np.nanquantile(r2.fee / r2.risk, [1 / 3, 2 / 3])
al["z7"] = ((al.fee / al.risk) > q[0]).astype(float)
al.loc[al.risk.isna(), "z7"] = np.nan
al["z5"] = np.where(al.c5.notna(), (al.c5 * al.side > 0).astype(float), np.nan)
al["zf"] = np.where(np.isfinite(al.fomc_neg), (al.fomc_neg == 1).astype(float), np.nan)
SPEC = [("O2", "C7: комиссии/риск средняя|высокая", "z7"), ("O2", "C5: BTC L=20 по тренду", "z5"), ("O1", "FOMC W=60: SPY<0", "zf")]
rows = []
for obj, nm, zc in SPEC:
    res = {}
    for grp, sel in (("real", al.role == "real"), ("ctrl", al.role != "real")):
        g = al[(al.obj == obj) & sel & al[zc].notna()]
        if zc == "zf":
            g = g[g.fomc_neg.notna()]
        z = g[zc].values == 1
        if z.sum() < 30 or (~z).sum() < 30:
            res[grp] = None; rows.append(dict(obj=obj, row=nm, group=grp, n1=int(z.sum()), n0=int((~z).sum()), verdict="не оценено (N<30)")); continue
        pt, se, n1, nc = contrib(g.coin.values, g.net.values, z)
        ys = []
        for y, gy in g.groupby("year"):
            zy = (gy[zc].values == 1)
            if zy.sum() >= 8 and gy.coin[zy].nunique() >= 5 and (~zy).sum() >= 8:
                ys.append((int(y), round((gy.net[~zy].mean() - gy.net[zy].mean()) * zy.mean(), 3)))
        res[grp] = (pt, se)
        rows.append(dict(obj=obj, row=nm, group=grp, n1=n1, n0=int((~z).sum()), coins1=nc, mean_z1=round(g.net[z].mean(), 3),
                         mean_z0=round(g.net[~z].mean(), 3), contrib=round(pt, 4), lo=round(pt - 1.96 * se, 4), hi=round(pt + 1.96 * se, 4),
                         se=round(se, 4), by_year=";".join(f"{y}:{v}" for y, v in ys)))
    if res.get("real") and res.get("ctrl"):
        d = res["real"][0] - res["ctrl"][0]; sd = sqrt(res["real"][1] ** 2 + res["ctrl"][1] ** 2)
        v = "объясняет (после контроля)" if d - 1.96 * sd > 0 else ("свойство окна/правил, не сигнала" if res["ctrl"][0] - 1.96 * res["ctrl"][1] > 0 else "не различимо")
        rows.append(dict(obj=obj, row=nm, group="real-ctrl", contrib=round(d, 4), lo=round(d - 1.96 * sd, 4), hi=round(d + 1.96 * sd, 4), se=round(sd, 4), verdict=v))
r = pd.DataFrame(rows); r.to_csv(out, index=False)
pd.set_option("display.width", 250); print(r.to_string())
