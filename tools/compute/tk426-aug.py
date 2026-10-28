#!/usr/bin/env python3
"""TK-426 шаг 3, возврат Судьи 28.10 (return2): «общее» на августе на ЗАМОРОЖЕННЫХ классах/порогах сентября + кросс-таблица форм каталога TK-414.
Использует функции tk426-classes.py (без его main). Классы: k=2, центроиды (медоиды) по репрезентантам 1-15.09, ранги по референсу 1-15.09.
Использование: как tk426-classes.py (8 аргументов; выход-префикс — для -aug-*)."""
import sys, os
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "tk426-classes.py"), encoding="utf-8").read()
src = src.rsplit("\nmain()", 1)[0]
exec(compile(src, "tk426-classes.py", "exec"))
K = 2


def delta_table(TT, cgrp, strat):
    rows = []
    for g in range(K):
        for sg in ("+", "-"):
            ss = TT[(TT.класс == g) & (TT.зн == sg)]
            for f in DELTA:
                dl = []
                for r_id, v in zip(ss.event_id, ss[f]):
                    cc = cgrp[r_id][f].dropna() if r_id in cgrp else []
                    if len(cc) >= 3 and not np.isnan(v): dl.append(v - float(np.median(cc)))
                dl = np.array(dl); n = len(dl)
                base = dict(срез=strat, класс=g, знак=sg, признак=f, кластеров=n)
                if n < 5: rows.append(base); continue
                delta = float((dl > 0).mean() - (dl < 0).mean())
                sgn = rng.choice([-1, 1], size=(5000, n)); perm = np.abs((np.sign(dl)[None, :] * sgn).mean(1))
                p = float((np.sum(perm >= abs(delta) - 1e-12) + 1) / 5001)
                bs = [(lambda x: (x > 0).mean() - (x < 0).mean())(dl[rng.integers(0, n, n)]) for _ in range(500)]
                rows.append(dict(base, delta=delta, lo=float(np.percentile(bs, 2.5)), hi=float(np.percentile(bs, 97.5)), p=p))
    R = pd.DataFrame(rows)
    if "p" not in R: R["p"] = np.nan
    ok = R.p.notna(); R["q"] = np.nan
    if ok.any(): R.loc[ok, "q"] = bh(R.loc[ok, "p"].to_numpy())
    return R


def verdict(R):
    R["вердикт"] = np.where(R.q.notna() & (R.q <= 0.10) & (R.lo * R.hi > 0), np.where(R.delta > 0, "выше контроля", "ниже контроля"), "не различимо")
    return R


o = open(OUT + ".md", "w", encoding="utf-8")
def P(*a): print(*a); print(*a, file=o); o.flush()
ev9, m9 = load(EV9, F9); ev8, m8 = load(EV8, F8)
e9, c9 = prep(m9, C9); e8, c8 = prep(m8, C8)
r9, r8 = repr_(e9), repr_(e8)
cols = [c for c in CLS if r9[c].isna().mean() <= 0.2]
F = r9[r9.t_start < HALF].copy(); T = r9[r9.t_start >= HALF].copy()
fz = Frozen(F[cols]); Xf = fz.rank(F)
lab, Dm, med = cluster(Xf, K)
F["класс"] = lab; T["класс"] = assign(fz.rank(T), Xf, med)
r8 = r8.copy(); r8["класс"] = assign(fz.rank(r8[cols]), Xf, med)
P(f"# TK-426 «общее» на августе, замороженные классы сент. (k={K}, признаки {cols}); репрезентантов авг. {len(r8)}, пропуски: " + str({c: round(float(r8[c].isna().mean()), 2) for c in cols}))
P("Класс (авг.) × знак × D:\n" + pd.crosstab(r8.класс, [r8.зн, r8.D]).to_string())
P("Класс (сент., 16–30.09) × знак × D:\n" + pd.crosstab(T.класс, [T.зн, T.D]).to_string())
allr9 = pd.concat([F, T])
edges = allr9.оборот24.quantile([1 / 3, 2 / 3]).to_numpy()   # границы терцилей — сентябрьские, заморожены
def strata_of(TT):
    tt = pd.cut(TT.оборот24, [-np.inf, edges[0], edges[1], np.inf], labels=["низ", "сред", "верх"])
    s = [("всё", TT)] + [(f"оборот24 {nm}", TT[tt == nm]) for nm in ("низ", "сред", "верх")]
    return s + [("Bybit", TT[TT.биржа == "bybit"]), ("Gate", TT[TT.биржа == "gate"])]
cg8 = {i: g for i, g in c8.groupby("event_id")}
R8 = verdict(pd.concat([delta_table(TT, cg8, nm) for nm, TT in strata_of(r8)], ignore_index=True))
R9P = os.environ.get("R9CSV", "docs/findings/pump-classes-2026-10-28-common.csv")
R9 = pd.read_csv(R9P) if os.path.exists(R9P) else None
key = ["срез", "класс", "знак", "признак"]
if R9 is not None:
    J = R8.merge(R9[key + ["delta", "вердикт"]], on=key, how="left", suffixes=("_авг", "_сент"))
    J["знак_совпал"] = np.where(J.delta_авг.notna() & J.delta_сент.notna(), np.sign(J.delta_авг) == np.sign(J.delta_сент), np.nan)
    J["вердикты_совпали"] = J.вердикт_авг == J.вердикт_сент
    both = J[J.delta_авг.notna() & J.delta_сент.notna()]
    P(f"\n## Сверка с сентябрём (ячеек с δ в обоих месяцах {len(both)}): знак совпал {int(both.знак_совпал.sum())}; вердикт совпал {int(both.вердикты_совпали.sum())}; "
      f"различимых авг. {int((R8.вердикт != 'не различимо').sum())} из {int(R8.p.notna().sum())}")
    P(md(J[J.срез == "всё"][key + ["кластеров", "delta_авг", "lo", "hi", "q", "вердикт_авг", "delta_сент", "вердикт_сент", "знак_совпал"]].round(3)))
    P("\n## Остальные срезы, только признаки «общего»\n" + md(J[(J.срез != "всё") & J.признак.isin(["m_pre", "vol_pre", "оборот24", "v_in"])][["срез"] + key[1:] + ["кластеров", "delta_авг", "вердикт_авг", "delta_сент", "вердикт_сент", "знак_совпал"]].round(3)))
    J.to_csv(OUT + "-common.csv", index=False)
else:
    R8.to_csv(OUT + "-common.csv", index=False)
# кросс-таблица с формами каталога TK-414: признаки формы (откат, доля1м, середина, длит, D) по классам, оба месяца
allm = pd.concat([allr9.assign(мес="сент."), r8.assign(мес="авг.")])
shp = allm.groupby(["мес", "класс"]).agg(кластеров=("D", "size"), D_мед=("D", "median"), длит_мед_мин=("длит", "median"), откат_мед=("откат", "median"),
    доля1м_мед=("доля1м", "median"), середина_мед=("середина", "median"), оборот24_мед=("оборот24", "median"), vol_pre_мед=("vol_pre", "median"),
    gate_spot=("биржа", lambda s: 0.0)).drop(columns="gate_spot")
shp["gate_spot"] = allm.groupby(["мес", "класс"]).apply(lambda g: float(((g.биржа == "gate") & (g.рынок == "spot")).mean()))
shp["быстрых_≤5м_доля1м≥0,5"] = allm.groupby(["мес", "класс"]).apply(lambda g: float(((g.доля1м >= 0.5)).mean()))
shp["откат≥0,5"] = allm.groupby(["мес", "класс"]).apply(lambda g: float((g.откат >= 0.5).mean()))
shp["откат<0,2"] = allm.groupby(["мес", "класс"]).apply(lambda g: float((g.откат < 0.2).mean()))
P("\n## Формы по классам (для кросс-таблицы с каталогом TK-414)\n" + md(shp.reset_index().round(3)))
shp.reset_index().to_csv(OUT + "-shapes.csv", index=False)
# причина пропусков v_pre (признак TK-433, в классы не входит): число пар соседних минут в окне 60 мин < 5
v = allm.copy(); v["gs"] = (v.биржа == "gate") & (v.рынок == "spot")
P("\n## v_pre: доля пустых по бирже×рынку×месяцу\n" + md(v.groupby(["мес", "биржа", "рынок"]).v_pre.apply(lambda s: float(s.isna().mean())).reset_index().round(3)))
P("v_pre Gate spot по терцилям оборота24:\n" + md(v[v.gs].assign(т=lambda d: pd.cut(d.оборот24, [-np.inf, edges[0], edges[1], np.inf], labels=["низ", "сред", "верх"])).groupby(["мес", "т"], observed=True).v_pre.apply(lambda s: float(s.isna().mean())).reset_index().round(3)))
