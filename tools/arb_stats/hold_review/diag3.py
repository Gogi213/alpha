import pickle, json, numpy as np, pandas as pd
cfg = json.load(open("/root/rev534/syms.json")); coins = cfg["coins"]
df = pickle.load(open("/data/arb-hold/v2/flag-strict.pkl","rb"))["df"]
X = df[(df.view=="z")&(df.th==50)&df.coin.isin(coins)]
E = pd.read_pickle("/root/rev534/my_eps.pkl")
B = pickle.load(open("/root/rev534/my_books.pkl","rb"))["books"]
raw = pickle.load(open("/data/arb-hold/raw.pkl","rb"))
b = E.merge(X, on=["coin","exA","exB","side","t"], suffixes=("","_x")); b = b[b.dur>=5000]
ms = (b.silA>5000)|(b.silB>5000)
d = b[~ms & b.f_stale]
leg = np.where(d.raw_gapA>5000, d.exA, d.exB)
print("exec-stale raw-not: n", len(d), " by exchange", pd.Series(leg).value_counts().to_dict(), " exec raw gap q50 s", round(float(np.maximum(d.raw_gapA.where(d.raw_gapA>5000,0), d.raw_gapB.where(d.raw_gapB>5000,0)).median())/1000,1))
print(" dur>=60s:", int((d.dur>=60000).sum()), " pairs:", d.groupby(["coin","exA","exB"]).size().sort_values(ascending=False).head(8).to_dict())
for r in d.sample(4, random_state=1).itertuples():
    for ex, sym, rg, sil in ((r.exA, r.symA, r.raw_gapA, r.silA), (r.exB, r.symB, r.raw_gapB, r.silB)):
        if rg <= 5000: continue
        e = B[(r.coin, ex)][0]; er = raw.get((ex, sym))
        lo, hi = r.t - 10000, r.t + r.dur + 10000
        mine = e[(e>=lo)&(e<=hi)]; ex_ = er[(er>=lo)&(er<=hi)] if er is not None else np.array([])
        print(f"{r.coin} {ex} {sym} t={r.t} dur={r.dur} exec raw_gap={rg:.0f} my sil={sil:.0f} | records in ±10s window: mine {len(mine)} raw.pkl {len(ex_)}; raw.pkl key present {er is not None}, raw.pkl span {er[:1] if er is not None else None}..{er[-1:] if er is not None else None}")
