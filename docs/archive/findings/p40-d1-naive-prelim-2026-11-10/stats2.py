"""D1 предв.: парные разности по ячейке актив×сутки (П-40 §4.5): ȳ_вариант − ȳ_base в одних ячейках; интервал 90 % — бутстрэп по активам.
Запуск на srv: stats2.py <каталог prelim> -> diffs.json, diffs.csv"""
import sys, csv, json, collections
import numpy as np
P = sys.argv[1]; TH = "12.6"; rng = np.random.default_rng(7); D0 = 1789603200  # 2026-09-17 00:00 UTC
def cells(asset, day, net):
    d = collections.defaultdict(lambda: [0.0, 0])
    for a, dy, x in zip(asset.tolist(), day.tolist(), net.tolist()):
        if x == x: c = d[(a, dy)]; c[0] += x; c[1] += 1
    return {k: (s / n, n) for k, (s, n) in d.items()}
def num(x):
    try: return float(x)
    except: return float("nan")
C = {}
for nm, v in [("base", ""), ("drain", "_drain"), ("k1p60", "_k1p60")]:
    z = np.load(f"{P}/d1{v}.npz"); C[nm] = cells(z["asset"], z["day"], z["net_bps"])
# K2: net = 0 для none, иначе pnl (без фандинга), как в stats.py
ex = collections.defaultdict(lambda: [0.0, 0])
for sp in [l.strip() for l in open(f"{P}/specs_k2.txt") if l.strip()]:
    a, d, p, b, base = sp.split(":", 4); base += ".k2"
    for r in csv.DictReader(open(base + ".exits.csv")):
        if r["theta"] != TH: continue
        x = 0.0 if r["outcome"] == "none" else num(r["pnl_bps"])
        dy = int((int(r["t0_ms"]) / 1000 - D0) // 86400)
        if x == x: c = ex[(int(a), dy)]; c[0] += x; c[1] += 1
C["k2ctl"] = {k: (s / n, n) for k, (s, n) in ex.items()}
out = {}; rows = []
for nm in ["drain", "k1p60", "k2ctl"]:
    keys = sorted(set(C[nm]) & set(C["base"]))
    dv = np.array([C[nm][k][0] - C["base"][k][0] for k in keys]); as_ = np.array([k[0] for k in keys])
    wb = np.array([C["base"][k][1] for k in keys], float)
    ua, inv = np.unique(as_, return_inverse=True)
    def agg(idx_assets):
        s = np.bincount(inv, weights=dv, minlength=len(ua)); c = np.bincount(inv, minlength=len(ua)).astype(float)
        return s, c
    s, c = agg(None); mean = s.sum() / c.sum(); bs = []
    for _ in range(2000):
        i = rng.integers(0, len(ua), len(ua)); bs.append(s[i].sum() / c[i].sum())
    lo, hi = np.percentile(bs, [5, 95])
    mw = float((dv * wb).sum() / wb.sum())
    # то же с весом по эпизодам base
    sw = np.bincount(inv, weights=dv * wb, minlength=len(ua)); cw = np.bincount(inv, weights=wb, minlength=len(ua)); bw = []
    for _ in range(2000):
        i = rng.integers(0, len(ua), len(ua)); bw.append(sw[i].sum() / cw[i].sum())
    lw, hw = np.percentile(bw, [5, 95])
    r = {"cells_paired": len(keys), "cells_base": len(C["base"]), "cells_var": len(C[nm]), "assets": len(ua),
         "diff_cellmean": float(mean), "lo90": float(lo), "hi90": float(hi), "diff_epweighted": mw, "lo90_w": float(lw), "hi90_w": float(hw),
         "base_mean_same_cells": float(np.mean([C["base"][k][0] for k in keys])), "var_mean_same_cells": float(np.mean([C[nm][k][0] for k in keys]))}
    out[nm] = r; rows.append([nm] + [f"{r[k]:.4g}" if isinstance(r[k], float) else r[k] for k in r])
json.dump(out, open(f"{P}/diffs.json", "w"), ensure_ascii=False, indent=1)
with open(f"{P}/diffs.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["variant"] + list(next(iter(out.values())))); w.writerows(rows)
print(open(f"{P}/diffs.csv").read())
