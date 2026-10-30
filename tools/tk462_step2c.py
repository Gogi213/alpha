#!/usr/bin/env python3
"""TK-462 шаг 2c (P-TK462 §5 C3–C6, C8): признаки на t0 по минуткам Binance + маргинальные вклады (без контроля п.4 -> «предв.»).
python tk462_step2c.py <entries.csv.gz O1/O2> <o3_dir> <минутки_dir> <step2a.csv> <out.csv> [<features.csv.gz>]
Признаки — только по барам, закрытым <= t0. Старший ТФ (число автора, §12 от 30.10 шаг 2c): 5->60, 60->240, 240->1440, 1440->10080 мин.
ATR = Wilder-подобная средняя (SMA 14) истинного диапазона / close, bps; C8 = медиана суточного quote_v за 30 суток до t0.
Вклад/бутстрап/Холм как в tk462_step2a.py (B=4000, seed 462, K=222; p-значения step2a входят в семейство Холма)."""
import glob, os, sys
from math import erf, sqrt
from multiprocessing import Pool
import numpy as np, pandas as pd

MK, TK, B, K = 1.26, 3.15, 4000, 222
ent, o3d, mdir, s2a, out = sys.argv[1:6]
feat_out = sys.argv[6] if len(sys.argv) > 6 else None
HI = {5: 60, 60: 240, 240: 1440, 1440: 10080}
LS = (10, 20, 40)


def bars(m, tf):
    k = tf * 60000
    g = m.groupby(m.t // k)
    b = pd.DataFrame({"o": g.t.min() // k * k, "h": g.h.max(), "l": g.l.min(), "c": g.c.last()}).reset_index(drop=True)
    return b


def load(coin):
    m = pd.read_csv(f"{mdir}/{coin}.csv.gz", usecols=["t_open_ms", "h", "l", "c", "quote_v"])
    return m.rename(columns={"t_open_ms": "t"})


def last_closed(b_open, tf, t0):
    return np.searchsorted(b_open, t0 - tf * 60000, side="right") - 1


def slope_sign(b, tf, t0, L):
    j = last_closed(b.o.values, tf, t0)
    ok = j >= L
    c = b.c.values
    s = np.full(len(t0), np.nan)
    jj = np.where(ok, j, L)
    s[ok] = np.sign(c[jj] - c[jj - L])[ok]
    return s


btc = {}


def btc_tf(tf):
    if tf not in btc:
        btc[tf] = bars(load("BTCUSDT"), tf)
    return btc[tf]


def feats(args):
    coin, g = args
    m = load(coin)
    t0 = g.t_in.values; tf = g.tf.values
    out = pd.DataFrame(index=g.index)
    for f in ("atr", "c4_10", "c4_20", "c4_40", "c5_10", "c5_20", "c5_40", "btc_ret", "liq"):
        out[f] = np.nan
    for T in np.unique(tf):
        ix = np.where(tf == T)[0]; t = t0[ix]
        b = bars(m, int(T)); j = last_closed(b.o.values, int(T), t)
        tr = np.maximum(b.h - b.l, np.maximum((b.h - b.c.shift()).abs(), (b.l - b.c.shift()).abs()))
        atr = (tr.rolling(14).mean() / b.c * 1e4).values
        ok = j >= 14
        a = np.full(len(ix), np.nan); a[ok] = atr[j[ok]]
        out.iloc[ix, out.columns.get_loc("atr")] = a
        hb = bars(m, HI[int(T)]); bb = btc_tf(int(T)); bh = btc_tf(HI[int(T)])
        for L in LS:
            out.iloc[ix, out.columns.get_loc(f"c4_{L}")] = slope_sign(hb, HI[int(T)], t, L)
            out.iloc[ix, out.columns.get_loc(f"c5_{L}")] = slope_sign(bb, int(T), t, L)
        jb = last_closed(bb.o.values, int(T), t); okb = jb >= 1
        r = np.full(len(ix), np.nan); cb = bb.c.values
        r[okb] = np.abs(cb[jb[okb]] / cb[jb[okb] - 1] - 1) * 1e4
        out.iloc[ix, out.columns.get_loc("btc_ret")] = r
    d = bars_daily = m.groupby(m.t // 86400000).quote_v.sum()
    dk = d.index.values * 86400000
    med = d.rolling(30).median().shift(1).values  # 30 суток строго до текущих суток
    jd = np.searchsorted(dk, t0, side="right") - 1
    ok = jd >= 30
    l = np.full(len(g), np.nan); l[ok] = med[jd[ok]]
    out["liq"] = l
    return out


def load_trades():
    a = pd.read_csv(ent, usecols=["sym", "tf", "form", "t0", "reason", "gross", "n_mk", "fund", "risk", "bars", "side", "year"])
    a["obj"] = np.where(a.form.astype(str).str.upper().str.startswith("B"), "O2", "O1")
    a["coin"] = a.sym; a["fee"] = a.n_mk * MK + (2 - a.n_mk) * TK; a["t_in"] = a.t0
    parts = [a[["coin", "obj", "tf", "year", "t_in", "side", "gross", "fee", "fund"]]]
    for p in sorted(glob.glob(o3d + "/*.csv.gz")):
        d = pd.read_csv(p, usecols=["coin", "side", "t_in_ms", "gross_bps", "fund_bps"])
        d = d.rename(columns={"t_in_ms": "t_in", "gross_bps": "gross", "fund_bps": "fund"})
        d["side"] = np.where(d.side == "long", 1, -1)
        d["obj"] = "O3"; d["tf"] = 5; d["fee"] = 2 * TK
        d["year"] = pd.to_datetime(d.t_in, unit="ms", utc=True).dt.year
        parts.append(d[["coin", "obj", "tf", "year", "t_in", "side", "gross", "fee", "fund"]])
    r = pd.concat(parts, ignore_index=True)
    r = r[r.fund.notna()].copy()
    r["net"] = r.gross - r.fee - r.fund
    return r


rng = np.random.default_rng(462)


def contrib(g, z):
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


if __name__ == "__main__":
    r = load_trades()
    with Pool(int(os.environ.get("NX", 15))) as pool:
        fs = pool.map(feats, [(c, g) for c, g in r.groupby("coin") if os.path.exists(f"{mdir}/{c}.csv.gz")], chunksize=1)
    f = pd.concat(fs)
    r = r.join(f, how="inner")
    print("trades", len(r), "w/ atr", int(r.atr.notna().sum()), "w/ c4_10", int(r.c4_10.notna().sum()), "w/ liq", int(r.liq.notna().sum()))
    if feat_out:
        r.drop(columns=["gross", "fee", "fund"]).to_csv(feat_out, index=False, compression="gzip")
    rows = []
    for obj, g in r.groupby("obj"):
        g = g.reset_index(drop=True)
        defs = []
        for col, fam, nmc in (("atr", "C3", "ранг ATR"), ("liq", "C8", "ликвидность")):
            ok = g[col].notna().values
            if ok.sum() > 100:
                tc, q = terc(g[col].values[ok]); t = np.full(len(g), -1); t[ok] = tc
                for i, nm in enumerate(["низкий", "средний", "высокий"]):
                    defs.append((fam, f"{nmc}: {nm} (q={np.round(q, 1).tolist()})", t == i))
        for fam, pre in (("C4", "c4"), ("C5", "c5")):
            for L in LS:
                s = g[f"{pre}_{L}"].values * g.side.values
                defs.append((fam, f"{'тренд ст.ТФ' if fam == 'C4' else 'тренд BTC'} L={L}: по тренду", s > 0))
                defs.append((fam, f"{'тренд ст.ТФ' if fam == 'C4' else 'тренд BTC'} L={L}: против", s < 0))
        okb = g.btc_ret.notna().values
        if okb.sum() > 100:
            for qq in (0.90, 0.99):
                thr = np.nanquantile(g.btc_ret.values, qq)
                defs.append(("C6", f"шок BTC > q{int(qq * 100)} ({thr:.1f} bps)", okb & (g.btc_ret.values > thr)))
        for fam, nm, z in defs:
            if z.sum() < 30 or (~z).sum() < 30:
                rows.append(dict(obj=obj, fam=fam, row=nm, n1=int(z.sum()), verdict="не оценено (N<30)")); continue
            pt, lo, hi, se, p, n1, nc = contrib(g, z)
            ys = []
            for y, gy in g.groupby("year"):
                zy = z[gy.index.values]
                if zy.sum() >= 8 and gy.coin[zy].nunique() >= 5 and (~zy).sum() >= 8:
                    ys.append((int(y), round((gy.net[~zy].mean() - gy.net[zy].mean()) * zy.mean(), 3)))
            rows.append(dict(obj=obj, fam=fam, row=nm, n1=n1, coins1=nc, share=round(n1 / len(g), 3), mean_z1=round(g.net[z].mean(), 3),
                             mean_z0=round(g.net[~z].mean(), 3), contrib=round(pt, 3), lo=round(lo, 3), hi=round(hi, 3), se=round(se, 3), p=p,
                             by_year=";".join(f"{y}:{v}" for y, v in ys), years_n=len(ys),
                             years_same_sign=len(ys) >= 2 and (all(v > 0 for _, v in ys) or all(v < 0 for _, v in ys))))
    o = pd.DataFrame(rows)
    prev = pd.read_csv(s2a)
    allp = pd.concat([prev.p.dropna(), o.p.dropna()]).sort_values().values
    adj = np.minimum(1, np.maximum.accumulate([(K - i) * p for i, p in enumerate(allp)]))
    o["p_holm"] = [adj[np.searchsorted(allp, p)] if pd.notna(p) else np.nan for p in o.p]

    def verdict(x):
        if isinstance(x.verdict, str): return x.verdict
        if x.p_holm < 0.05 and x.contrib > 0 and x.years_n >= 2 and x.years_same_sign: return "объясняет (предв., без контроля)"
        if x.p_holm < 0.05 and x.contrib < 0 and x.years_n >= 2 and x.years_same_sign: return "не объясняет (лучше остальных)"
        return "не различимо"
    if "verdict" not in o: o["verdict"] = np.nan
    o["verdict"] = o.apply(verdict, axis=1)
    o.to_csv(out, index=False)
    pd.set_option("display.width", 250); pd.set_option("display.max_colwidth", 48)
    print(o[["obj", "fam", "row", "n1", "share", "mean_z1", "mean_z0", "contrib", "lo", "hi", "p_holm", "years_n", "years_same_sign", "verdict"]].to_string())
