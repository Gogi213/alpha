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
# --- возврат Судьи 20:15: строки по согласованным ценам и разложение-лайт (без bid/ask на t0/t_end)
z = np.load(f"{P}/d1.npz"); base = np.isclose(T["theta"], 12.6)
spr = np.full(n, -1); dep = np.full(n, -1); spr[base] = z["f_spread"]; dep[base] = z["f_depth"]
FT = 2*1.26 + 2*3.15   # вход мейкером (2 ноги), выход тейкером (2 ноги): 8.82
out = {"exit_taker": [], "decomp": []}
for th in sorted(set(T["theta"].tolist())):
    m = np.isclose(T["theta"], th) & conv
    mu, lo, hi = ci(T["pnl"][m] - FT, T["asset"][m]); out["exit_taker"].append({"theta": th, "n": int(m.sum()), "net_exit_taker_8.82": mu, "lo": lo, "hi": hi, "share_pos": float(np.mean(T["pnl"][m] - FT > 0)), "gross": float(np.mean(T["pnl"][m]))})
def dec(mask, label):
    p = T["pnl"][mask]; d0 = T["d0"][mask]; x = p - d0
    return {"label": label, "n": int(mask.sum()), "pnl_mean": float(p.mean()), "pnl_med": float(np.median(p)), "d0_mean": float(d0.mean()), "d0_med": float(np.median(d0)),
            "pnl_minus_d0_mean": float(x.mean()), "pnl_minus_d0_med": float(np.median(x)), "share_pnl_gt_d0": float(np.mean(x > 0)), "exit_taker_mean": float(np.mean(p - FT)),
            "tconv_med_s": float(np.median(T["tconv"][mask]) / 1000)}
mm = base & conv
out["decomp"].append(dec(mm, "all"))
for k, arr in [("f_spread", spr), ("f_depth", dep)]:
    for v in (0, 1, 2): out["decomp"].append(dec(mm & (arr == v), f"{k}={v}"))
# по d0: терцили
q = np.percentile(T["d0"][mm], [33.3, 66.7])
for i, (a, b) in enumerate([(0, q[0]), (q[0], q[1]), (q[1], 1e9)]): out["decomp"].append(dec(mm & (T["d0"] >= a) & (T["d0"] < b), f"d0_tercile{i} [{a:.1f},{min(b,999):.1f})"))
# быстрые (≤1 с) и медленные
for lab, mk in [("tconv<=1s", T["tconv"] <= 1000), ("1s<tconv<=60s", (T["tconv"] > 1000) & (T["tconv"] <= 60000)), ("tconv>60s", T["tconv"] > 60000)]: out["decomp"].append(dec(mm & mk, lab))
# число эпизодов по θ и по парам: сколько эпизодов с d0 в [θ, 1.1θ)
json.dump(out, open("nv2_out.json", "w"), ensure_ascii=False, indent=1, default=float)
print(json.dumps(out, ensure_ascii=False, indent=1, default=float))
