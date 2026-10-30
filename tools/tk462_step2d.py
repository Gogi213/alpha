#!/usr/bin/env python3
"""TK-462 шаг 2d (подпункт SPY §4: z = дневной знак SPY<0 против >=0 внутри окна W=60 для 7 классов, без контроля п.4); каркас — шаг 2б.
Старое описание шага 2б: TK-462 шаг 2б (H-N по календарю, без контроля п.4); база — шаг 2а (P-TK462 §3, §4 H-F2, §5 C1/C2/C7/C9): маргинальные вклады причин из выписки сделок (без минуток).
python tk462_step2a.py <entries.csv.gz O1/O2> <o3_dir> <out.csv> [<out_c9.csv>]
net = gross - комиссия В-63 - фандинг (как шаг 1). Вклад в минус = (mean_net[z=0] - mean_net[z=1]) * доля(z=1), bps на сделку:
> 0 — в z=1 хуже остального. Интервал и SE — бутстрап по монетам (B=4000, seed 462); p — нормальное приближение по SE
(B=4000 не разрешает p < 2,5e-4, а порог Холма при K=222 — 2,3e-4). Холм по K=222 (§7); нерассчитанные строки остаются в семействе.
Контроль §3 п.4 (случайные входы в те же окна) здесь НЕ считается -> вердикт «предв.», не итог. O3 — три цели сложены (кластер = монета)."""
import glob, sys
import numpy as np, pandas as pd
from math import erf, sqrt

MK, TK, B, K = 1.26, 3.15, 4000, 222
ent, o3d, cal, out = sys.argv[1:5]
H8 = 8 * 3600 * 1000

a = pd.read_csv(ent, usecols=["sym", "tf", "form", "t0", "reason", "gross", "n_mk", "fund", "risk", "bars", "year"])
a["obj"] = np.where(a.form.astype(str).str.upper().str.startswith("B"), "O2", "O1")
a["coin"] = a.sym
a["fee"] = a.n_mk * MK + (2 - a.n_mk) * TK
a["t_in"] = a.t0; a["t_out"] = a.t0 + (a.bars * a.tf * 60000).round()
parts = [a[["coin", "obj", "year", "t_in", "t_out", "reason", "gross", "fee", "fund", "risk"]]]
for p in sorted(glob.glob(o3d + "/*.csv.gz")):
    d = pd.read_csv(p, usecols=["coin", "t_in_ms", "t_out_ms", "reason", "gross_bps", "fund_bps", "rho_bps"])
    d = d.rename(columns={"t_in_ms": "t_in", "t_out_ms": "t_out", "gross_bps": "gross", "fund_bps": "fund", "rho_bps": "risk"})
    d["obj"] = "O3"; d["fee"] = 2 * TK; d["year"] = pd.to_datetime(d.t_in, unit="ms", utc=True).dt.year
    parts.append(d[["coin", "obj", "year", "t_in", "t_out", "reason", "gross", "fee", "fund", "risk"]])
r = pd.concat(parts, ignore_index=True)
r = r[r.fund.notna()].copy()
r["net"] = r.gross - r.fee - r.fund
dt = pd.to_datetime(r.t_in, unit="ms", utc=True)
r["hour"] = dt.dt.hour; r["dow"] = dt.dt.dayofweek
r["crossed"] = (r.t_out // H8) > (r.t_in // H8)

rng = np.random.default_rng(462)
def contrib(g, z):
    """g: сделки объекта; z: булев массив. Возвращает вклад в минус, bps на сделку (точка, lo, hi, se, p), n1, монет."""
    c = pd.DataFrame({"coin": g.coin.values, "net": g.net.values, "z": z})
    c["n1"] = c.z.astype(int); c["s1"] = np.where(c.z, c.net, 0.0)
    c = c.groupby("coin").agg(n=("net", "size"), s=("net", "sum"), n1=("n1", "sum"), s1=("s1", "sum"))
    n, s, n1, s1 = (c[x].values.astype(float) for x in ["n", "s", "n1", "s1"])
    def f(w):
        N, S, N1, S1 = w @ n, w @ s, w @ n1, w @ s1
        with np.errstate(all="ignore"):
            return ((S - S1) / (N - N1) - S1 / N1) * N1 / N
    k = len(c); pt = f(np.ones((1, k)))[0]
    bs = f(rng.multinomial(k, np.ones(k) / k, size=B).astype(float)); bs = bs[np.isfinite(bs)]
    se = bs.std(ddof=1)
    p = 0.5 * (1 - erf(pt / se / sqrt(2))) if se > 0 and np.isfinite(pt) else np.nan
    return pt, np.quantile(bs, .025), np.quantile(bs, .975), se, p, int(n1.sum()), int((c.n1 > 0).sum())

def terc(x):
    q = np.nanquantile(x, [1 / 3, 2 / 3]); return np.where(x <= q[0], 0, np.where(x <= q[1], 1, 2)), q


from zoneinfo import ZoneInfo
NY = ZoneInfo("America/New_York"); WS = [15, 30, 60, 120]; MIN = 60000
def read(n): return pd.read_csv(f"{cal}/P-TK462-{n}-2026-10-30.csv")
spy = read("spyqqq-daily"); days = sorted(set(spy.date)); early = set(read("nyse-early").date)
def ny_ms(d, hm):
    h, m = map(int, hm.split(":")); return int(pd.Timestamp(f"{d} {h:02d}:{m:02d}", tz=NY).timestamp() * 1000)
opens = np.array([ny_ms(d, "09:30") for d in days]); closes = np.array([ny_ms(d, "13:00" if d in early else "16:00") for d in days])
fo = read("fomc"); bl = read("bls")
def bls_ms(k):
    x = bl[(bl.kind == k) & (bl.date >= "2020-01-01")].drop_duplicates(["kind", "date"]); return np.array([ny_ms(d, pd.Timestamp(t).strftime("%H:%M")) for d, t in zip(x.date, x.time_et)])
er = read("earnings"); er["ms"] = (pd.to_datetime(er.accept_utc, utc=True).map(lambda x: x.timestamp()) * 1000).astype("int64")
# отбор квартального ритма (правило вне исходов): запись тикера берётся, если >= 60 суток после предыдущей взятой
keep = []
for tic, x in er.sort_values("ms").groupby("tic"):
    last = -1e18
    for ms in x.ms:
        if ms - last >= 60 * 86400000: keep.append(ms); last = ms
rep = np.sort(np.array(keep))
print(fo.kind.value_counts().to_dict()); fo = fo[fo.kind.isin(["scheduled", "unscheduled"])]; fo_ms = np.array([ny_ms(d, "14:00") for d in fo.statement_date])
first_open = opens[np.searchsorted(opens, rep, side="right").clip(max=len(opens) - 1)]
CLS = [("US-open", opens), ("US-close", closes), ("FOMC", np.sort(fo_ms)), ("CPI", np.sort(bls_ms("CPI"))), ("NFP", np.sort(bls_ms("NFP"))),
       ("отчёт-публикация", rep), ("отчёт-первое-открытие", np.unique(first_open))]
print("события:", {k: len(v) for k, v in CLS}, "отчётов отобрано:", len(rep))
def inwin(t, ev, W):
    i = np.searchsorted(ev, t, side="right") - 1
    return (i >= 0) & (t < ev[np.clip(i, 0, None)] + W * MIN)


spyd = spy[spy.sym == "SPY"].drop_duplicates("date").set_index("date")
dlist = list(days); didx = {d: i for i, d in enumerate(dlist)}
def ny_date(ms): return pd.Timestamp(ms, unit="ms", tz="UTC").tz_convert(NY)
def neg_of(ms, kind):
    """знак SPY<0 для события: open->close дня события; для отчётов после 16:00 ET — гэп close(d)->open(d+1)."""
    t = ny_date(ms); d = str(t.date())
    if d not in didx: return np.nan
    if kind == "rep" and (t.hour * 60 + t.minute) >= 16 * 60:
        i = didx[d]
        if i + 1 >= len(dlist): return np.nan
        return float(spyd.open[dlist[i + 1]] < spyd.close[d])
    return float(spyd.close[d] < spyd.open[d])
SC = [("US-open", opens, "d"), ("US-close", closes, "d"), ("FOMC", np.sort(fo_ms), "d"), ("CPI", np.sort(bls_ms("CPI")), "d"),
      ("NFP", np.sort(bls_ms("NFP")), "d"), ("отчёт-публикация", rep, "rep"), ("отчёт-первое-открытие", first_open, "rep_first")]
W = 60; rows = []
for obj, g in r.groupby("obj"):
    g = g.reset_index(drop=True); t = g.t_in.values
    for cn, ev, kd in SC:
        if kd == "rep_first":
            neg = np.array([neg_of(m, "rep") for m in rep]); ev2 = first_open
            o = np.argsort(ev2, kind="stable"); ev2 = ev2[o]; neg = neg[o]
        else:
            ev2 = ev; neg = np.array([neg_of(m, "rep" if kd == "rep" else "d") for m in ev])
        i = np.searchsorted(ev2, t, side="right") - 1
        ok = (i >= 0) & (t < ev2[np.clip(i, 0, None)] + W * MIN)
        sg = np.where(ok, neg[np.clip(i, 0, None)], np.nan); ok = ok & np.isfinite(sg)
        nm = f"{cn} W=60: SPY<0 против >=0"
        gi = g[ok].reset_index(drop=True); z = (sg[ok] == 1)
        if z.sum() < 30 or (~z).sum() < 30:
            rows.append(dict(obj=obj, fam="H-N/SPY", row=nm, n1=int(z.sum()), n0=int((~z).sum()), verdict="не оценено (N<30)")); continue
        pt, lo, hi, se, p, n1, nc = contrib(gi, z)
        ys = []
        for y, gy in gi.groupby("year"):
            zy = z[gy.index.values]
            if zy.sum() >= 8 and gy.coin[zy].nunique() >= 5 and (~zy).sum() >= 8:
                ys.append((int(y), round((gy.net[~zy].mean() - gy.net[zy].mean()) * zy.mean(), 3)))
        rows.append(dict(obj=obj, fam="H-N/SPY", row=nm, n1=n1, n0=int((~z).sum()), coins1=nc, mean_z1=round(gi.net[z].mean(), 3),
                         mean_z0=round(gi.net[~z].mean(), 3), contrib_in_window=round(pt, 4), contrib_all=round(pt * len(gi) / len(g), 5),
                         lo=round(lo, 4), hi=round(hi, 4), se=round(se, 4), p=p, by_year=";".join(f"{y}:{v}" for y, v in ys), years_n=len(ys),
                         years_same_sign=len(ys) >= 2 and (all(v > 0 for _, v in ys) or all(v < 0 for _, v in ys))))
o = pd.DataFrame(rows)
tt = o[o.p.notna()].sort_values("p").reset_index()
adj = np.minimum(1, np.maximum.accumulate([(K - i) * p for i, p in enumerate(tt.p)]))
o["p_holm"] = np.nan; o.loc[tt["index"], "p_holm"] = adj
def verdict(x):
    if isinstance(x.verdict, str): return x.verdict
    if x.p_holm < 0.05 and x.contrib_in_window > 0 and x.years_n >= 2 and x.years_same_sign: return "объясняет (предв., без контроля)"
    return "не различимо"
if "verdict" not in o: o["verdict"] = np.nan
o["verdict"] = o.apply(verdict, axis=1)
o.to_csv(out, index=False)
pd.set_option("display.width", 250)
print(o[["obj", "row", "n1", "n0", "mean_z1", "mean_z0", "contrib_in_window", "contrib_all", "lo", "hi", "p_holm", "years_n", "verdict"]].to_string())
