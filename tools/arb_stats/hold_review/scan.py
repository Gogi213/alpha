"""Independent recount from raw extraction (/root/rev534/raw): own pairing, own episodes, own stale/contract; compare per pair with executor flag-strict.pkl."""
import glob, json, pickle, sys
import numpy as np, pandas as pd
R = "/root/rev534/raw"
cfg = json.load(open("/root/rev534/syms.json")); T0, T1 = cfg["t0"], cfg["t1"]
EX5 = ["binance", "bybit", "okx", "bingx", "gate"]
TH = 50.0

def fixE(e):
    hi = e // 1_000_000_000
    return hi * 1_000_000 + (e - hi * 1_000_000_000)

def load(ex, kind):
    fs = sorted(glob.glob(f"{R}/{ex}.*.{'book' if kind=='b' else 'trade'}*.tsv"))
    parts = []
    for f in fs:
        try:
            d = pd.read_csv(f, sep="\t", header=None, names=["k","sym","E","rx","p1","q1","p2","q2"], dtype={"sym":str}, engine="c")
        except pd.errors.EmptyDataError:
            continue
        d["fo"] = np.arange(len(d)); d["fi"] = len(parts)
        parts.append(d[d.k == kind])
    d = pd.concat(parts, ignore_index=True)
    d["E"] = fixE(d["E"].to_numpy(np.int64)); d["rx"] = fixE(d["rx"].to_numpy(np.int64))
    return d

def main():
    tab = pickle.load(open("/data/arb-hold/v2/flag-strict.pkl", "rb"))
    df, pairs = tab["df"], tab["pairs"]
    coins = cfg["coins"]
    # symbols per (coin, ex): from executor pairs where known, else standard name
    std = {"binance": "{}USDT", "bybit": "{}USDT", "okx": "{}-USDT-SWAP", "bingx": "{}-USDT", "gate": "{}_USDT"}
    symof = {}
    for (c, a, b), p in pairs.items():
        if c in coins:
            symof[(c, a)], symof[(c, b)] = p["syms"]
    books, trades = {}, {}
    for ex in EX5:
        bk = load(ex, "b"); tr = load(ex, "t")
        for c in coins:
            s = symof.get((c, ex), std[ex].format(c))
            x = bk[bk.sym == s]
            x = x[(x.p1 > 0) & (x.p1 <= x.p2)].sort_values(["E", "rx", "fi", "fo"], kind="stable")
            if len(x) > 20:
                books[(c, ex)] = (x.E.to_numpy(), x.p1.to_numpy(float), x.p2.to_numpy(float), x.q1.to_numpy(float), x.q2.to_numpy(float))
            y = tr[tr.sym == s].sort_values("E", kind="stable")
            trades[(c, ex)] = (y.E.to_numpy(), y.p1.to_numpy(float))
        print(ex, "loaded", len(bk), flush=True)
        del bk, tr
    rows, eps_all = [], []
    for c in coins:
        exs = [e for e in EX5 if (c, e) in books]
        for i, a in enumerate(exs):
            for b in exs[i + 1:]:
                eA, bidA, askA, _, _ = books[(c, a)]; eB, bidB, askB, _, _ = books[(c, b)]
                if not 0.5 < np.median((bidA + askA) / 2) / np.median((bidB + askB) / 2) < 2: continue
                # state at each event: last record with E <= t (ties: last in sort order)
                T = np.union1d(eA, eB); T = T[T < T1]
                ia = np.searchsorted(eA, T, "right") - 1; ib = np.searchsorted(eB, T, "right") - 1
                ok = (ia >= 0) & (ib >= 0); T, ia, ib = T[ok], ia[ok], ib[ok]
                S = (bidA[ia] / askB[ib] - 1) * 1e4; Bu = (askA[ia] / bidB[ib] - 1) * 1e4
                Tn = np.r_[T[1:], T1]
                inw = T >= T0
                M = (S + Bu) / 2
                valid = inw & ((T - eA[ia]) <= 60000) & ((T - eB[ib]) <= 60000)
                mmed = float(np.median(M[valid])) if valid.any() else np.nan
                w = (Tn - T)[valid]; o = np.argsort(M[valid]); cw = np.cumsum(w[o])
                mmed_tw = float(M[valid][o][np.searchsorted(cw, cw[-1] / 2)]) if valid.any() else np.nan
                if abs(mmed) >= 1000: continue
                for side, x in ((1, S), (-1, -Bu)):
                    cond = inw & (x >= TH)
                    dd = np.diff(np.r_[False, cond, False].astype(np.int8))
                    st, en = np.flatnonzero(dd == 1), np.flatnonzero(dd == -1) - 1
                    dur = Tn[en] - T[st]
                    k = dur >= 1000
                    for s0, e0, du in zip(st[k], en[k], dur[k]):
                        t0, t1 = T[s0], T[s0] + du
                        # raw silence: longest gap between consecutive records of each leg intersecting (t0, t1)
                        sil = []
                        for ee in (eA, eB):
                            lo = max(np.searchsorted(ee, t0, "right") - 1, 0); hi = np.searchsorted(ee, t1, "left")
                            seg = ee[lo:hi + 1]
                            g = np.diff(seg) if len(seg) > 1 else np.array([0])
                            sil.append(float(g.max()) if len(g) else 0.0)
                        eps_all.append((c, a, b, side, int(t0), int(du), sil[0], sil[1], mmed, mmed_tw, float(x[s0:e0 + 1].max())))
                rows.append((c, a, b, mmed, mmed_tw))
    E = pd.DataFrame(eps_all, columns=["coin", "exA", "exB", "side", "t", "dur", "silA", "silB", "mmed", "mmed_tw", "edgemax"])
    E.to_pickle("/root/rev534/my_eps.pkl")
    P = pd.DataFrame(rows, columns=["coin", "exA", "exB", "mmed", "mmed_tw"]); P.to_pickle("/root/rev534/my_pairs.pkl")
    pickle.dump({"books": books, "trades": trades}, open("/root/rev534/my_books.pkl", "wb"), protocol=4)
    print("episodes", len(E), "pairs", len(P))

main()
