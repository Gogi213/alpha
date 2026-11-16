import pickle, numpy as np, pandas as pd
d = pickle.load(open("/data/arb-hold/v2/flag-strict.pkl","rb")); df = d["df"]
z5 = df[(df.view=="z")&(df.th==50)&(df.dur>=5000)]
st = z5[z5.layer=="stale"]
g = np.maximum(np.where(st.raw_gapA>5000, st.raw_gapA, 0), np.where(st.raw_gapB>5000, st.raw_gapB, 0))/1000
for lo,hi in ((5,10),(10,30),(30,60),(60,600),(600,3600),(3600,1e9)):
    m=(g>lo)&(g<=hi); print(f"stale silence {lo}-{hi}s: {m.sum()} ({m.mean():.3f})")
# stale with silence 5-30 s: how many would survive all other filters?
m = (g<=30)
rest = st[m]
other = rest[["f_contract","f_tradfi","f_delist","f_funding","f_mark"]].any(axis=1)
print("stale 5-30s silence:", m.sum(), " of them not hit by any later filter:", (~other).sum(), " >=60s dur:", ((~other)&(rest.dur>=60000)).sum())
print("top pairs of those:", rest[~other].groupby(["coin","exA","exB"]).size().sort_values(ascending=False).head(10).to_dict())
# silent leg size: dollar size at start for stale episodes
print("stale episodes size0 $ q50:", round(st.size0.median(),1), "keep size0 q50:", round(z5[z5.keep].size0.median(),1))
# window
print("t0", d["t0"], "t1", d["t1"])
