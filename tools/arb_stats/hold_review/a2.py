import pickle, numpy as np, pandas as pd
r = pickle.load(open("/data/arb-hold/v2/resim-strict.pkl","rb"))
k = r["keep"]
for th, view in ((50,"z"),(100,"z"),(50,"c")):
    s = k[(k.th==th)&(k.view==view)]
    for d in (1,3,5):
        p = f"c-t_{d}_"
        e = s[s[p+"net"].notna()]
        hold = (e[p+"te"] - e[p+"tx"])/1000
        sp = e.spA + e.spB
        print(f"th{th} {view} d{d} c-t n={len(e)} hold_s q50={hold.median():.1f} q90={hold.quantile(.9):.1f} share hold<=d+0.5s={(hold<=d+0.5).mean():.3f}"
              f" gross q50={e[p+'gross'].median():.1f} edge_tx q50={e[p+'edge_tx'].median():.1f} c_tx q50={e[p+'c_tx'].median():.1f}"
              f" sideadj(edge_tx-c_tx) q50={(np.where(e.side>0, e[p+'edge_tx']-e[p+'c_tx'], -e[p+'edge_tx']+e[p+'c_tx'])).__array__().__abs__().mean():.1f} spread(A+B) q50={sp.median():.1f}"
              f" c0nan={e.c0.isna().mean():.2f}")
s = k[(k.th==50)&(k.view=="z")]
p="c-t_1_"; e=s[s[p+"net"].notna()]
# how far is centre from mid at entry, in direction of side
mid_tx = np.where(e.side>0, e[p+"edge_tx"] + (e.spA+e.spB)/2, e[p+"edge_tx"] - (e.spA+e.spB)/2)   # approx M at entry
dist = e.side*(mid_tx - e[p+"c_tx"])
print("side*(M_entry - centre) q10/50/90:", np.nanquantile(dist,[.1,.5,.9]).round(1), " share<=0 (exit at once):", np.nanmean(dist<=0).round(3))
print("pair_Mmed abs q50 for z50 keep:", round(s.pair_Mmed.abs().median(),1))
# by hold buckets
hold=(e[p+"te"]-e[p+"tx"])/1000
for lo,hi in ((0,1.5),(1.5,5),(5,60),(60,1e9)):
    m=(hold>lo)&(hold<=hi); print(f"hold {lo}-{hi}s n={m.sum()} gross q50={e[p+'gross'][m].median():.1f} net q50={e[p+'net'][m].median():.1f}")
# z-t exit
for kind in ("z-t","x1-t"):
    p=f"{kind}_1_"; e2=s[s[p+"net"].notna()]; h=(e2[p+"te"]-e2[p+"tx"])/1000
    print(kind, "n", len(e2), "hold q50", round(h.median(),1), "gross q50", round(e2[p+"gross"].median(),1), "how2 share", round((e2[p+"how"]==2).mean(),3))
# size distribution $ and top-of-book
print("size0 $ q50:", s["size0"].median(), " c-t_1_size q50", s["c-t_1_size"].median())
# local centre vs pair median: why contract filter misses
e = s[s["c-t_1_net"].notna()]
print("z50 keep: |c_tx| q50", round(e["c-t_1_c_tx"].abs().median(),1), " share |c_tx|>=25:", round((e["c-t_1_c_tx"].abs()>=25).mean(),3))
# net if entries restricted to deviation from local centre >= 25 bps
dev = np.where(e.side>0, e["c-t_1_edge_tx"] - e["c-t_1_c_tx"], e["c-t_1_c_tx"] - e["c-t_1_edge_tx"])
for lo in (0, 25, 50):
    m = dev >= lo
    print(f"dev from local centre >= {lo}: n={m.sum()} net q50={np.median(e['c-t_1_net'][m]):.1f} share>0={np.mean(e['c-t_1_net'][m]>0):.2f}")
print("top pairs in z50 keep entries:", e.groupby(["coin","exA","exB"]).size().sort_values(ascending=False).head(10).to_dict())
