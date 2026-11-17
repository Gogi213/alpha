import pickle, numpy as np, pandas as pd
d = pickle.load(open("/data/arb-hold/v2/flag-strict.pkl","rb")); df = d["df"]
z = df[(df.view=="z")&(df.th==50)]
print("z50 total>=1s", len(z), ">=5s", (z.dur>=5000).sum(), ">=60", (z.dur>=60000).sum(), ">=600", (z.dur>=600000).sum())
z5 = z[z.dur>=5000]
print("layer counts >=5s:", z5.layer.value_counts().to_dict())
# stale by exchange of the stale leg
st = z5[z5.layer=="stale"]
legA = st.raw_gapA>5000; legB = st.raw_gapB>5000
exs = pd.concat([st.exA[legA], st.exB[legB]]).value_counts()
print("stale leg exchange (>=5s z50):", exs.to_dict())
print("stale raw gap quantiles s:", (np.maximum(st.raw_gapA.where(legA,0), st.raw_gapB.where(legB,0))/1000).quantile([.1,.25,.5,.75,.9,.99]).round(1).to_dict())
# per-exchange share of stale leg with raw gap > 60s
g = np.maximum(st.raw_gapA.where(legA,0), st.raw_gapB.where(legB,0))
print("share raw gap>60s", (g>60000).mean().round(3), ">600s", (g>600000).mean().round(3))
# liquid coins
for c in ["BTC","ETH","SOL","XRP","DOGE","BNB","SUI","ADA","LINK","1INCH","INIT","SOPH","KAVA"]:
    s = z5[z5.coin==c]
    if len(s): print(c, len(s), s.layer.value_counts().to_dict())
# top coins removed by stale
print("top stale coins:", st.coin.value_counts().head(15).to_dict())
# contract
ct = z5[z5.layer=="contract"]
pp = ct.groupby(["coin","exA","exB"]).agg(n=("t","size"), Mmed=("pair_Mmed","first"), mg=("mark_gap","median")).sort_values("n", ascending=False)
print("contract pairs:", ct.groupby(["coin","exA","exB"]).ngroups); print(pp.head(25).round(1).to_string())
print("contract |Mmed| dist bps:", pp.Mmed.abs().quantile([.1,.25,.5,.75,.9]).round(1).to_dict())
print("contract pairs |Mmed|<50:", (pp.Mmed.abs()<50).sum(), "episodes", pp.n[pp.Mmed.abs()<50].sum())
print("contract, mark gap known share", ct.mark_gap.notna().mean().round(3), " |markgap - Mmed| median", (ct.mark_gap - ct.pair_Mmed).abs().median())
