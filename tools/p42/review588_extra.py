"""TK-588 review: zero-volume sparseness on U0/U1, spot-check of tpm60/turn1h/turn24h, concentration of the excess over control.

Usage: python3 review588_extra.py <episodes.parquet> <cache> <aggtrades_root> <out.json>
"""
import datetime as dt
import io
import json
import sys
import zipfile

import numpy as np
import pandas as pd
from scipy.stats import rankdata

EP, CACHE, AGG, OUT = sys.argv[1:5]
N_MIN = 106560
rng = np.random.default_rng(5882)


def auc(x, y):
    m = ~np.isnan(x)
    x, y = x[m], y[m]
    n1 = int(y.sum()); n0 = len(y) - n1
    r = rankdata(x)
    return float((r[y].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


cols = ["univ", "side", "sym", "sig_ms", "day", "half", "ok_t60", "o_x1_t60", "o_x1_t60_f01", "emp6h", "flat60",
        "tpm60", "turn1h", "turn24h", "ctrl_n", "ctrl_o3_x1"]
e = pd.read_parquet(EP, columns=cols)
e = e[e.ok_t60].reset_index(drop=True)
e["t"] = (e.sig_ms // 60000).astype(int)
zv6 = np.full(len(e), np.nan); zv24 = np.full(len(e), np.nan); t1h = np.full(len(e), np.nan); t24 = np.full(len(e), np.nan)
for sym, g in e.groupby("sym"):
    a = np.fromfile(f"{CACHE}/{sym}.b42", dtype="<i8", offset=8, count=5 * N_MIN).reshape(5, N_MIN)
    o, qv = a[0], a[4]
    first = int(np.nonzero(o)[0][0])
    z = np.concatenate([[0], np.cumsum(qv == 0)])
    q = np.concatenate([[0], np.cumsum(qv)])
    t = g.t.to_numpy()
    for w, arr in ((360, zv6), (1440, zv24)):
        s0 = np.maximum(t - w, first)
        n = t - s0
        arr[g.index] = np.where(n >= 30, (z[t] - z[s0]) / np.maximum(n, 1), np.nan)
    t1h[g.index] = (q[t] - q[t - 60]) / 1e5
    t24[g.index] = (q[t] - q[t - 1440]) / 1e5
e["zv6h"], e["zv24h"], e["my_t1h"], e["my_t24h"] = zv6, zv24, t1h, t24
out = {"turn1h_maxrel": float(np.nanmax(np.abs(e.my_t1h / e.turn1h - 1))),
       "turn24h_maxrel": float(np.nanmax(np.abs(e.my_t24h / e.turn24h - 1)))}
y = (e.o_x1_t60 == 3).to_numpy()
yf = (e.o_x1_t60_f01 == 3).to_numpy()
for (u, side), g in e.groupby(["univ", "side"]):
    ix = g.index.to_numpy()
    yy, ff = y[ix], yf[ix]
    zz = g.zv6h.to_numpy()
    A = (g.half == "A").to_numpy()
    thr = float(np.nanquantile(zz[A], 0.8))
    Z = zz > thr if thr > 0 else zz > 0
    r = {"n": len(g), "share_zv6h_pos": float(np.nanmean(zz > 0)), "zv6h_q": [float(v) for v in np.nanquantile(zz, [.5, .8, .9, .99])],
         "emp6h_pos": float((g.emp6h > 0).mean()), "thr": thr, "nZ": int(Z.sum()), "o3_Z": float(yy[Z].mean()),
         "o3_out": float(yy[~Z].mean()), "o3f_Z": float(ff[Z].mean()), "o3f_out": float(ff[~Z].mean()),
         "auc_zv6h": auc(zz, yy), "auc_zv6h_A": auc(zz[A], yy[A]), "auc_zv6h_B": auc(zz[~A], yy[~A]),
         "auc_zv24h": auc(g.zv24h.to_numpy(), yy), "auc_zv6h_f01": auc(zz, ff),
         "explained_nonbreak": float((yy[~Z].mean() - yy[Z].mean()) * Z.sum() / (~yy).sum())}
    # stratified by turn24h quintile: Z vs not within quintile
    qb = np.nanquantile(g.turn24h[A], [.2, .4, .6, .8])
    qi = np.searchsorted(qb, g.turn24h.to_numpy())
    r["by_turn_q"] = [{"q": int(q + 1), "nZ": int((Z & (qi == q)).sum()), "o3_Z": float(yy[Z & (qi == q)].mean()) if (Z & (qi == q)).any() else None,
                       "o3_out": float(yy[~Z & (qi == q)].mean())} for q in range(5)]
    if u == "U1":
        hc = (g.ctrl_n > 0).to_numpy()
        cy = g.ctrl_o3_x1.to_numpy()
        vc = g[yy].sym.value_counts()
        keep = ~g.sym.isin(vc.head(5).index).to_numpy()
        r["diff_drop_top5coins"] = float(yy[keep & hc].mean() - np.nanmean(cy[keep & hc]))
        vd = g[yy].day.value_counts()
        keep = ~g.day.isin(vd.head(5).index).to_numpy()
        r["diff_drop_top5days"] = float(yy[keep & hc].mean() - np.nanmean(cy[keep & hc]))
        r["top5coins"] = vc.head(5).to_dict()
        ex = g[g.sym.isin(["KORUUSDT", "STXXUSDT", "CLANKERUSDT", "NXPCUSDT"]) & (side == "long")]
        r["owner_examples_zv6h"] = {f"{s}@{m}": float(v) for s, m, v in zip(ex.sym, ex.sig_ms, ex.zv6h)}
    out[f"{u}_{side}"] = r

# tpm60 spot check from raw aggTrades, 60 random U1 episodes
u1 = e[e.univ == "U1"].sample(60, random_state=5)
bad = 0; mx = 0.0
for r in u1.itertuples():
    d = int(r.sig_ms // 86400000)
    ts = []
    for dd in {d, int((r.sig_ms - 3600000) // 86400000)}:
        name = f"{r.sym}-aggTrades-{(dt.date(2026, 9, 1) + dt.timedelta(days=dd)).isoformat()}"
        with zipfile.ZipFile(f"{AGG}/{r.sym}/{name}.zip") as z, z.open(name + ".csv") as fh:
            ts.append(pd.read_csv(io.BytesIO(fh.read()), usecols=["transact_time"]).transact_time.to_numpy() - 1788220800000)
    t = np.concatenate(ts)
    n = int(((t >= r.sig_ms - 3600000) & (t < r.sig_ms)).sum())
    dev = abs(n / 60 - r.tpm60)
    mx = max(mx, dev)
    bad += dev > 0.02
out["tpm60_spot"] = {"n": 60, "mismatch": int(bad), "max_abs_dev": mx}
json.dump(out, open(OUT, "w"), indent=1)
print("ok")
