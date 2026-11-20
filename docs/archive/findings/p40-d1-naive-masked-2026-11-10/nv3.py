"""D1 наивный, маски (CEO 10.11 22:22): без входов в первые 10 с часа (UTC), без входов со спредом любой ноги >=100 bps, оба. θ=12,6. Запуск: nv3.py <prelim> <outdir>"""
import csv, sys, json, collections
import numpy as np
P, OUT = sys.argv[1], sys.argv[2]
rng = np.random.default_rng(1)
pairs = {int(r["pair"]): r for r in csv.DictReader(open("/root/p40core/pairs_d1_v3_nobitget.csv"))}
cls = {}
for r in csv.DictReader(open(f"{P}/universe-assets.csv")):
    for k, v in r.items():
        if k.endswith("_futures") and v: cls[v] = r["class"]
def klass(pr):
    for c in "AB":
        if pr["sym" + c] in cls: return cls[pr["sym" + c]]
    return "?"
def num(x):
    try: return float(x)
    except: return float("nan")
cols = collections.defaultdict(list)
for sp in [l.strip() for l in open(f"{P}/specs.txt") if l.strip()]:
    a, d, p, b, base = sp.split(":", 4)
    R = list(csv.DictReader(open(base))); Q = list(csv.DictReader(open(base + ".path.csv"))); F = list(csv.DictReader(open(base + ".features.csv")))
    assert len(R) == len(Q) == len(F)
    for r, q, f in zip(R, Q, F):
        if abs(float(r["theta"]) - 12.6) > 1e-9: continue
        assert r["t0_ms"] == q["t0_ms"] == f["t_start"]
        cols["asset"].append(int(a)); cols["pair"].append(int(p)); cols["t0"].append(int(r["t0_ms"])); cols["d0"].append(num(r["d0_bps"]))
        cols["cens"].append(int(r["censored"])); cols["pnl"].append(num(q["pnl_end_bps"])); cols["spr"].append(num(f["spread"])); cols["tconv"].append(num(r["t_conv_ms"]))
T = {k: np.array(v) for k, v in cols.items()}; n = len(T["asset"]); print("эпизодов θ=12,6:", n, flush=True)
np.savez(f"{OUT}/nv3_ep.npz", **T)
pk = {p: (pairs[p]["exA"], pairs[p]["exB"], klass(pairs[p]), pairs[p]["symA"]) for p in set(T["pair"].tolist())}
xp = np.array(["+".join(sorted(pk[p][:2])) for p in T["pair"]]); cl = np.array([pk[p][2] for p in T["pair"]])
conv = T["cens"] == 0
h10 = (T["t0"] % 3600000) < 10000
sp100 = ~(T["spr"] < 100)          # NaN спред — тоже в маску (неизвестно)
sp_nan = ~np.isfinite(T["spr"])
MK = 5.04; TK = 2*1.26 + 2*3.15
def ci(v, a, B=300):
    ok = np.isfinite(v); v = v[ok]; a = a[ok]
    if len(v) == 0: return (np.nan,)*3
    ua, inv = np.unique(a, return_inverse=True)
    s = np.bincount(inv, weights=v, minlength=len(ua)); c = np.bincount(inv, minlength=len(ua)).astype(float); m = s.sum()/c.sum(); bs = []
    for _ in range(B):
        k = rng.integers(0, len(ua), len(ua)); bs.append(s[k].sum()/c[k].sum())
    return m, np.percentile(bs, 2.5), np.percentile(bs, 97.5)
def row(mask, label, full=False):
    m = mask & conv; p = T["pnl"][m]; N = int(mask.sum())
    o = {"label": label, "n": N, "n_conv": int(m.sum()), "assets": int(len(np.unique(T["asset"][mask]))) if N else 0}
    if m.sum() == 0: return o
    o["gross"] = float(np.nanmean(p)); o["maker"] = o["gross"] - MK; o["taker_exit"] = o["gross"] - TK; o["gross_med"] = float(np.nanmedian(p))
    o["d0"] = float(np.nanmean(T["d0"][m])); o["spr_mean"] = float(np.nanmean(T["spr"][m])); o["spr_med"] = float(np.nanmedian(T["spr"][m]))
    o["pnl_minus_d0"] = o["gross"] - o["d0"]; o["sum_maker"] = float(np.nansum(p - MK))
    if full:
        mu, lo, hi = ci(p - MK, T["asset"][m]); o["maker_ci"] = [lo, hi]
        mu, lo, hi = ci(p - TK, T["asset"][m]); o["taker_ci"] = [lo, hi]
    return o
allm = np.ones(n, bool)
M = {"все": allm, "(а) без первых 10 с часа": ~h10, "(б) без спред ≥100": ~sp100, "(в) без обоих": ~h10 & ~sp100}
out = {"n": n, "share_h10": float(h10.mean()), "share_sp100": float(sp100.mean()), "share_spnan": float(sp_nan.mean()), "share_both": float((h10 & sp100).mean()), "summary": [], "removed": [], "xp": {}, "cls": {}, "top30": []}
for k, m in M.items(): out["summary"].append(row(m, k, True))
out["removed"] = [row(h10, "только первые 10 с"), row(sp100, "только спред ≥100"), row(h10 & sp100, "оба признака"), row(h10 & ~sp100, "первые 10 с, спред <100"), row(~h10 & sp100, "не первые 10 с, спред ≥100")]
for g, arr, key in [("xp", xp, "xp"), ("cls", cl, "cls")]:
    for v in sorted(set(arr.tolist())):
        s = arr == v
        out[key][v] = {k: row(m & s, k) for k, m in M.items()}
        out[key][v]["share_h10"] = float(h10[s].mean()); out[key][v]["share_sp100"] = float(sp100[s].mean())
# классы × биржи пар для (в)
# топ-30 пар по сумме мейкер-net по всем эпизодам (пара = актив × пара бирж)
s_all = {}
for p in set(T["pair"].tolist()):
    s = (T["pair"] == p) & conv; s_all[p] = float(np.nansum(T["pnl"][s] - MK))
top = sorted(s_all, key=lambda p: -s_all[p])[:30]; tot = sum(s_all.values())
for p in top:
    s = T["pair"] == p; r = {"pair": p, "sym": pk[p][3], "xp": "+".join(sorted(pk[p][:2])), "cls": pk[p][2], "sum_all": s_all[p], "share_total": s_all[p]/tot}
    for k, m in M.items(): r[k] = row(m & s, k)
    out["top30"].append(r)
# концентрация: доля суммы мейкер-net топ-10/30 пар
v = np.array(sorted(s_all.values(), reverse=True)); out["conc_top10"] = float(v[:10].sum()/tot); out["conc_top30"] = float(v[:30].sum()/tot)
vm = {}
for p in set(T["pair"].tolist()):
    s = (T["pair"] == p) & conv & ~h10 & ~sp100; vm[p] = float(np.nansum(T["pnl"][s] - MK))
v2 = np.array(sorted(vm.values(), reverse=True)); out["conc_top10_masked"] = float(v2[:10].sum()/v2.sum()); out["total_sum_all"] = tot; out["total_sum_masked"] = float(v2.sum())
# спред ≥100 по биржам-ногам не знаем; доля в окне часа: 10 с, 30 с
for w in (5, 10, 30, 60):
    out[f"share_first{w}s"] = float(((T["t0"] % 3600000) < w*1000).mean())
json.dump(out, open(f"{OUT}/nv3_out.json", "w"), ensure_ascii=False, indent=1, default=float)
print("DONE", flush=True)
