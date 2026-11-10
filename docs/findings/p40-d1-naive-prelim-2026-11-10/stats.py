"""D1 предварительно (описание, без валидации): сводка по готовым активам out_d1v4. Запуск: stats.py <каталог prelim>"""
import csv, sys, json, collections
import numpy as np
P = sys.argv[1]; TH = "12.6"
rng = np.random.default_rng(1)
pairs = {int(r["pair"]): r for r in csv.DictReader(open("/root/p40core/pairs_d1_v3_nobitget.csv"))}
cls = {}
for r in csv.DictReader(open(f"{P}/universe-assets.csv")):
    for k, v in r.items():
        if k.endswith("_futures") and v: cls[v] = r["class"]
def klass(pr):
    for c in "AB":
        s = pr["sym" + c]
        if s in cls: return cls[s]
    return "?"
V = {"base": "", "drain": "_drain", "k2": "_k2", "k1p60": "_k1p60"}
def num(x):
    try: return float(x)
    except: return float("nan")
D = {}
def load_k2():
    """K2 (случайные моменты, зазор 600 с): <base>.k2.k2*; npz нет (нет aux/age/фандинга) -> колонки собираются здесь; net = pnl без фандинга"""
    specs = [l.strip() for l in open(f"{P}/specs_k2.txt") if l.strip()]
    ex = collections.defaultdict(list)
    for sp in specs:
        a, d, p, b, base = sp.split(":", 4); base += ".k2"
        n0 = len(ex["asset"])
        E = [r for r in csv.DictReader(open(base + ".exits.csv")) if r["theta"] == TH]
        PA = [r for r in csv.DictReader(open(base + ".path.csv")) if r["theta"] == TH]
        M = [r for r in csv.DictReader(open(base)) if r["theta"] == TH]
        F = [r for r in csv.DictReader(open(base + ".fills.csv")) if r["theta"] == TH]
        assert len(E) == len(PA) == len(M) == len(F), base
        for e, pa, m, f in zip(E, PA, M, F):
            pnl = num(e["pnl_bps"]); tc = num(m["t_conv_ms"])
            ex["asset"].append(int(a)); ex["pair"].append(int(p)); ex["outcome"].append(e["outcome"]); ex["kind"].append(e["exit_kind"])
            ex["net_bps"].append(0.0 if e["outcome"] == "none" else pnl); ex["fund_bps"].append(0.0)
            ex["trunc"].append(int(e["trunc"])); ex["mpu"].append(num(pa["mpu_bps"])); ex["best"].append(num(pa["best_bps"]))
            ex["d0"].append(num(m["d0_bps"])); ex["tcms"].append(tc)
            ex["conv_H"].append(int(tc >= 0 and tc <= 600000)); cens = not (tc >= 0 and tc <= 600000)
            ex["cens"].append(int(cens)); ex["t_conv_s"].append(600.0 if cens else tc / 1000)
            ex["fsell"].append(num(f["fill_sell_ms"])); ex["fbuy"].append(num(f["fill_buy_ms"])); ex["sleg"].append(f["sell_leg"]); ex["stale"].append(-1)
    t = {k: np.array(L) for k, L in ex.items()}
    return t
for name, v in V.items():
    z = np.load(f"{P}/d1{v}.npz")
    specs = [l.strip() for l in open(f"{P}/specs{v}.txt") if l.strip()]
    ex = collections.defaultdict(list)
    for sp in specs:
        a, d, p, b, base = sp.split(":", 4)
        for r in csv.DictReader(open(base + ".exits.csv")):
            if r["theta"] == TH: ex["outcome"].append(r["outcome"]); ex["kind"].append(r["exit_kind"]); ex["fees"].append(num(r["fees_bps"]))
        for r in csv.DictReader(open(base + ".path.csv")):
            if r["theta"] == TH: ex["mpu"].append(num(r["mpu_bps"])); ex["best"].append(num(r["best_bps"]))
        for r in csv.DictReader(open(base)):
            if r["theta"] == TH: ex["d0"].append(num(r["d0_bps"])); ex["tcms"].append(num(r["t_conv_ms"]))
        for r in csv.DictReader(open(base + ".fills.csv")):
            if r["theta"] == TH: ex["fsell"].append(num(r["fill_sell_ms"])); ex["fbuy"].append(num(r["fill_buy_ms"])); ex["sleg"].append(r["sell_leg"])
    t = {k: z[k] for k in z.files}
    n = len(t["asset"])
    for k, L in ex.items(): assert len(L) == n, (name, k, len(L), n)
    t.update({k: np.array(L) for k, L in ex.items()})
    t["exA"] = np.array([pairs[int(p)]["exA"] for p in t["pair"]]); t["exB"] = np.array([pairs[int(p)]["exB"] for p in t["pair"]])
    t["xp"] = np.array(["+".join(sorted([a, b])) for a, b in zip(t["exA"], t["exB"])])
    cp = {int(p): klass(pairs[int(p)]) for p in set(t["pair"].tolist())}
    t["cls"] = np.array([cp[int(p)] for p in t["pair"]])
    D[name] = t
def finish(t):
    t["exA"] = np.array([pairs[int(p)]["exA"] for p in t["pair"]]); t["exB"] = np.array([pairs[int(p)]["exB"] for p in t["pair"]])
    t["xp"] = np.array(["+".join(sorted([a, b])) for a, b in zip(t["exA"], t["exB"])])
    cp = {int(p): klass(pairs[int(p)]) for p in set(t["pair"].tolist())}
    t["cls"] = np.array([cp[int(p)] for p in t["pair"]])
    return t
D["k2ctl"] = finish(load_k2())
def ci_cluster(vals, assets, f=np.nanmean):
    """среднее по эпизодам; 95 % интервал — бутстрэп по активам (кластер)"""
    ua, inv = np.unique(assets, return_inverse=True)
    m = np.isfinite(vals)
    s = np.bincount(inv, weights=np.where(m, vals, 0), minlength=len(ua)); c = np.bincount(inv, weights=m, minlength=len(ua))
    if c.sum() == 0: return (np.nan,) * 3
    mean = s.sum() / c.sum(); bs = []
    for _ in range(1000):
        i = rng.integers(0, len(ua), len(ua)); cc = c[i].sum()
        if cc: bs.append(s[i].sum() / cc)
    return mean, np.percentile(bs, 2.5), np.percentile(bs, 97.5)
def pc(x, q): return float(np.nanpercentile(x, q)) if np.isfinite(x).any() else float("nan")
def desc(t, m, name="base"):
    """описательная строка по маске m"""
    n = int(m.sum())
    if n == 0: return None
    g = lambda k: t[k][m]
    o = g("outcome")
    r = {"n_ep": n, "n_pairs": len(set(g("pair").tolist())), "n_assets": len(set(g("asset").tolist()))}
    r["d0_med"] = pc(g("d0"), 50)
    mp = -g("mpu"); r["mpu_adv_med"] = pc(mp, 50); r["mpu_adv_p90"] = pc(mp, 90)    # неблагоприятный ход по пути, bps, >0 = хуже
    r["best_med"] = pc(g("best"), 50)
    r["conv60"] = float(np.mean((g("tcms") >= 0) & (g("tcms") <= 60000)))
    r["conv600"] = float(g("conv_H").mean())
    cv = g("cens") == 0
    r["tconv_med_s"] = pc(g("t_conv_s")[cv], 50); r["tconv_p90_s"] = pc(g("t_conv_s")[cv], 90)
    r["both"] = float(np.mean(o == "both")); r["one"] = float(np.mean(o == "one")); r["none"] = float(np.mean(o == "none"))
    fs, fb = g("fsell"), g("fbuy")
    r["fill_sell"] = float(np.mean(fs >= 0)); r["fill_buy"] = float(np.mean(fb >= 0))
    ex = o != "none"
    r["n_exec"] = int(ex.sum())
    net = g("net_bps"); fund = g("fund_bps"); gross = net - fund
    r["trunc"] = int((g("trunc") > 0).sum())
    mu, lo, hi = ci_cluster(net, g("asset")); r["net_itt"] = mu; r["net_itt_lo"] = lo; r["net_itt_hi"] = hi
    r["net_nofund_itt"] = float(np.nanmean(gross)); r["fund_itt"] = float(np.nanmean(fund))
    r["net_exec"] = float(np.nanmean(net[ex])) if ex.any() else float("nan")
    r["net_exec_med"] = pc(net[ex], 50)
    r["fund_exec"] = float(np.nanmean(fund[ex])) if ex.any() else float("nan")
    r["win_exec"] = float(np.nanmean(net[ex] > 0)) if ex.any() else float("nan")
    sl = g("sleg"); exs = np.where(sl == "A", g("exA"), g("exB")); exb = np.where(sl == "A", g("exB"), g("exA"))   # биржа продающей / покупающей ноги
    for e in ["binance", "okx", "bybit", "gate"]:
        n_e = int((exs == e).sum() + (exb == e).sum())
        r["fill_" + e] = float(((fs >= 0) & (exs == e)).sum() + ((fb >= 0) & (exb == e)).sum()) / n_e if n_e else float("nan")
    r["stale1"] = float(np.mean(g("stale") == 1)); 
    return r
out = {}
for name, t in D.items():
    allm = np.ones(len(t["asset"]), bool)
    out[name] = {"all": desc(t, allm)}
    out[name]["nonstale"] = desc(t, t["stale"] == 0)
    byb = {"нет ноги Bybit/Gate": ~np.isin(t["exA"], ["bybit", "gate"]) & ~np.isin(t["exB"], ["bybit", "gate"]), "есть нога Bybit/Gate": np.isin(t["exA"], ["bybit", "gate"]) | np.isin(t["exB"], ["bybit", "gate"])}
    for k, m in byb.items(): out[name]["leg:" + k] = desc(t, m)
    for x in sorted(set(t["xp"])): out[name]["xp:" + x] = desc(t, t["xp"] == x)
    for c in sorted(set(t["cls"])): out[name]["cls:" + c] = desc(t, t["cls"] == c)
    # контроль/вариант: тот же набор эпизодов нельзя сопоставить построчно (k1p60 меньше) — сводка по маске all
json.dump(out, open(f"{P}/prelim_numbers.json", "w"), ensure_ascii=False, indent=1)
# csv
cols = ["n_ep", "n_pairs", "n_assets", "d0_med", "mpu_adv_med", "mpu_adv_p90", "best_med", "conv60", "conv600", "tconv_med_s", "tconv_p90_s", "both", "one", "none", "fill_sell", "fill_buy", "fill_binance", "fill_okx", "fill_bybit", "fill_gate", "n_exec", "trunc", "net_itt", "net_itt_lo", "net_itt_hi", "net_nofund_itt", "fund_itt", "net_exec", "net_exec_med", "fund_exec", "win_exec", "stale1"]
with open(f"{P}/prelim_table.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["variant", "slice"] + cols)
    for name, d in out.items():
        for sl, r in d.items():
            if r: w.writerow([name, sl] + [f"{r[c]:.4g}" if isinstance(r[c], float) else r[c] for c in cols])
print("ok", {k: len(D[k]["asset"]) for k in D})
