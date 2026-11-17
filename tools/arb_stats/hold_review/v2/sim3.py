"""review-2: stats of the causal-filter re-sim (sim2) for exit-to-centre, view z, 0.5 %."""
import pickle, numpy as np, pandas as pd
r = pickle.load(open("/root/rev534/v2_causal_resim.pkl", "rb")); TZ = 4 * 3600_000
ex1 = pd.read_csv("/root/rev534/v2_trades1s.csv")
def boot(df, B=5000, seed=3):
    rng = np.random.default_rng(seed); cs = df.coin.unique(); g = {c: df[df.coin == c].net.to_numpy() for c in cs}; o = []
    for _ in range(B):
        v = np.concatenate([g[c] for c in rng.choice(cs, len(cs))]); o.append((np.median(v), v.mean()))
    o = np.array(o); return np.quantile(o[:, 0], [.025, .975]).round(1).tolist(), np.quantile(o[:, 1], [.025, .975]).round(1).tolist()
for name in ("keep", "keep_p"):
    k = r[name]
    for d in (1, 3):
        p = f"c-t_{d}_"; e = k[k[p + "net"].notna()].copy()
        e["net"], e["pl"], e["tx"], e["te"] = e[p + "net"], e[p + "pl"], e[p + "tx"], e[p + "te"]
        e["bx"] = (e.exA == "bingx") | (e.exB == "bingx"); e["day"] = pd.to_datetime(e.t + TZ, unit="ms").dt.strftime("%d.%m")
        print(f"== causal {name} d={d}: n {len(e)} coins {e.coin.nunique()} net med {e.net.median():.1f} mean {e.net.mean():.1f} share>0 {(e.net>0).mean():.2f} pl med {e.pl.median():.1f}; coin-boot median/mean CI {boot(e)}")
        print(f"   bingx n {int(e.bx.sum())} med {e[e.bx].net.median():.1f} | no-bingx n {int((~e.bx).sum())} med {e[~e.bx].net.median():.1f}" + (f" CI {boot(e[~e.bx])}" if (~e.bx).sum() > 5 else ""))
        print(f"   contract-flagged pairs n {int(e.f_contract.sum())} med {e[e.f_contract].net.median():.1f} | not contract n {int((~e.f_contract).sum())} med {e[~e.f_contract].net.median():.1f}")
        print(f"   exec-stale(full episode) n {int(e.f_stale.sum())} med {e[e.f_stale].net.median():.1f} | dur<5s n {int((e.dur<5000).sum())} med {e[e.dur<5000].net.median():.1f} | dur>=5s med {e[e.dur>=5000].net.median():.1f}")
        print("   per day:", e.groupby("day").net.agg(["size", "median"]).round(1).to_dict("index"))
        if name == "keep" and d == 1:
            j = e.merge(ex1[["coin", "exA", "exB", "side", "t"]], on=["coin", "exA", "exB", "side", "t"], how="inner")
            print("   overlap with executor 177:", len(j))
