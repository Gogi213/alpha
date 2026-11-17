"""review-2 (TK-534): re-sim exit-to-centre on z50 episodes with CAUSAL fake layers (known at entry t+d), no whole-window contract layer.
keep  = not (gap/outage on [t-2min, t+1s] | audit on [t, t+1s] | raw silence>5s intersecting (t-2min, t+1s) | tradfi | delist | funding on [t-2min, t+1s])
keep_p= keep & not f_mark (executor's mark layer, bucket incl. up to 10 s after t)"""
import json, pickle, sys, time
from multiprocessing import Pool
sys.path.insert(0, "/opt/arb-hold4/tools/arb_stats")
import numpy as np, pandas as pd
import hold_scan as hs
hs.KINDS = ("c-t",); hs.DELAYS = (1000, 3000)
import hold_resim as hr
from hold_common import DAYS, build_coins
from hold_filter import OUTAGES, GAP_MARGIN_MS, FUND_MARGIN_MS, load_audit, load_marks, max_free_gap, merge, overlaps
V = "/data/arb-hold/v4"; B = "/data/arb-hold/b3"
tab = pickle.load(open(f"{V}/flag-strict.pkl", "rb"))
df = tab["df"]; df = df[(df.th == 50) & (df.view == "z")].reset_index(drop=True)
t = df.t.to_numpy().astype(np.int64); dec = t + 1000; n = len(df)
dead = json.load(open(f"{V}/dead.json")); raw = pickle.load(open(f"{V}/rawgaps.pkl", "rb")); audit = load_audit("/data/collect-gap/out")
gap = np.zeros(n, bool); aud = np.zeros(n, bool); stale = np.zeros(n, bool); fund = np.zeros(n, bool)
exA, exB = df.exA.to_numpy(), df.exB.to_numpy()
for ex in ("binance", "bybit", "okx", "bingx", "gate"):
    iv = [tuple(x) for x in dead.get(ex, [])] + [(int(a.timestamp() * 1000), int(b.timestamp() * 1000)) for a, b in OUTAGES.get(ex, []) + OUTAGES["*"]]
    s, e = merge(iv); on = (exA == ex) | (exB == ex)
    gap[on] |= overlaps(s, e, t[on] - GAP_MARGIN_MS, dec[on])
mk = load_marks(f"{V}/marks.pkl")
for exc, syc in (("exA", "symA"), ("exB", "symB")):
    for (ex, sym), ii in df.groupby([exc, syc]).indices.items():
        a = audit.get(ex, {}).get(sym)
        if a: aud[ii] |= overlaps(*merge(a), t[ii], dec[ii])
        gp, gn = raw["gaps"][(ex, sym)]
        stale[ii] |= max_free_gap(gp, gn, t[ii] - GAP_MARGIN_MS, dec[ii]) > 5000
        d = mk.get(ex, {}).get(sym)
        if d is not None and d["pay"]:
            tf = np.sort(np.array([x[0] for x in d["pay"]], np.int64)); j = np.searchsorted(tf, t[ii] - FUND_MARGIN_MS, "left")
            fund[ii] |= (j < len(tf)) & (tf[np.minimum(j, len(tf) - 1)] <= dec[ii])
del mk
fake = gap | aud | stale | fund | df.f_tradfi.to_numpy() | df.f_delist.to_numpy()
df["keep"] = ~fake; df["keep_p"] = ~fake & ~df.f_mark.to_numpy()
df["c_stale"], df["c_gap"] = stale, gap | aud
print({"z50": n, "causal_keep": int(df.keep.sum()), "causal_keep_mark": int(df.keep_p.sum()), "exec_keep": int(tab["df"].query("th==50 and view=='z'").keep.sum()), "stale_c": int(stale.sum()), "contract_among_causal": int((df.keep & df.f_contract).sum())}, flush=True)
coins, _ = build_coins(f"{B}/parsed", DAYS)
need = df[df.keep | df.keep_p].groupby(["coin", "exA", "exB"]).indices
hr.G.update({"bwork": f"{B}/parsed", "t0": tab["t0"], "t1": tab["t1"], "coins": coins, "df": df, "idx": df.groupby(["coin", "exA", "exB"]).indices})
cols = {}; t_ = time.time(); keys = list(need)
print("pairs", len(keys), flush=True)
with Pool(5, maxtasksperchild=4) as p:
    for k, res in enumerate(p.imap_unordered(hr.work_pair, keys, chunksize=1)):
        for (name, th, view, d, kind), (ids, arr) in res.items():
            for ci, c in enumerate(hs.SIM_COLS):
                cols.setdefault((name, f"{kind}_{d // 1000}_{c}"), []).append(pd.Series(arr[:, ci], index=ids))
        if k % 100 == 0: print(k, len(keys), round(time.time() - t_), flush=True)
out = {}
for name in ("keep", "keep_p"):
    part = df[df[name]].copy()
    for (nm, col), lst in cols.items():
        if nm == name: part[col] = pd.concat(lst)
    out[name] = part
pickle.dump(out, open("/root/rev534/v2_causal_resim.pkl", "wb"), protocol=4)
print("DONE", round(time.time() - t_))
