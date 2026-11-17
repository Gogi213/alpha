"""review-2: recount §7 repeat tables and §5 survivors from flag-strict (z50, raw reading), and check raw_unknown survivors."""
import pickle, numpy as np, pandas as pd
V = "/data/arb-hold/v4"
tab = pickle.load(open(f"{V}/flag-strict.pkl", "rb")); df = tab["df"]; TZ = 4*3600_000
print("t0 t1", tab["t0"], tab["t1"], "cols", [c for c in df.columns][:80])
z = df[(df.th == 50) & (df.view == "z")]
for ms in (5000, 60000, 600000):
    s = z[z.dur >= ms]; print(f">= {ms/1000:.0f}s: all {len(s)} keep {int(s.keep.sum())} keep_p {int(s.keep_p.sum())} raw_unknown&keep {int((s.raw_unknown & s.keep).sum())}")
print("raw_unknown total in z50:", int(z.raw_unknown.sum()))
k = z[(z.dur >= 5000) & z.keep].copy(); k["day"] = (k.t + TZ) // 86400000
pp = k.groupby(["coin","exA","exB"]).agg(n=("t","size"), days=("day","nunique"))
print("pairs", len(pp), pd.cut(pp.n, [0,1,5,20,100,1e9]).value_counts(sort=False).to_dict(), "days>=2", int((pp.days>=2).sum()))
pd_ = k.groupby(["coin","exA","exB","day"]).size()
print("pair-days", len(pd_), pd.cut(pd_, [0,1,5,20,1e9]).value_counts(sort=False).to_dict())
print("per day keep>=5s", k.groupby("day").size().to_dict())
# top coins share
print("top coins share keep>=5s:", (k.coin.value_counts(normalize=True).head(10)*100).round(1).to_dict())
# frozen-price residual: price age among keep >=5s survivors
age = np.maximum(k.ageA, k.ageB)/1000
print("survivors max price age s quantiles", age.quantile([.5,.9,.99,1]).round(1).to_dict(), ">60s:", int((age>60).sum()), ">300s:", int((age>300).sum()))
k60 = z[(z.dur>=60000)&z.keep]; a60=np.maximum(k60.ageA,k60.ageB)/1000
print(">=60s survivors price age q", a60.quantile([.5,.9,1]).round(1).to_dict(), " >60s:", int((a60>60).sum()))
print("by leg exchange of the oldest-price leg among keep>=5s with age>30s:", pd.Series(np.where(k.ageA>k.ageB,k.exA,k.exB))[ (age>30).to_numpy()].value_counts().to_dict())
# symbols whose last record is well before exchange end, still present in survivors
sym = tab.get("pairs")
last_ex = {ex: v[1] for ex, v in pickle.load(open(f"{V}/rawgaps.pkl","rb"))["span"].items()}
