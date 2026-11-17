"""review-2 (TK-534): 1 s exit-to-centre trades: concentration, BingX, coin-cluster bootstrap, leg silence at entry/exit, per day."""
import pickle, sys, json
import numpy as np, pandas as pd
V = "/data/arb-hold/v4"
r = pickle.load(open(f"{V}/resim-strict.pkl", "rb"))
raw = pickle.load(open(f"{V}/rawgaps.pkl", "rb"))
TZ = 4 * 3600_000
def in_sil(ex, sym, x):
    gp, gn = raw["gaps"].get((ex, sym), (np.zeros(0, np.int64), np.zeros(0, np.int64)))
    i = np.searchsorted(gp, x, "right") - 1
    ok = (i >= 0) & (x < gn[np.maximum(i, 0)])
    return np.where(ok, (x - gp[np.maximum(i, 0)]) / 1000, 0.0)
def boot_med(df, col, B=5000, seed=3):
    rng = np.random.default_rng(seed); coins = df.coin.unique(); g = {c: df[df.coin == c][col].to_numpy() for c in coins}
    out = []
    for _ in range(B):
        pick = rng.choice(coins, len(coins)); v = np.concatenate([g[c] for c in pick]); out.append((np.median(v), v.mean()))
    out = np.array(out); return np.quantile(out[:, 0], [.025, .975]).round(1), np.quantile(out[:, 1], [.025, .975]).round(1)
for name in ("keep", "keep_p"):
    k = r[name]
    s = k[(k.th == 50) & (k.view == "z")]
    for d in (1, 3, 5):
        p = f"c-t_{d}_"
        e = s[s[p + "net"].notna()].copy()
        e["net"], e["gross"], e["tx"], e["te"], e["pl"], e["size"] = e[p + "net"], e[p + "gross"], e[p + "tx"], e[p + "te"], e[p + "pl"], e[p + "size"]
        e["bx"] = (e.exA == "bingx") | (e.exB == "bingx")
        e["day"] = pd.to_datetime((e.t + TZ), unit="ms").dt.strftime("%d.%m")
        print(f"== {name} d={d}s n={len(e)} net med {e.net.median():.1f} mean {e.net.mean():.1f} pl med {e.pl.median():.1f} gross med {e.gross.median():.1f} spread(A+B) med {(e.spA+e.spB).median():.1f} usd/trade med {(e['size']).median():.0f}")
        if d != 1 and name == "keep":
            continue
        print("  coins", e.coin.nunique(), "pairs", e.groupby(['coin','exA','exB']).ngroups, " boot by coin: median", *boot_med(e, "net"))
        print("  bingx legs: n", int(e.bx.sum()), "net med", round(e[e.bx].net.median(), 1), "| no bingx: n", int((~e.bx).sum()), "net med", round(e[~e.bx].net.median(), 1), "mean", round(e[~e.bx].net.mean(), 1))
        if (~e.bx).sum() > 5: print("  no-bingx boot by coin:", *boot_med(e[~e.bx], "net"))
        pc = e.groupby("coin").agg(n=("net", "size"), med=("net", "median"), sum_bps=("net", "sum")).sort_values("n", ascending=False)
        print("  per coin:", pc.head(12).round(1).to_dict("index"))
        top = pc.index[0]; print(f"  without top coin {top}: n {len(e[e.coin!=top])} med {e[e.coin!=top].net.median():.1f}")
        print("  dur<5s:", int((e.dur < 5000).sum()), "med", round(e[e.dur < 5000].net.median(), 1), "| dur>=5s:", int((e.dur >= 5000).sum()), "med", round(e[e.dur >= 5000].net.median(), 1))
        # silence of raw records at entry/exit
        for col in ("tx", "te"):
            sa = np.zeros(len(e)); sb = np.zeros(len(e))
            for (ex, sym), ii in e.groupby(["exA", "symA"]).indices.items(): sa[ii] = in_sil(ex, sym, e[col].to_numpy()[ii].astype(np.int64))
            for (ex, sym), ii in e.groupby(["exB", "symB"]).indices.items(): sb[ii] = in_sil(ex, sym, e[col].to_numpy()[ii].astype(np.int64))
            m = np.maximum(sa, sb); e[f"sil_{col}"] = m
            print(f"  leg inside raw silence>5s at {col}: {int((m>0).sum())} (max {m.max():.0f}s)")
        bad = (e.sil_tx > 0) | (e.sil_te > 0)
        print("  without silent-at-entry/exit:", int((~bad).sum()), "med", round(e[~bad].net.median(), 1))
        print("  per day:", e.groupby("day").net.agg(["size", "median", "mean"]).round(1).to_dict("index"))
        hold = (e.te - e.tx) / 1000
        print("  hold q50", round(hold.median(), 1), "share hold<=2s", round((hold <= 2).mean(), 3), "net by hold<=2s:", round(e[hold <= 2].net.median(), 1), ">2s:", round(e[hold > 2].net.median(), 1))
        print("  edge_tx q50", round(e[p+'edge_tx'].median(),1), " dev at signal ~ (edge0 - c0) q50", round((e.edge0 - e.side*e.c0).median(),1) if 'c0' in e else None)
        if name == "keep" and d == 1:
            e[["coin","exA","exB","symA","symB","side","t","dur","net","gross","pl","tx","te","sil_tx","sil_te","bx","day","spA","spB","edge0","size"]].to_csv("/root/rev534/v2_trades1s.csv", index=False)
