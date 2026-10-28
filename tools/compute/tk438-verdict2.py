"""TK-438 вердикты v2: доли (r3s, NB=20000, Холм K=360) и конец пампа агрегатом (H-016, кD, Холм K=36); авг. — тем же знаком p<0.05."""
import numpy as np, pandas as pd, glob
D = "data/tk438/"
sh = pd.read_csv(D + "r3s-shares.csv", dtype={"месяц": str})
en = pd.concat([pd.read_csv(f, dtype={"месяц": str}) for f in sorted(glob.glob(D + "r3a?-agg.csv"))])
en = en[en.отсчёт == "кD"].reset_index(drop=True)
def holm(df, K):
    o = df.p.sort_values().index; adj = {}; run = 0
    for i, ix in enumerate(o):
        run = max(run, min(1, df.p[ix] * (K - i))); adj[ix] = run
    return pd.Series(adj)
def run(df, keys, K):
    s = df[df.месяц == "09"].copy(); s["p_holm"] = holm(s, K); a = df[df.месяц == "08"]
    m = s.merge(a[keys + ["разность", "p"]], on=keys, how="left", suffixes=("", "_авг"))
    z = m.p_holm < 0.05; same = np.sign(m.разность) == np.sign(m.разность_авг); ok = (m.p_авг < 0.05) & same
    m["вердикт"] = np.where(z & ok, "подтверждено", np.where(z & m.p_авг.isna(), "нет авг.", np.where(z & (m.p_авг < 0.05), "оспорено(обратный знак)", np.where(z, "оспорено(авг. нет)", "не различимо"))))
    return m
a = run(sh, ["D", "знак", "сценарий"], 360); b = run(en, ["сценарий", "горизонт"], 36)
a.to_csv(D + "verdict2-shares.csv", index=False); b.to_csv(D + "verdict2-end.csv", index=False)
g = a.groupby("сценарий").agg(ячеек=("p", "size"), доля_соб=("доля_соб", "median"), доля_контр=("доля_контр", "median"), холм=("вердикт", lambda x: int((x != "не различимо").sum())), подтв=("вердикт", lambda x: int((x == "подтверждено").sum())), оспор=("вердикт", lambda x: int(x.str.startswith("оспорено").sum())), сырое09=("p", lambda x: int((x < .05).sum())))
print(g.round(3).to_string())
print(b[["сценарий", "горизонт", "n_e", "разность", "lo", "hi", "p", "p_holm", "разность_авг", "p_авг", "вердикт"]].round(4).to_string())
