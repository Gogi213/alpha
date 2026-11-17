"""review-2 (TK-534): independent recount on 22 coins over the v6 window from own raw extraction (/root/rev534/raw).
Own pairing / episodes (z, 0.5 %, strict), own raw-silence incl. tail after the symbol's last record up to the exchange's last record, own trade-through check.
Other layers (gap, tradfi, delist, funding, mark, contract) — executor flags on matched episodes; own contract for comparison."""
import glob, json, pickle
import numpy as np, pandas as pd
R = "/root/rev534/raw"; V = "/data/arb-hold/v4"
cfg = json.load(open("/root/rev534/syms.json")); coins = cfg["coins"]
W = json.load(open(f"{V}/window.json")); T0, T1 = W["t0"], W["t1"]
EX5 = ["binance", "bybit", "okx", "bingx", "gate"]; TH = 50.0; TZ = 4 * 3600_000


def fixE(e):
    hi = e // 1_000_000_000
    return hi * 1_000_000 + (e - hi * 1_000_000_000)


tab = pickle.load(open(f"{V}/flag-strict.pkl", "rb")); pairs = tab["pairs"]
X = tab["df"]; X = X[(X.view == "z") & (X.th == 50) & X.coin.isin(coins)].copy()
std = {"binance": "{}USDT", "bybit": "{}USDT", "okx": "{}-USDT-SWAP", "bingx": "{}-USDT", "gate": "{}_USDT"}
symof = {}
for (c, a, b), p in pairs.items():
    if c in coins:
        symof[(c, a)], symof[(c, b)] = p["syms"]
books, trades, exend, symlast = {}, {}, {}, {}
END_MS = 1794873600000 + 60_000          # raw dirs end 16.11 24:00 UTC; E beyond = corrupt record
nbad = {}
for ex in EX5:
    acc = {}
    files = sorted(glob.glob(f"{R}/{ex}.*.book*.tsv")) + sorted(glob.glob(f"{R}/{ex}.*.trade*.tsv"))
    emax = 0
    for fi, f in enumerate(files):
        try:
            d = pd.read_csv(f, sep="	", header=None, names=["k", "sym", "E", "rx", "p1", "q1", "p2", "q2"], usecols=["k", "sym", "E", "p1", "p2"],
                            dtype={"k": "category", "sym": "category"}, engine="c")
        except pd.errors.EmptyDataError:
            continue
        d["E"] = fixE(d["E"].to_numpy(np.int64))
        bad = (d.E < T0 - 86_400_000) | (d.E > END_MS)
        if bad.any():
            nbad[ex] = nbad.get(ex, 0) + int(bad.sum()); d = d[~bad]
        if (d.k == "b").any():
            emax = max(emax, int(d.E[d.k == "b"].max()))
        for (k, s), g in d.groupby(["k", "sym"], observed=True):
            acc.setdefault((k, s), []).append((g.E.to_numpy(), g.p1.to_numpy(float), g.p2.to_numpy(float), np.full(len(g), fi, np.int32), np.arange(len(g), dtype=np.int64)))
        del d
    exend[ex] = emax
    for c in coins:
        s = symof.get((c, ex), std[ex].format(c))
        if ("b", s) not in acc:
            continue
        E_, p1, p2, fi_, fo_ = (np.concatenate(z) for z in zip(*acc[("b", s)]))
        o = np.lexsort((fo_, fi_, E_)); E_, p1, p2 = E_[o], p1[o], p2[o]
        symlast[(c, ex)] = int(E_.max())
        ok = (p1 > 0) & (p1 <= p2)
        if ok.sum() > 20:
            books[(c, ex)] = (E_[ok], p1[ok], p2[ok], E_)
        if ("t", s) in acc:
            tE, tp, _, tfi, tfo = (np.concatenate(z) for z in zip(*acc[("t", s)]))
            o = np.lexsort((tfo, tfi, tE)); trades[(c, ex)] = (tE[o], tp[o])
    print(ex, "loaded", len(files), "files, end", exend[ex], "dropped bad E", nbad.get(ex, 0), flush=True); del acc


def silences(c, ex):
    """own raw-silence intervals (> 5 s) incl. tail to exchange end"""
    allE = np.unique(books[(c, ex)][3])
    st, en = allE[:-1], allE[1:]
    k = (en - st) > 5000
    st, en = st[k], en[k]
    if exend[ex] - allE[-1] > 5000:
        st, en = np.r_[st, allE[-1]], np.r_[en, exend[ex]]
    return st, en


def maxsil(st, en, a, b):
    out = np.zeros(len(a))
    for j in range(len(a)):
        i0, i1 = np.searchsorted(en, a[j], "right"), np.searchsorted(st, b[j], "left")
        if i1 > i0:
            out[j] = (en[i0:i1] - st[i0:i1]).max()
    return out


SIL = {k: silences(*k) for k in books}
rows = []
for c in coins:
    exs = [e for e in EX5 if (c, e) in books]
    for i, a in enumerate(exs):
        for b in exs[i + 1:]:
            if (c, a, b) not in pairs:
                continue
            eA, bidA, askA, _ = books[(c, a)]; eB, bidB, askB, _ = books[(c, b)]
            T = np.union1d(eA, eB); T = T[T < T1]
            ia = np.searchsorted(eA, T, "right") - 1; ib = np.searchsorted(eB, T, "right") - 1
            ok = (ia >= 0) & (ib >= 0); T, ia, ib = T[ok], ia[ok], ib[ok]
            S = (bidA[ia] / askB[ib] - 1) * 1e4; Bu = (askA[ia] / bidB[ib] - 1) * 1e4
            Tn = np.r_[T[1:], T1]; inw = T >= T0; M = (S + Bu) / 2
            valid = inw & ((T - eA[ia]) <= 60000) & ((T - eB[ib]) <= 60000)
            mmed = float(np.median(M[valid])) if valid.any() else np.nan
            for side, x in ((1, S), (-1, -Bu)):
                cond = inw & (x >= TH)
                dd = np.diff(np.r_[False, cond, False].astype(np.int8))
                st, en = np.flatnonzero(dd == 1), np.flatnonzero(dd == -1) - 1
                dur = Tn[en] - T[st]; k = dur >= 1000
                for s0, du in zip(st[k], dur[k]):
                    rows.append((c, a, b, side, int(T[s0]), int(du), mmed))
E = pd.DataFrame(rows, columns=["coin", "exA", "exB", "side", "t", "dur", "mmed"])
print("long > 30 min (mine):", int((E.dur > 1_800_000).sum()))
E = E[E.dur <= 1_800_000].reset_index(drop=True)
E["silA"], E["silB"] = 0.0, 0.0
for (c, a, b), ii in E.groupby(["coin", "exA", "exB"]).indices.items():
    t = E.t.to_numpy()[ii]; te = t + E.dur.to_numpy()[ii]
    E.loc[ii, "silA"] = maxsil(*SIL[(c, a)], t, te); E.loc[ii, "silB"] = maxsil(*SIL[(c, b)], t, te)
E["my_stale"] = (E.silA > 5000) | (E.silB > 5000)
key = ["coin", "exA", "exB", "side", "t"]
m = E.merge(X[key + ["dur", "f_stale", "f_stale_price", "f_contract", "f_gap", "f_tradfi", "f_delist", "f_funding", "f_mark", "keep", "keep_p", "raw_gapA", "raw_gapB"]],
            on=key, how="outer", suffixes=("", "_x"), indicator=True)
print("== 22 coins, z 0.5%, strict, <= 30 min; episodes >= 1 s mine", len(E), "executor", len(X), m._merge.value_counts().to_dict())
b = m[m._merge == "both"].copy()
print("dur exact", int((b.dur == b.dur_x).sum()), "of", len(b))
for ms in (5000, 60000, 600000):
    print(f">= {ms // 1000}s: mine {int((E.dur >= ms).sum())} exec {int((X.dur >= ms).sum())}")
for side in ("left_only", "right_only"):
    o = m[(m._merge == side) & (m[["dur", "dur_x"]].max(axis=1) >= 5000)]
    print(side, ">=5s", len(o), "by pair:", o.groupby(["coin", "exA", "exB"]).size().sort_values(ascending=False).head(8).to_dict())
s5 = b[b.dur >= 5000]
print("stale agreement >= 5s (mine x exec):", pd.crosstab(s5.my_stale, s5.f_stale).to_dict())
oth = b[["f_gap", "f_tradfi", "f_delist", "f_funding", "f_mark", "f_contract"]].fillna(False).astype(bool).any(axis=1)
b["my_keep"] = ~b.my_stale & ~oth
b["my_keep_owncontract"] = ~b.my_stale & ~b[["f_gap", "f_tradfi", "f_delist", "f_funding", "f_mark"]].fillna(False).astype(bool).any(axis=1) & ~(b.mmed.abs() >= 25)
for ms in (5000, 60000, 600000):
    s = b[b.dur >= ms]
    print(f"survivors >= {ms // 1000}s: mine {int(s.my_keep.sum())} (own contract {int(s.my_keep_owncontract.sum())}) exec {int(s.keep.sum())}  exec-not-mine {int((s.keep & ~s.my_keep).sum())} mine-not-exec {int((~s.keep & s.my_keep).sum())}")
dis = b[(b.dur >= 5000) & (b.keep != b.my_keep)]
print("disagreements by pair:", dis.groupby(["coin", "exA", "exB", "keep"]).size().sort_values(ascending=False).head(10).to_dict())


def tt(c, ex, t0, t1):
    """trades of the leg during [t0, t1] priced > 10 bps outside the leg's own quote 1 s after the trade; NaN — no trade data around"""
    if (c, ex) not in trades or (c, ex) not in books:
        return np.nan
    te_, tp = trades[(c, ex)]
    i0, i1 = np.searchsorted(te_, t0, "left"), np.searchsorted(te_, t1, "right")
    if i1 == i0:
        return 0.0 if ((te_ > t1).any() and (te_ < t0).any()) else np.nan
    e, bid, ask, _ = books[(c, ex)]
    q = np.maximum(np.searchsorted(e, te_[i0:i1] + 1000, "right") - 1, 0)
    p = tp[i0:i1]
    return float(((p > ask[q] * 1.001) | (p < bid[q] * 0.999)).sum())


def tt_check(df):
    return np.array([(tt(r.coin, r.exA, r.t, r.t + r.dur), tt(r.coin, r.exB, r.t, r.t + r.dur)) for r in df.itertuples()]).reshape(-1, 2)


st5 = b[(b.dur >= 5000) & b.my_stale]
for lab, sub in (("survivors>=5s", b[(b.dur >= 5000) & b.my_keep]), ("survivors>=60s", b[(b.dur >= 60000) & b.my_keep]),
                 ("stale-removed>=5s (sample)", st5.sample(min(3000, len(st5)), random_state=1))):
    a = tt_check(sub)
    anyb = np.nanmax(np.where(np.isnan(a), -1, a), axis=1) if len(a) else np.zeros(0)
    print(f"trade-through > 10 bps vs quote 1 s later, {lab}: n {len(sub)}, with >= 1 such trade {int((anyb > 0).sum())} ({(anyb > 0).mean() * 100:.1f}%), no trade data both legs {int((anyb < 0).sum())}")
    if lab == "survivors>=5s":
        sub = sub.assign(tt=anyb)
        print("  by pair (tt>0):", sub[sub.tt > 0].groupby(["coin", "exA", "exB"]).size().sort_values(ascending=False).head(8).to_dict())
print("symbols with last record > 5 min before exchange end (h):", {f"{c}/{ex}": round((exend[ex] - v) / 3.6e6, 1) for (c, ex), v in symlast.items() if exend[ex] - v > 300_000})
k5 = b[(b.dur >= 5000) & b.my_keep].copy(); k5["day"] = pd.to_datetime(k5.t + TZ, unit="ms").dt.strftime("%d.%m")
print("my survivors >= 5s per day:", k5.groupby("day").size().to_dict(), " by pair top:", k5.groupby(["coin", "exA", "exB"]).size().sort_values(ascending=False).head(10).to_dict())
k10 = b[(b.dur >= 600000) & b.my_keep]
print(">= 10 min mine:", k10.groupby(["coin", "exA", "exB"]).dur.agg(["size", "max"]).to_dict("index"))
pp = k5.groupby(["coin", "exA", "exB"]).agg(n=("t", "size"), days=("day", "nunique"))
print("repeat (mine, 22 coins): pairs", len(pp), pd.cut(pp.n, [0, 1, 5, 20, 100, 1e9]).value_counts(sort=False).to_dict(), "days>=2", int((pp.days >= 2).sum()))
E.to_pickle("/root/rev534/v2_my_eps.pkl"); b.to_pickle("/root/rev534/v2_matched.pkl")
print("DONE")
