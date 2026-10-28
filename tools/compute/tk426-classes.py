#!/usr/bin/env python3
"""TK-426 шаг 3 на событиях v11 (P-TK426 §12 v3 + правки Судьи 28.10): кластеры -> классы на 1-15.09 -> устойчивость -> «общее» на 16-30.09.
Использование: tk426-classes.py <events-09.csv> <events-08.csv> <features-09.csv> <features-08.csv> <xq.json> <свечи-09> <свечи-08> <выход-префикс>"""
import sys, json, os, hashlib
import numpy as np, pandas as pd

EV9, EV8, F9, F8, XQ, C9, C8, OUT = sys.argv[1:9]
MIN = 60000; DAY = 86400000
SEP1 = 1788220800000; HALF = SEP1 + 15 * DAY
rng = np.random.default_rng(426)
xq = json.load(open(XQ))
GATE_BAD = (SEP1 + 29 * DAY + 3 * 3600000 + 55 * MIN, SEP1 + 29 * DAY + 4 * 3600000 + 5 * MIN)  # 30.09 07:55-08:05 GMT+4
cache = {}


def md(df):
    c = list(df.columns)
    return "\n".join(["| " + " | ".join(map(str, c)) + " |", "|" + "---|" * len(c)] +
                     ["| " + " | ".join(str(round(x, 3)) if isinstance(x, float) else str(x) for x in r) + " |" for r in df.itertuples(index=False)])


def candles(root, bourse, mkt, ins):
    key = (root, bourse, mkt, ins)
    if key in cache: return cache[key]
    p = f"{root}/{bourse}-{mkt}/{ins}.csv.gz"
    r = None
    if os.path.exists(p):
        d = pd.read_csv(p, usecols=["t_open_ms", "o", "c", "quote_v"])
        r = (d.t_open_ms.to_numpy(np.int64) // MIN, d.o.to_numpy(float), d.c.to_numpy(float), d.quote_v.to_numpy(float))
    cache[key] = r
    if len(cache) > 300: cache.pop(next(iter(cache)))
    return r


def shape(biz, mkt, ins, sgn, t0, t1, root):
    out = dict(ход=np.nan, откат=np.nan, середина=np.nan, доля1м=np.nan, vol_pre=np.nan, оборот24=np.nan)
    c = candles(root, biz, mkt, ins)
    if c is None: return out
    t, o, cl, q = c
    a, b = int(t0 // MIN), int(t1 // MIN)
    i0, i1 = np.searchsorted(t, a), np.searchsorted(t, b, side="right")
    if i1 - i0 < 10 or t[i0] != a: return out
    leg = cl[i0:i1]; p0 = o[i0]
    lr = sgn * np.log(leg / p0); mv = lr[-1]
    out["ход"] = abs(mv) * 100
    if mv > 0:
        out["откат"] = float(np.max(np.maximum.accumulate(lr) - lr) / mv)
        out["середина"] = float(np.argmax(lr >= mv / 2) / max(1, len(lr) - 1))
        d1 = np.diff(np.concatenate([[np.log(p0)], np.log(leg)])) * sgn
        out["доля1м"] = float(np.max(np.abs(d1)) / mv)
    k0 = np.searchsorted(t, a - 360)
    pr = np.log(cl[k0:i0])
    if len(pr) >= 180: out["vol_pre"] = float(np.log10(np.std(np.diff(pr)) * 1e4 + 1e-9))
    k1 = np.searchsorted(t, a - 1440)
    qq = q[k1:i0]
    if len(qq) >= 720: out["оборот24"] = float(np.log10(np.median(qq) * 1440 + 1))
    return out


def norm_coin(s):
    return s.str.replace("_", "").str.replace(r"^1000+", "", regex=True)


def load(ev_csv, f_csv):
    ev = pd.read_csv(ev_csv)
    ev["монета"] = norm_coin(ev.инструмент); ev["D"] = ev["D_мин"]; ev["зн"] = ev.знак; ev["bx"] = ev.биржа_рынок
    ev = ev.sort_values(["монета", "зн", "t_start_ms"]).reset_index(drop=True)
    cid = np.zeros(len(ev), np.int64); cur = -1; key = None; end = -1
    for i, (m, s, a, b) in enumerate(zip(ev.монета, ev.зн, ev.t_start_ms, ev.t_peak_ms)):
        if (m, s) != key or a > end: cur += 1; key = (m, s); end = b
        else: end = max(end, b)
        cid[i] = cur
    ev["кластер"] = cid
    f = pd.read_csv(f_csv, low_memory=False)
    f["зн"] = np.where(pd.to_numeric(f.знак, errors="coerce") > 0, "+", "-")
    f["монета"] = norm_coin(f.инструмент); f["bx"] = f.биржа + "-" + f.рынок; f["D"] = f["θ"]
    k = ev.drop_duplicates(["bx", "инструмент", "зн", "D", "t_start_ms"])[["bx", "инструмент", "зн", "D", "t_start_ms", "кластер"]]
    mrg = f.merge(k, left_on=["bx", "инструмент", "зн", "D", "t_start"], right_on=["bx", "инструмент", "зн", "D", "t_start_ms"], how="left")
    return ev, mrg


def add_shape(df, root):
    sh = [shape(b, m, i, 1 if s == "+" else -1, t0, t1, root) for b, m, i, s, t0, t1 in zip(df.биржа, df.рынок, df.инструмент, df.зн, df.t_start, df.t_peak)]
    return pd.concat([df.reset_index(drop=True), pd.DataFrame(sh)], axis=1)


CLS = ["D", "ход/X", "длит", "откат", "середина", "доля1м", "vol_pre", "оборот24", "v_in", "m_in", "m_pre"]
DELTA = ["откат", "середина", "доля1м", "vol_pre", "оборот24", "v_in", "m_in", "m_pre"]  # у контролей ход/длит/D совпадают по построению


def prep(mrg, root):
    e = add_shape(mrg[mrg.роль == "event"], root); c = add_shape(mrg[mrg.роль == "control"], root)
    e["ход/X"] = e.ход / (e.D.astype(int).astype(str) + "|" + e.зн).map(lambda k: xq[k]["q99"])
    for d in (e, c):
        d["длит"] = (d.t_peak - d.t_start) / MIN; d["D"] = d["D"].astype(float)
    bad = (e.биржа == "gate") & (e.t_start <= GATE_BAD[1]) & (e.t_peak >= GATE_BAD[0])
    return e[~bad], c


def repr_(e):
    e = e[e.кластер.notna() & e["ход/X"].notna()].sort_values(["кластер", "ход/X", "t_start"], ascending=[True, False, True])
    return e.drop_duplicates("кластер")


def gower(Xr):
    n, m = Xr.shape; D = np.zeros((n, n)); W = np.zeros((n, n))
    for j in range(m):
        v = Xr[:, j]; ok = ~np.isnan(v)
        both = ok[:, None] & ok[None, :]
        D += np.where(both, np.abs(v[:, None] - v[None, :]), 0); W += both
    return D / np.maximum(W, 1)


class Frozen:
    def __init__(s, X):
        s.cols = list(X.columns); s.ref = [np.sort(X[c].dropna().to_numpy()) for c in s.cols]
    def rank(s, X):
        R = np.full((len(X), len(s.cols)), np.nan)
        for j, c in enumerate(s.cols):
            v = X[c].to_numpy(float); ok = ~np.isnan(v); r = s.ref[j]
            if len(r): R[ok, j] = np.searchsorted(r, v[ok]) / len(r)
        return R


def cluster(Xr, k):
    """k-medoids по матрице Гауэра: старт farthest-first от самой центральной точки, до 30 итераций."""
    Dm = gower(Xr)
    med = [int(np.argmin(Dm.sum(1)))]
    while len(med) < k:
        med.append(int(np.argmax(Dm[:, med].min(1))))
    for _ in range(30):
        lab = np.argmin(Dm[:, med], axis=1)
        new = []
        for g in range(k):
            ix = np.where(lab == g)[0]
            new.append(int(ix[np.argmin(Dm[np.ix_(ix, ix)].sum(1))]) if len(ix) else med[g])
        if new == med: break
        med = new
    lab = np.argmin(Dm[:, med], axis=1)
    return lab, Dm, med


def sil(Dm, lab):
    s = []; gs = set(lab)
    for i in range(len(lab)):
        same = lab == lab[i]
        if same.sum() < 2 or len(gs) < 2: continue
        a = Dm[i, same].sum() / (same.sum() - 1)
        b = min(Dm[i, lab == g].mean() for g in gs if g != lab[i])
        s.append((b - a) / max(a, b, 1e-12))
    return float(np.mean(s)) if s else 0.0


def assign(Xn, Xf, med):
    out = np.zeros(len(Xn), int)
    for i in range(len(Xn)):
        best = 9
        for g, mi in enumerate(med):
            if mi < 0: continue
            ok = ~np.isnan(Xn[i]) & ~np.isnan(Xf[mi])
            d = np.abs(Xn[i, ok] - Xf[mi, ok]).mean() if ok.any() else 9
            if d < best: best, out[i] = d, g
    return out


def ari(a, b):
    _, ia = np.unique(a, return_inverse=True); _, ib = np.unique(b, return_inverse=True)
    M = np.zeros((ia.max() + 1, ib.max() + 1))
    for x, y in zip(ia, ib): M[x, y] += 1
    c2 = lambda x: x * (x - 1) / 2
    s = c2(M).sum(); sa = c2(M.sum(1)).sum(); sb = c2(M.sum(0)).sum(); n2 = c2(M.sum())
    exp = sa * sb / n2 if n2 else 0; mx = (sa + sb) / 2
    return float((s - exp) / (mx - exp)) if mx != exp else 1.0


def bh(p):
    m = len(p); o = np.argsort(p); adj = np.empty(m); run = 1.0
    for r in range(m - 1, -1, -1):
        run = min(run, p[o[r]] * m / (r + 1)); adj[o[r]] = run
    return adj


def main():
    o = open(OUT + ".md", "w", encoding="utf-8")
    def P(*a): print(*a); print(*a, file=o); o.flush()
    ev9, m9 = load(EV9, F9); ev8, m8 = load(EV8, F8)
    e9, c9 = prep(m9, C9); e8, c8 = prep(m8, C8)
    for nm, ev, e in (("сент.", ev9, e9), ("авг.", ev8, e8)):
        sz = ev.groupby("кластер").agg(строк=("D", "size"), рынков=("bx", "nunique"), Dn=("D", "nunique"), a=("t_start_ms", "min"), b=("t_peak_ms", "max"))
        sz["сут"] = (sz.b - sz.a) / DAY
        P(f"## Охват {nm}: кластеров {len(sz)}, строк {len(ev)}; в выборке 433 — {e.кластер.nunique()} кластеров, без признаков — {len(sz) - e.кластер.nunique()}")
        P("размер кластера (строк):", sz.строк.describe(percentiles=[.5, .9, .99]).round(1).to_dict())
        P("рынков:", sz.рынков.value_counts().sort_index().to_dict(), " D:", sz.Dn.value_counts().sort_index().to_dict(),
          " макс. размах, сут:", round(float(sz.сут.max()), 2), "; кластеров >1 сут:", int((sz.сут > 1).sum()))
    r9, r8 = repr_(e9), repr_(e8)
    cols = [c for c in CLS if r9[c].isna().mean() <= 0.2]
    P("\n## Признаки классов (пропусков ≤20 %):", cols, "; вон:", [c for c in CLS if c not in cols])
    F = r9[r9.t_start < HALF].copy(); T = r9[r9.t_start >= HALF].copy()
    fz = Frozen(F[cols]); Xf = fz.rank(F)
    ctl = c9[c9.t_start < HALF].copy(); ctl["ход/X"] = np.nan; Xc = fz.rank(ctl[cols])
    sils = {}
    for k in range(2, 7):
        lab, Dm, _ = cluster(Xf, k); sils[k] = sil(Dm, lab)
    nulls = []
    for _ in range(10):
        ix = rng.choice(len(Xc), min(len(Xf), len(Xc)), replace=False)
        lab, Dm, _ = cluster(Xc[ix], 3); nulls.append(sil(Dm, lab))
    null95 = float(np.percentile(nulls, 95))
    P(f"\n## Классы: репрезентантов 1–15.09 = {len(F)}, 16–30.09 = {len(T)}; авг. {len(r8)}. Силуэт по k: {({k: round(v, 3) for k, v in sils.items()})}; нулевая на C1 (95-й проц., k=3): {null95:.3f}")
    kb = max(sils, key=sils.get)
    k = kb if sils[kb] > null95 else 1
    P(f"Выбрано k = {k}" + (" — структуры сильнее нулевой нет: «один класс»" if k == 1 else ""))
    if k > 1:
        lab, Dm, med = cluster(Xf, k)
        F["класс"] = lab; T["класс"] = assign(fz.rank(T), Xf, med)
    else:
        med = [0]; F["класс"] = 0; T["класс"] = 0
    allr = pd.concat([F, T])
    stab = {}
    if k > 1:
        def stable(fit, new, name):
            if len(fit) < 40 or len(new) < 40: stab[name] = None; return
            fzz = Frozen(fit[cols]); Xa = fzz.rank(fit); la, _, ma = cluster(Xa, k)
            Xb = fzz.rank(new); lb, _, _ = cluster(Xb, k)
            stab[name] = round(ari(assign(Xb, Xa, ma), lb), 3)
        hh = np.array([int(hashlib.md5(x.encode()).hexdigest(), 16) % 2 for x in r9.монета])
        stable(r9[hh == 0], r9[hh == 1], "монеты пополам A→B")
        stable(r9[r9.биржа == "bybit"], r9[r9.биржа == "gate"], "Bybit→Gate")
        stable(r9[r9.D.isin([90, 180])], r9[r9.D.isin([720, 1440])], "D{90,180}→{720,1440}")
        stable(r9, r9[r9["ход/X"] >= 1.5], "порог ×1,5")
        stable(r9, r9[r9["ход/X"] >= 2], "порог ×2")
        stable(F, T, "1–15.09→16–30.09")
        stable(r9, r8, "сент.→авг.")
    P("\n## Устойчивость (ARI: по центроидам другой части против своей кластеризации; порог 0,75 из §8):", json.dumps(stab, ensure_ascii=False))
    cell_tot = ev9.groupby(["D", "зн"]).size()
    smp = m9[m9.роль == "event"].groupby(["D", "зн"]).size()
    p_cell = (smp / cell_tot).to_dict()
    byc = {cl: g.groupby(["D", "зн"]).size() for cl, g in ev9.groupby("кластер")}
    comp = []
    for g in range(k):
        s = allr[allr.класс == g]; w = 0.0
        for cl in s.кластер:
            pi = 1 - np.prod([(1 - p_cell.get(ix, 0)) ** n for ix, n in byc[cl].items()])
            w += 1 / max(pi, 1e-9)
        comp.append(dict(класс=g, кластеров=len(s), доля_как_есть=len(s) / len(allr), вес_1_π=w,
                         **{f"мед_{c}": float(s[c].median()) for c in cols}, дампов=float((s.зн == "-").mean()),
                         bybit=float((s.биржа == "bybit").mean()), gate_spot=float(((s.биржа == "gate") & (s.рынок == "spot")).mean())))
    comp = pd.DataFrame(comp); comp["доля_с_весом"] = comp.вес_1_π / comp.вес_1_π.sum()
    P("\n## Состав классов (весь сентябрь; медианы; доля с весом 1/π)\n" + md(comp))
    if "оборот24" in cols and k > 1:
        allr["терциль"] = pd.qcut(allr.оборот24, 3, labels=["низ", "сред", "верх"])
        P("\n## Класс × терциль оборота 24 ч (кластеров)\n" + pd.crosstab(allr.класс, allr.терциль).to_string())
    P("\n## Класс × знак × D (кластеров)\n" + pd.crosstab(allr.класс, [allr.зн, allr.D]).to_string())
    cgrp = {i: g for i, g in c9.groupby("event_id")}
    rows = []
    for g in range(k):
        for sg in ("+", "-"):
            ss = T[(T.класс == g) & (T.зн == sg)]
            for f in DELTA:
                dl = []
                for r_id, v in zip(ss.event_id, ss[f]):
                    cc = cgrp[r_id][f].dropna() if r_id in cgrp else []
                    if len(cc) >= 3 and not np.isnan(v): dl.append(v - float(np.median(cc)))
                dl = np.array(dl); n = len(dl)
                if n < 5: rows.append(dict(класс=g, знак=sg, признак=f, кластеров=n)); continue
                delta = float((dl > 0).mean() - (dl < 0).mean())
                sgn = rng.choice([-1, 1], size=(5000, n)); perm = np.abs((np.sign(dl)[None, :] * sgn).mean(1))
                p = float((np.sum(perm >= abs(delta) - 1e-12) + 1) / 5001)
                bs = [(lambda x: (x > 0).mean() - (x < 0).mean())(dl[rng.integers(0, n, n)]) for _ in range(500)]
                rows.append(dict(класс=g, знак=sg, признак=f, кластеров=n, delta=delta, lo=float(np.percentile(bs, 2.5)), hi=float(np.percentile(bs, 97.5)), p=p))
    R = pd.DataFrame(rows)
    ok = R.p.notna() if "p" in R else pd.Series(False, index=R.index)
    R["q"] = np.nan
    if ok.any(): R.loc[ok, "q"] = bh(R.loc[ok, "p"].to_numpy())
    R["вердикт"] = np.where(R.q.notna() & (R.q <= 0.10) & (R.lo * R.hi > 0), np.where(R.delta > 0, "выше контроля", "ниже контроля"), "не различимо")
    P(f"\n## «Общее» внутри классов, 16–30.09 (δ знаков против медианы C1; BH q*=0,10 по {int(ok.sum())} ячейкам): различимых {int((R.вердикт != 'не различимо').sum())}")
    P(md(R[R.вердикт != "не различимо"]) if (R.вердикт != "не различимо").any() else "нет")
    P("\nмалых ячеек (<5 кластеров):", int((~ok).sum()))
    allr.to_csv(OUT + "-events.csv", index=False); R.to_csv(OUT + "-common.csv", index=False); comp.to_csv(OUT + "-classes.csv", index=False)


main()
