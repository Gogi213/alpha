"""v2: покрытие по паре, учёт выпавших, нетто тейкер-выход 8,82. nb_ge20v2.py <out_csv> <drop_csv>"""
import glob, os, sys
import numpy as np, pandas as pd
R = "/root/p40core/out_d1naive2"
P0 = pd.read_csv("/root/p40core/pairs_d1_v3_nobitget.csv")
pairs = P0[(P0.exA != "binance") & (P0.exB != "binance")]
pm = {int(r.pair): (r.exA, r.exB, r.symA) for r in pairs.itertuples()}
rows = []; reason = {}
files = {}
for a in os.listdir(R):
    if not os.path.isdir(f"{R}/{a}"): continue
    for ch in os.listdir(f"{R}/{a}"):
        for f in glob.glob(f"{R}/{a}/{ch}/*"):
            b = os.path.basename(f)
            if b.isdigit() and int(b) in pm: files.setdefault(int(b), []).append(f)
for p in pm:
    if p not in files: reason[p] = "нет файла в out_d1naive2"; continue
    got = 0
    for f in files[p]:
        try:
            B = pd.read_csv(f); N = pd.read_csv(f + ".naive.csv"); P = pd.read_csv(f + ".path.csv")
        except Exception as e:
            reason[p] = "пустой/нечитаемый файл"; continue
        if not (len(B) == len(N) == len(P)): reason[p] = "длины B/N/P не равны"; continue
        if len(B) == 0: reason[p] = "пустой файл"; continue
        m = np.isclose(B.theta, 12.6)
        if not m.any(): reason.setdefault(p, "нет эпизодов θ=12,6"); continue
        B = B[m]; N = N[m]; P = P[m]
        mid_a = (N.a_bid0 + N.a_ask0) / 2; mid_b = (N.b_bid0 + N.b_ask0) / 2
        spr = np.maximum((N.a_ask0 - N.a_bid0) / mid_a, (N.b_ask0 - N.b_bid0) / mid_b) * 1e4
        rows.append(pd.DataFrame({"pair": p, "t0": B.t0_ms.values, "d0": B.d0_bps.values, "peak": np.maximum(B.peak_bps.values, B.d0_bps.values),
              "tconv": B.t_conv_ms.values, "cens": B.censored.values, "pnl": P.pnl_end_bps.values, "spr": spr.values})); got += 1
    if got: reason.pop(p, None)
E = pd.concat(rows, ignore_index=True)
E["day"] = E.t0 // 86400000; E["sec"] = (E.t0 % 3600000) / 1000
E["ok"] = (E.sec >= 10) & (E.spr < 100)
D0 = E.day.min(); T0 = D0 * 86400000
ex_end = {}
for p, g in E.groupby("pair"):
    for e in pm[p][:2]: ex_end[e] = max(ex_end.get(e, 0), g.t0.max())
print("max t0 по биржам, сут от 17.09:", {e: round((t - T0) / 86400000, 2) for e, t in ex_end.items()}, flush=True)
# покрытие пары = минимум концов обеих ног (по эпизодам всех пар биржи), старт T0, максимум 14 сут
cov = lambda a, b: min(14.0, (min(ex_end[a], ex_end[b]) - T0) / 86400000)
out = []
for p, g in E.groupby("pair"):
    ea, eb, sym = pm[p]; C = cov(ea, eb); covdays = int(np.ceil(C))
    base = sym.replace("-USDT-SWAP", "").replace("USDT", "") if sym.endswith("USDT") or sym.endswith("SWAP") else sym
    r = {"pair": p, "sym": sym, "base": base, "xp": "+".join(sorted([ea, eb])), "cov_days": round(C, 2), "n_all": len(g)}
    for tag, gg in (("", g), ("_m", g[g.ok])):
        for X in (20, 30, 50):
            s = gg[gg.peak >= X]
            r[f"n{X}{tag}"] = len(s); r[f"per_day{X}{tag}"] = len(s) / C; r[f"days{X}{tag}"] = s.day.nunique(); 
        s = gg[gg.peak >= 20]; c = s[s.cens == 0]
        r[f"conv20{tag}"] = len(c)
        r[f"tconv_med_s20{tag}"] = float(np.median(c.tconv) / 1000) if len(c) else np.nan
        r[f"fast1s20{tag}"] = float((c.tconv < 1000).mean()) if len(c) else np.nan
        r[f"gross_med20{tag}"] = float(np.median(c.pnl)) if len(c) else np.nan
        r[f"net_tk_med20{tag}"] = float(np.median(c.pnl) - 8.82) if len(c) else np.nan
        r[f"net_tk_mean20{tag}"] = float(np.mean(c.pnl) - 8.82) if len(c) else np.nan
        r[f"net_mk_med20{tag}"] = float(np.median(c.pnl) - 5.04) if len(c) else np.nan
        r[f"peak_med20{tag}"] = float(np.median(s.peak)) if len(s) else np.nan
    out.append(r)
O = pd.DataFrame(out).sort_values("per_day20_m", ascending=False); O.to_csv(sys.argv[1], index=False)
dr = pairs[~pairs.pair.isin(O.pair)][["pair", "exA", "symA", "exB"]].copy()
dr["reason"] = dr.pair.map(lambda p: reason.get(int(p), "нет эпизодов θ=12,6"))
dr.to_csv(sys.argv[2], index=False)
print(len(pairs), len(O), len(dr)); print(dr.reason.value_counts()); print(dr.to_string())
print(O.cov_days.value_counts())
