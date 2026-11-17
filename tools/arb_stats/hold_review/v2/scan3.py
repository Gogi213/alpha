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



import pickle as _p
_p.dump({"books": books, "trades": trades, "exend": exend}, open("/root/rev534/v2_books.pkl", "wb"), protocol=4)
b = pd.read_pickle("/root/rev534/v2_matched.pkl")
k = b[(b.dur >= 5000) & b.my_keep].copy()
print("22-coin survivors >= 5 s:", len(k), " of them price unchanged > 5 s on a leg (raw-only survivors):", int(k.f_stale_price.sum()))


def frozen_stretches(e, bid, ask, t0, t1):
    """stretches inside [t0, t1] where best bid/ask did not change for > 5 s: list of (start, end, bid, ask)"""
    i0 = max(np.searchsorted(e, t0, "right") - 1, 0); i1 = np.searchsorted(e, t1, "right")
    ee, bb, aa = e[i0:i1], bid[i0:i1], ask[i0:i1]
    ch = np.r_[True, (bb[1:] != bb[:-1]) | (aa[1:] != aa[:-1])]
    idx = np.flatnonzero(ch); st = np.maximum(ee[idx], t0); en = np.r_[ee[idx[1:]], t1]
    m = (en - st) > 5000
    return [(int(x), int(y), float(bb[i]), float(aa[i])) for x, y, i in zip(st[m], en[m], idx[m])]


rows = []
for r in k.itertuples():
    for leg, ex in (("A", r.exA), ("B", r.exB)):
        if (r.coin, ex) not in books:
            continue
        e, bid, ask, _ = books[(r.coin, ex)]
        te_, tp = trades.get((r.coin, ex), (np.zeros(0, np.int64), np.zeros(0)))
        for st, en, fb, fa in frozen_stretches(e, bid, ask, r.t, r.t + r.dur):
            i0, i1 = np.searchsorted(te_, st, "left"), np.searchsorted(te_, en, "right")
            p = tp[i0:i1]
            out = (p > fa * 1.001) | (p < fb * 0.999)
            rows.append((r.coin, r.exA, r.exB, r.t, ex, (en - st) / 1000, len(p), int(out.sum()), int(((p > fa) | (p < fb)).sum())))
F = pd.DataFrame(rows, columns=["coin", "exA", "exB", "t", "ex", "len_s", "ntr", "nout10", "nout0"])
print("frozen-price stretches > 5 s inside 22-coin survivors:", len(F), "in episodes:", F.groupby(["coin", "exA", "exB", "t"]).ngroups, " by leg exchange:", F.ex.value_counts().to_dict())
print("  stretch length s q:", F.len_s.quantile([.5, .9, 1]).round(1).to_dict())
print("  with trades:", round((F.ntr > 0).mean(), 3), " with a trade outside the frozen quote:", round((F.nout0 > 0).mean(), 3), " > 10 bps outside:", round((F.nout10 > 0).mean(), 3))
ep = F.groupby(["coin", "exA", "exB", "t"]).agg(o10=("nout10", "max")).reset_index()
print("  episodes with a trade > 10 bps outside a frozen quote:", int((ep.o10 > 0).sum()), "by pair:", ep[ep.o10 > 0].groupby(["coin", "exA", "exB"]).size().sort_values(ascending=False).head(8).to_dict())
F.to_csv("/root/rev534/v2_frozen.csv", index=False)
print("DONE")
