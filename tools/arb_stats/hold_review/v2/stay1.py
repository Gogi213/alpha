"""review-2: independent re-evaluation of stay.pkl — own threshold fit (max BA on fit days), BA on test, both directions; scope of labelled units."""
import pickle, numpy as np, pandas as pd
st = pickle.load(open("/data/arb-hold/v4/stay.pkl", "rb"))
st = st["st"]
print(type(st), getattr(st, "shape", None)); print(list(st.columns))
TZ = 4*3600_000; DAY = 86400_000
st = st[st.dur >= 5000].copy()
st["d"] = pd.to_datetime(st.t + TZ, unit="ms").dt.strftime("%d.%m")
u = st[~st.f_fake & st.d.isin(["13.11", "14.11", "15.11", "16.11"])]
print("after layers in good days", len(u), "label counts", u.label.value_counts().to_dict())
L = u[u.label >= 0].copy(); L["stay"] = (L.label == 0).astype(int)
print("labelled", len(L), "stay", int(L.stay.sum()), "coins", L.coin.nunique(), "pairs", L.groupby(['coin','exA','exB']).ngroups)
print("top coins share %", (L.coin.value_counts(normalize=True).head(8)*100).round(1).to_dict())
print("per day stay/all", L.groupby("d").stay.agg(['sum','size']).to_dict('index'))
def ba(y, p):
    tp = (p & (y == 1)).sum(); fn = ((~p) & (y == 1)).sum(); tn = ((~p) & (y == 0)).sum(); fp = (p & (y == 0)).sum()
    return 0.5 * (tp / max(tp + fn, 1) + tn / max(tn + fp, 1))
def fit(y, x):
    ok = np.isfinite(x); best = (0.5, None, None)
    qs = np.unique(np.quantile(x[ok], np.linspace(0.02, 0.98, 97))) if ok.sum() > 10 else []
    for q in qs:
        for sg in (1, -1):
            p = ok & ((x >= q) if sg > 0 else (x <= q)); b = ba(y, p)
            if b > best[0]: best = (b, q, sg)
    return best
FEATS = ["edge0", "hist_conv6", "hist_n6", "since_end", "lvl_shift6", "dev_lvl6", "mark_conf", "mark_off", "fund_diff", "depth", "spread", "flow_min", "mover", "vol10", "bingx"]
blocks = {"A": (["13.11", "14.11"], ["15.11", "16.11"]), "B": (["15.11", "16.11"], ["13.11", "14.11"])}
for nm, (fd, td) in blocks.items():
    tr = L[L.d.isin(fd)]; te = L[L.d.isin(td)]
    # embargo as protocol: fit units within 6h30 of test block dropped, test units within 30 min of fit block dropped
    lo_te, hi_te = te.t.min(), te.t.max(); lo_tr, hi_tr = tr.t.min(), tr.t.max()
    tr = tr[(tr.t < lo_te - 6.5*3600_000) | (tr.t > hi_te + 6.5*3600_000)]
    te = te[(te.t < lo_tr - 1800_000) | (te.t > hi_tr + 1800_000)]
    print(f"== {nm}: fit {len(tr)} ({int(tr.stay.sum())} stay) test {len(te)} ({int(te.stay.sum())} stay)")
    for f in FEATS:
        if f not in L: continue
        x_tr = tr[f].to_numpy(float); x_te = te[f].to_numpy(float)
        b_tr, q, sg = fit(tr.stay.to_numpy(), x_tr)
        if q is None: print(f, "no fit"); continue
        p = np.isfinite(x_te) & ((x_te >= q) if sg > 0 else (x_te <= q))
        print(f"  {f:12s} fitBA {b_tr:.3f} thr {q:.3f} sign {'>=' if sg>0 else '<='} testBA {ba(te.stay.to_numpy(), p):.3f} cover {np.isfinite(x_te).mean()*100:.0f}%")
