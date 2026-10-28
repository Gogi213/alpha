#!/usr/bin/env python3
"""TK-433 v11: v9b-признаки на случайной выборке событий v11 (Судья 28.10 07:10). r = ev/медиана контролей (±30), бутстреп по монетам,
Холм из 34×5=170 (нормальное приближение по SE, B=3000); знак на ±7 = подмножество контролей |Δ|<=7 сут (описательно, без бутстрепа);
сводный вердикт: класс Холма сент. = класс Холма авг. (±30) = знак медианы ±7 сент.
Использование: tk433-v11-v9b.py <features-09.csv> <features-08.csv> <выход-префикс>"""
import sys, importlib.util
import numpy as np, pandas as pd
from scipy.stats import norm

spec = importlib.util.spec_from_file_location("an", __file__.replace("tk433-v11-v9b.py", "tk433-analyze.py"))
an = importlib.util.module_from_spec(spec); spec.loader.exec_module(an)
FEATS, DS, K, B = an.FEATS, (90, 180, 360, 720, 1440), 170, 3000
an.B = B
DAY = 86400000


def span7(d):
    ev = d[d["роль"] == "event"].set_index("id")["t_start"]
    c = d["роль"] == "control"
    dt = (d.loc[c, "t_start"].to_numpy() - ev.reindex(d.loc[c, "event_id"]).to_numpy()).astype(float)
    keep = np.ones(len(d), bool)
    keep[np.where(c)[0]] = np.abs(dt) <= 7 * DAY + 3600000
    return d[keep]


def rows_for(d, rng, boot):
    out = []
    for D in DS:
        R, _ = an.build(d, D)
        for sg in (1, -1):
            sub = R[R["знак"] == sg]
            for f in FEATS:
                v = sub[f].to_numpy(float); ok = ~np.isnan(v)
                row = dict(D=D, знак="+" if sg == 1 else "-", признак=f, N=int(ok.sum()), монет=int(sub.loc[ok, "монета"].nunique()))
                if ok.sum() >= 8 and row["монет"] >= 5:
                    row["мед"] = float(np.median(v[ok]))
                    if boot:
                        bm = an.boot_medians(v[ok], sub.loc[ok, "монета"].to_numpy(), rng)
                        row["se"] = float(bm.std(ddof=1))
                out.append(row)
    return pd.DataFrame(out)


def holm(T):
    T = T.copy(); T["p"] = np.nan
    ok = T["se"].notna() & (T["se"] > 0)
    T.loc[ok, "p"] = 2 * norm.sf(np.abs(T.loc[ok, "мед"]) / T.loc[ok, "se"])
    order = T["p"].fillna(2).sort_values().index
    for rank, i in enumerate(order):
        T.loc[i, "k"] = K - rank
    for i, r in T.iterrows():
        if np.isnan(r["p"]):
            T.loc[i, "класс"] = 0; continue
        z = norm.isf(0.05 / r["k"] / 2)
        lo, hi = r["мед"] - z * r["se"], r["мед"] + z * r["se"]
        T.loc[i, "lo"], T.loc[i, "hi"] = lo, hi
        T.loc[i, "класс"] = 1 if lo > 0 else (-1 if hi < 0 else 0)
    return T


if __name__ == "__main__":
    f9, f8, out = sys.argv[1:4]
    d9, d8 = pd.read_csv(f9), pd.read_csv(f8)
    rng = np.random.default_rng(an.SEED)
    T9, T8 = holm(rows_for(d9, rng, True)), holm(rows_for(d8, rng, True))
    S9 = rows_for(span7(d9), rng, False)
    key = ["D", "знак", "признак"]
    M = T9.merge(T8[key + ["N", "мед", "lo", "hi", "класс"]], on=key, suffixes=("", "_авг")).merge(
        S9[key + ["N", "мед"]].rename(columns={"N": "N_7", "мед": "мед_7"}), on=key, how="left")
    s7 = np.sign(M["мед_7"])
    M["вердикт"] = np.where((M["класс"] != 0) & (M["класс"] == M["класс_авг"]) & (s7 == M["класс"]),
                            np.where(M["класс"] > 0, "подтверждено", "оспорено"), "не различимо")
    M.to_csv(out + "-v9b-on-v11.csv", index=False, float_format="%.4g")
    print(M["вердикт"].value_counts().to_string())
    print(M[M["вердикт"] != "не различимо"][key + ["N", "мед", "мед_авг", "мед_7", "вердикт"]].to_string())
    print(M.groupby(["D", "знак"])["N"].max().unstack().to_string())
