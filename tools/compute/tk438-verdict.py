"""TK-438: Холм по сент. (семейство K=360 рамки), подтверждение авг. тем же знаком p<0.05; вердикт на сценарий."""
import sys, numpy as np, pandas as pd
sh = pd.read_csv("data/tk438/scen-shares.csv", dtype={"месяц": str}); en = pd.read_csv("data/tk438/scen-end.csv", dtype={"месяц": str})
en = en[en.сценарий != "все"]
K = 360
def holm(df, K):
    df = df.copy(); o = df.p.sort_values().index; adj = {}; run = 0
    for i, ix in enumerate(o):
        run = max(run, min(1, df.p[ix] * (K - i))); adj[ix] = run
    df["p_holm"] = pd.Series(adj); return df
def run(df, keys, tag):
    s = holm(df[df.месяц == "09"].copy(), K); a = df[df.месяц == "08"]
    m = s.merge(a[keys + ["разность", "p"]], on=keys, how="left", suffixes=("", "_авг"))
    m["сент_знач"] = m.p_holm < 0.05
    m["авг_подтв"] = (m.p_авг < 0.05) & (np.sign(m.разность) == np.sign(m.разность_авг))
    m["вердикт"] = np.where(m.сент_знач & m.авг_подтв, "подтверждено", np.where(m.сент_знач, "оспорено(авг. нет)", "не различимо"))
    m["тип"] = tag; return m
a = run(sh, ["D", "знак", "сценарий"], "доля"); b = run(en, ["D", "знак", "сценарий", "горизонт"], "конец")
a.to_csv("data/tk438/verdict-shares.csv", index=False); b.to_csv("data/tk438/verdict-end.csv", index=False)
print("доли: ячеек", len(a), a.вердикт.value_counts().to_dict(), "| сент. сырое p<0.05:", int((a.p < .05).sum()))
print("конец: ячеек", len(b), b.вердикт.value_counts().to_dict(), "| сент. сырое p<0.05:", int((b.p < .05).sum()))
# по сценарию: доли соб/контр (средн. по ячейкам сент.), число ячеек сырое p<.05 в обоих месяцах одного знака
for nm, m in (("доля", a), ("конец", b)):
    m["обоих"] = (m.p < .05) & (m.p_авг < .05) & (np.sign(m.разность) == np.sign(m.разность_авг))
    print(nm); print(m.groupby("сценарий").agg(ячеек=("p", "size"), сырое_сент=("p", lambda x: int((x < .05).sum())), оба_мес=("обоих", "sum"), холм=("сент_знач", "sum"), подтв=("вердикт", lambda x: int((x == "подтверждено").sum()))).to_string())
print(a[["D", "знак", "сценарий", "доля_соб", "доля_контр", "разность", "разность_авг", "p", "p_holm", "p_авг", "вердикт"]].query("p<0.05").round(3).to_string())
print(b[["D", "знак", "сценарий", "горизонт", "мед_соб", "мед_контр", "разность", "разность_авг", "p", "p_holm", "p_авг", "вердикт"]].query("вердикт!='не различимо'").round(3).to_string())
