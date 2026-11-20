"""D1 наивный (В-125, описание, без валидации): вошли на расхождении (мейкерская сторона лучших цен), вышли на схождении (закрывающая сторона лучших цен), без очереди и жизни заявки.
pnl_end_bps = снятый разрыв без комиссий (ядро main.rs: Ep.step, pnl по ea/eb на входе, aa/ab/ba/bb на выходе). Запуск: nv.py <prelim> <outdir>"""
import csv, sys, json, collections
import numpy as np
P, OUT = sys.argv[1], sys.argv[2]; SPECS = sys.argv[3] if len(sys.argv) > 3 else "specs.txt"; SUF = sys.argv[4] if len(sys.argv) > 4 else ""
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
specs = [l.strip() for l in open(f"{P}/{SPECS}") if l.strip()]
cols = collections.defaultdict(list)
for sp in specs:
    a, d, p, b, base = sp.split(":", 4)
    day = base.split("/")[-2]
    base += SUF; R = list(csv.DictReader(open(base))); Q = list(csv.DictReader(open(base + ".path.csv")))
    assert len(R) == len(Q)
    for r, q in zip(R, Q):
        assert r["theta"] == q["theta"] and r["t0_ms"] == q["t0_ms"]
        cols["asset"].append(int(a)); cols["pair"].append(int(p)); cols["day"].append(day)
        cols["theta"].append(float(r["theta"])); cols["t0"].append(int(r["t0_ms"])); cols["d0"].append(num(r["d0_bps"])); cols["peak"].append(num(r["peak_bps"]))
        cols["tconv"].append(num(r["t_conv_ms"])); cols["cens"].append(int(r["censored"]))
        cols["pnl"].append(num(q["pnl_end_bps"])); cols["best"].append(num(q["best_bps"])); cols["mpu"].append(num(q["mpu_bps"]))
T = {k: np.array(v) for k, v in cols.items()}
n = len(T["asset"]); print("эпизодов (все θ)", n, flush=True)
pk = {p: (pairs[p]["exA"], pairs[p]["exB"], klass(pairs[p])) for p in set(T["pair"].tolist())}
T["day"] = np.datetime_as_string(T["t0"].astype("datetime64[ms]"), unit="D")
T["xp"] = np.array(["+".join(sorted(pk[p][:2])) for p in T["pair"]]); T["cls"] = np.array([pk[p][2] for p in T["pair"]])
# фандинг из d1.npz (θ=12.6, тот же порядок)
fund = np.full(n, np.nan)
if not SUF and SPECS == "specs.txt":
    z = np.load(f"{P}/d1.npz"); m126 = np.isclose(T["theta"], 12.6)
    assert m126.sum() == len(z["asset"]) and (T["asset"][m126] == z["asset"]).all() and (T["pair"][m126] == z["pair"]).all()
    fund[m126] = z["fund_bps"]
T["fund"] = fund
conv = T["cens"] == 0
T["conv"] = conv
FEE = {"gross": 0.0, "maker": 5.04, "taker": 12.6}
def ci(vals, assets, B=300):
    ok = np.isfinite(vals); v = vals[ok]; a = assets[ok]
    if len(v) == 0: return (np.nan,) * 3
    ua, inv = np.unique(a, return_inverse=True)
    s = np.bincount(inv, weights=v, minlength=len(ua)); c = np.bincount(inv, minlength=len(ua)).astype(float)
    mean = s.sum() / c.sum(); bs = []
    for _ in range(B):
        k = rng.integers(0, len(ua), len(ua)); bs.append(s[k].sum() / c[k].sum())
    return mean, np.percentile(bs, 2.5), np.percentile(bs, 97.5)
def row(mask, label, full=True):
    t = {k: v[mask] for k, v in T.items()}
    N = int(mask.sum()); cv = t["conv"]; nc = int(cv.sum())
    o = {"label": label, "n": N, "n_assets": int(len(np.unique(t["asset"]))), "share_conv": nc / N if N else np.nan,
         "share_conv_600s": float(((t["tconv"] >= 0) & (t["tconv"] <= 600000)).sum() / N) if N else np.nan,
         "d0_med": float(np.median(t["d0"])) if N else np.nan, "peak_med": float(np.median(t["peak"])) if N else np.nan,
         "peak_p90": float(np.percentile(t["peak"], 90)) if N else np.nan,
         "tconv_med_s": float(np.median(t["tconv"][cv]) / 1000) if nc else np.nan, "tconv_p90_s": float(np.percentile(t["tconv"][cv], 90) / 1000) if nc else np.nan}
    pnl = t["pnl"][cv]; o["pnl_med"] = float(np.nanmedian(pnl)) if nc else np.nan
    o["share_pnl_pos_gross"] = float(np.mean(pnl > 0)) if nc else np.nan
    # потолок «вход на пике»: pnl + (peak − d0), знание будущего, приближённо (по d, не по ценам)
    ceil = pnl + (t["peak"][cv] - t["d0"][cv])
    for nm, f in FEE.items():
        if full:
            mu, lo, hi = ci(pnl - f, t["asset"][cv]); o[f"net_{nm}"] = mu; o[f"net_{nm}_lo"] = lo; o[f"net_{nm}_hi"] = hi
        else:
            o[f"net_{nm}"] = float(np.nanmean(pnl - f)) if nc else np.nan
        o[f"ceil_{nm}"] = float(np.nanmean(ceil - f)) if nc else np.nan
        o[f"share_pos_{nm}"] = float(np.mean(pnl - f > 0)) if nc else np.nan
    o["fund_mean"] = float(np.nanmean(t["fund"][cv])) if nc and np.isfinite(t["fund"][cv]).any() else np.nan
    # сошедшиеся не позже H=600 с: отдельно
    h = cv & (t["tconv"] <= 600000)
    o["n_conv600"] = int(h.sum()); o["net_maker_conv600"] = float(np.nanmean(t["pnl"][h] - 5.04)) if h.sum() else np.nan
    late = cv & (t["tconv"] > 600000); o["n_conv_late"] = int(late.sum()); o["net_maker_conv_late"] = float(np.nanmean(t["pnl"][late] - 5.04)) if late.sum() else np.nan
    o["n_cens"] = N - nc
    o["mpu_med"] = float(np.nanmedian(t["mpu"][cv])) if nc else np.nan
    return o
import os; os.makedirs(OUT, exist_ok=True)
res = {"theta": [], "xp": [], "cls": [], "day": [], "pairs": []}
for th in sorted(set(T["theta"].tolist())):
    res["theta"].append(row(np.isclose(T["theta"], th), f"theta={th}"))
base = np.isclose(T["theta"], 12.6)
for k in ["xp", "cls"]:
    for v in sorted(set(T[k][base].tolist())): res[k].append(row(base & (T[k] == v), f"{k}={v}"))
for v in sorted(set(T["day"][base].tolist())): res["day"].append(row(base & (T["day"] == v), f"day={v}", full=False))
# по парам (θ=12.6), n ≥ 30
pr = []
for p in sorted(set(T["pair"][base].tolist())):
    mk = base & (T["pair"] == p)
    if mk.sum() < 30: continue
    r = row(mk, f"pair={p}", full=False); r["pair"] = p; r["exA"], r["exB"], r["cls"] = pk[p]; r["symA"] = pairs[p]["symA"]; r["symB"] = pairs[p]["symB"]; pr.append(r)
res["pairs"] = pr
json.dump(res, open(f"{OUT}/naive.json", "w"), ensure_ascii=False, default=float)
def wr(name, rows):
    if not rows: return
    ks = list(rows[0].keys())
    with open(f"{OUT}/{name}.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=ks); w.writeheader(); [w.writerow(r) for r in rows]
for k in ["theta", "xp", "cls", "day", "pairs"]: wr(k, res[k])
print("готово", len(pr), "пар с n>=30", flush=True)
