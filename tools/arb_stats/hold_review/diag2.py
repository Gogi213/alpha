import pickle, json, numpy as np, pandas as pd
cfg = json.load(open("/root/rev534/syms.json")); coins = cfg["coins"]
df = pickle.load(open("/data/arb-hold/v2/flag-strict.pkl","rb"))["df"]
X = df[(df.view=="z")&(df.th==50)&df.coin.isin(coins)]
E = pd.read_pickle("/root/rev534/my_eps.pkl")
b = E.merge(X, on=["coin","exA","exB","side","t"], suffixes=("","_x"))
b = b[b.dur>=5000]
ms = (b.silA>5000)|(b.silB>5000)
d = b[ms & ~b.f_stale]
leg = np.where(d.silA>5000, d.exA, d.exB)
print("mine-stale exec-not: by silent-leg exchange", pd.Series(leg).value_counts().to_dict())
print(d[["coin","exA","exB","dur","silA","silB","ageA","ageB","raw_gapA","raw_gapB"]].head(12).round(0).to_string())
bt = b[ms & b.f_stale]
print("both stale: my max sil q50/q90 s", (np.maximum(bt.silA,bt.silB)/1000).quantile([.5,.9]).round(1).to_dict(), " exec raw gap q50/q90", (np.maximum(bt.raw_gapA,bt.raw_gapB)/1000).quantile([.5,.9]).round(1).to_dict())
