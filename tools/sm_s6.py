"""TK-459 слой S, S6: метки цепочек канала (TK-454, исход «цель1 первой», N=71 решённых) × сессия входа / D1-фрактал / премиум-дискаунт.
Протокол docs/research/P-TK459-synthesis-S-2026-10-30.md §2–3, §9; решение Судьи docs/research/reviews/TK-459-S3-S5-S6-2026-10-30.md (S6).
Метки считаются по закрытым барам Binance UM до минуты входа:
  сессия — meta.session (как в TK-454), 4 строки (Азия/Франкфурт/Лондон/Нью-Йорк) против остальных (вкл. «вне окон»);
  D1-фрактал — последний подтверждённый (k=1, 3-св.) D1-фрактал: тип совпадает со стороной цели (лонг — хай, шорт — лой) = 1;
  премиум/дискаунт — последняя нога k=2 на 1H до входа, p=(вход−лой)/(хай−лой); лонг в дискаунте (p<0,5) или шорт в премиуме (p>0,5) = 1.
Контроли: C-внутри (доля цель1 при метке − без метки) и C-нуль (то же по y − 1/(1+RR), мартингал TK-454). Бутстреп по цепочкам B=2000 seed 459.
python -I tools/sm_s6.py <findings-dir> <каталог 1m csv.gz> <выход-префикс>"""
import sys, os, csv, importlib.util
import numpy as np, pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
for p in (HERE, "/data/tk0451", os.path.join(HERE, "compute")):
    if os.path.isdir(p): sys.path.insert(0, p)
from sancho import bars, swings  # noqa: E402

F, SRC, OUT = sys.argv[1].rstrip("/") + "/", sys.argv[2].rstrip("/"), sys.argv[3]
S = "-2026-10-30.csv"; B = 2000; rng = np.random.default_rng(459); M = 12   # Холм: 6 строк × 2 контроля
rd = lambda n: list(csv.DictReader(open(F + n + S, encoding="utf-8")))
sys.argv = [sys.argv[0], sys.argv[1].rstrip("/")]
ns = {}; exec(compile(open(os.path.join(HERE, "sancho_channel_report.py"), encoding="utf-8").read().split("seen = {}")[0], "rep", "exec"), ns); cat = ns["cat"]
p1 = rd("sancho-trades-p1-all"); meta = {m["trade_id"]: m for m in rd("sancho-trades-meta")}
seen = {}
for r in p1: seen.setdefault(r["trade_id"], r)
lv = {}
for r in rd("sancho-trades"):
    try: lv.setdefault(r["trade_id"], (float(r["entry"]), float(r["stop"]), float(r["tp1"])))
    except ValueError: pass
for r in rd("sancho-trades-handtv"):
    try: lv.setdefault(r["trade_id"], (float(r["entry"]), float(r["stop"]), float(r["tp"])))
    except ValueError: pass
dec = [r for r in seen.values() if cat(r) in ("первым цель1", "первым стоп") and r["trade_id"] in lv]
print("решённых с RR:", len(dec), "цель1:", sum(cat(r) == "первым цель1" for r in dec))   # сверка: 71 / 48
assert len(dec) == 71 and sum(cat(r) == "первым цель1" for r in dec) == 48, "расхождение с TK-454 — стоп"


def path(sym):
    for q in (f"{SRC}/{sym}.csv.gz", f"{SRC}/1000{sym}.csv.gz"):
        if os.path.exists(q): return q


def frac(h, l, ok):
    out = []
    for i in range(1, len(h) - 1):
        if not (ok[i - 1] and ok[i] and ok[i + 1]): continue
        hi = h[i] > h[i - 1] and h[i] > h[i + 1]; lo = l[i] < l[i - 1] and l[i] < l[i + 1]
        if hi != lo: out.append((i + 1, 1 if hi else -1))          # подтверждение — закрытие бара i+1
    return out


cache = {}
rows = []
for r in dec:
    i = r["trade_id"]; sym = meta[i]["symbol"]; e0, st, tp = lv[i]; lng = r["dir"] == "long"
    tm = int(r["entry_min"].split("|")[0]); y = int(cat(r) == "первым цель1"); rr = abs(tp - e0) / abs(e0 - st)
    d1 = pr = np.nan
    pth = path(sym)
    if pth:
        if sym not in cache: cache[sym] = (bars(pth, 1440), bars(pth, 60))
        D, H1 = cache[sym]
        # D1: бар b подтверждён закрытием бара b+1; используем, если закрытие бара b+1 <= минуты входа
        h, l, ok = D.h.values.astype(float), D.l.values.astype(float), D.ok.values; b0 = D.index.min()
        fr = [(c, t) for c, t in frac(h, l, ok) if (b0 + c + 1) * 86400000 <= tm]
        if fr: d1 = int(fr[-1][1] == (1 if lng else -1))
        h, l, ok = H1.h.values.astype(float), H1.l.values.astype(float), H1.ok.values; a0 = H1.index.min()
        lim = tm // 3600000 - a0                                   # бары < lim закрыты к моменту входа
        sh, sl = swings(h, ok, 2), swings(-l, ok, 2); sh = sh[sh + 2 < lim]; sl = sl[sl + 2 < lim]
        if len(sh) and len(sl):
            a, b = sh[-1], sl[-1]; z = (sl[sl < a] if a > b else sh[sh < b])
            if len(z):
                hi, lo = (h[a], l[z[-1]]) if a > b else (h[z[-1]], l[b])
                if hi > lo: pr = (e0 - lo) / (hi - lo)
    s = meta[i]["session"]
    rows.append(dict(id=i, sym=sym, year=int(meta[i]["year"]), dir=r["dir"], y=y, p0=1 / (1 + rr), early=meta[i]["history_since_2020"] == "да",
                     **{"сессия " + k: int(s == k) for k in ("Азия", "Франкфурт", "Лондон", "Нью-Йорк")},
                     d1=d1, prem=(np.nan if np.isnan(pr) else int((pr < .5) if lng else (pr > .5))), p_pos=pr))
D = pd.DataFrame(rows); D.to_csv(OUT + "-chains.csv", index=False)
FL = ["сессия Азия", "сессия Франкфурт", "сессия Лондон", "сессия Нью-Йорк", "d1", "prem"]
NM = {"d1": "D1-фрактал по стороне цели", "prem": "вход в дискаунте (лонг) / премиуме (шорт)"}


def boot(v1, v0):
    if len(v1) < 8 or len(v0) < 8: return (np.nan,) * 4
    a = rng.integers(0, len(v1), (B, len(v1))); b = rng.integers(0, len(v0), (B, len(v0)))
    bs = v1[a].mean(1) - v0[b].mean(1)
    return v1.mean() - v0.mean(), np.percentile(bs, 2.5), np.percentile(bs, 97.5), 2 * min((bs <= 0).mean(), (bs >= 0).mean()) + 1 / B


def deltas(g, f):
    g = g[g[f].notna()]; a, b = g[g[f] == 1], g[g[f] == 0]
    return {"внутри": (a.y.values.astype(float), b.y.values.astype(float)), "к нулю": ((a.y - a.p0).values, (b.y - b.p0).values)}


tot, yrs, funnel, subs = [], [], [], []
for f in FL:
    nm = NM.get(f, f)
    funnel.append((nm, int(D[f].notna().sum()), int((D[f] == 1).sum()), int((D[f] == 0).sum())))
    for cn, (v1, v0) in deltas(D, f).items(): tot.append((nm, cn, len(v1), len(v0)) + boot(v1, v0))
    for y, g in D.groupby("year"):
        for cn, (v1, v0) in deltas(g, f).items():
            yrs.append((nm, cn, y, len(g), len(v1), len(v0)) + boot(v1, v0)[:3])
    for sn, g in [("история с 2020", D[D.early]), ("листинг позже/нет данных", D[~D.early]), ("BTC+ETH", D[D.sym.isin(["BTCUSDT", "ETHUSDT"])]), ("прочие монеты", D[~D.sym.isin(["BTCUSDT", "ETHUSDT"])])]:
        for cn, (v1, v0) in deltas(g, f).items(): subs.append((nm, cn, sn, len(v1), len(v0)) + boot(v1, v0)[:1])
T = pd.DataFrame(tot, columns=["строка", "контроль", "n1", "n0", "dlt", "lo", "hi", "p"])
T["pg"] = np.where(T.dlt > 0, T.p / 2, 1 - T.p / 2); T["pl"] = np.where(T.dlt < 0, T.p / 2, 1 - T.p / 2)
for col, nm in (("pg", "holm_gt0"), ("pl", "holm_lt0")):
    o = T[col].fillna(1).sort_values(); run = 0; adj = {}
    for k, (i, p) in enumerate(o.items()): run = max(run, min(1, p * (M - k))); adj[i] = run
    T[nm] = T.index.map(adj)
Y = pd.DataFrame(yrs, columns=["строка", "контроль", "год", "N год", "n1", "n0", "dlt", "lo", "hi"])
Y["данные"] = Y["N год"] >= 8; Y["знак"] = np.sign(Y.dlt)
ver = {}
for r, g in T.groupby("строка"):
    up = dn = True
    for _, x in g.iterrows():
        z = Y[(Y.строка == r) & (Y.контроль == x.контроль) & Y.данные & Y.dlt.notna()]; same = len(z) > 0 and z.знак.nunique() == 1
        up &= bool(x.holm_gt0 < .05 and same and z.знак.iloc[0] > 0); dn &= bool(x.holm_lt0 < .05 and same and z.знак.iloc[0] < 0)
    ver[r] = "подтверждено" if up else "оспорено" if dn else "не различимо"
T["вердикт"] = T.строка.map(ver)
pd.DataFrame(funnel, columns=["строка", "цепочек с меткой", "метка=1", "метка=0"]).to_csv(OUT + "-funnel.csv", index=False)
T.to_csv(OUT + "-total.csv", index=False); Y.to_csv(OUT + "-years.csv", index=False)
pd.DataFrame(subs, columns=["строка", "контроль", "срез", "n1", "n0", "dlt"]).to_csv(OUT + "-slices.csv", index=False)
print(T.round(3).to_string()); print(pd.DataFrame(funnel).to_string()); print(Y.round(3).to_string())
