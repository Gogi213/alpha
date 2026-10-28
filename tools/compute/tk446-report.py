#!/usr/bin/env python3
"""TK-446: сводка кривых по месяцам. Правило выбора порога (заморожено, чисел не назначает):
порог = argmax по сетке от min_{янв..июнь}(подъём_lo), все 6 месяцев определены; проверка июль/авг/сент. без подбора.
  python3 tk446-report.py <каталог с m01..m07,aug,sep .csv> <вых. префикс>"""
import sys
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

src, outp = sys.argv[1], sys.argv[2]
MON = {"m01": "2026-01", "m02": "2026-02", "m03": "2026-03", "m04": "2026-04", "m05": "2026-05", "m06": "2026-06",
       "m07": "2026-07", "aug": "2026-08", "sep": "2026-09"}
TRAIN, TEST = ["m01", "m02", "m03", "m04", "m05", "m06"], ["m07", "aug", "sep"]
d = pd.concat([pd.read_csv(f"{src}/{k}.csv").assign(мес=k) for k in MON])
ntr = len(d[d.мес == "sep"])
out_rows, md = [], []


def fam(a):
    x = d[d.ось == a]
    if a == "A3":
        x = x[x.напр == "any"]
    elif a != "A4":
        x = x[x.напр == "any"]
    return x


def pick(x, keys, thr):
    """x: строки одной семьи; возвращает таблицу порог → min lo на train."""
    g = x[x.мес.isin(TRAIN)].pivot_table(index=keys + [thr], columns="мес", values="подъём_lo")
    g = g.dropna()
    return g.min(axis=1).sort_values(ascending=False)


fig, ax = plt.subplots(2, 3, figsize=(18, 9))
def curve(axis, x, keys, thr, title, a_):
    sel = []
    for k, grp in x.groupby(keys):
        k = k if isinstance(k, tuple) else (k,)
        sc = pick(grp, [], thr)
        if sc.empty:
            continue
        best = sc.index[0]
        sel.append((k, best, sc.iloc[0]))
        med = grp[grp.мес.isin(TRAIN)].groupby(thr).подъём.median()
        row = {"ось": axis, "семья": "|".join(map(str, k)), "порог": best, "min_lo_янв-июнь": round(sc.iloc[0], 3),
               "пик_по_медиане_янв-июнь": f"×{med.idxmax():.4g} (подъём {med.max():.1f})", "конец_сетки": round(float(grp[thr].max()), 3)}
        for m in TEST + TRAIN:
            r = grp[(grp.мес == m) & (np.isclose(grp[thr], best))]
            if len(r):
                r = r.iloc[0]
                row[MON[m]] = f"{r.подъём:.2f} [{r.подъём_lo:.2f};{r.подъём_hi:.2f}] т={r.точность:.4f} n={int(r.срабат)} ({r.срабат_в_сутки_на_монету:.3g}/сут/мон)"
        out_rows.append(row)


MC = (("m07", "C1", "июль"), ("aug", "C2", "август"), ("sep", "C3", "сент."))


def lines(a_, grp, thr, col="подъём", title="", xl="порог"):
    P = grp.pivot_table(index=thr, columns="мес", values=col)
    tr = P[[m for m in TRAIN if m in P]]
    a_.fill_between(P.index, tr.min(axis=1), tr.max(axis=1), color="0.85", label="янв–июнь мин–макс")
    a_.plot(P.index, tr.median(axis=1), "k", lw=1.5, label="янв–июнь медиана")
    for m, c, nm in MC:
        if m in P:
            a_.plot(P.index, P[m], c, lw=1.3, label=nm)
    a_.set_xscale("log"); a_.set_title(title, fontsize=9); a_.set_xlabel(xl); a_.set_ylabel(col); a_.legend(fontsize=6)


curve("A1", fam("A1"), ["сигнал", "D"], "порог", "", None)
curve("A2", fam("A2"), ["D"], "порог", "", None)
lines(ax[0, 0], fam("A1").query("сигнал=='q15' and D==90"), "порог", title="A1 оборот 15 м к норме, D=90: подъём", xl="кратность оборота")
lines(ax[0, 1], fam("A2").query("D==90"), "порог", title="A2 σ1м к норме, D=90: подъём", xl="кратность σ")
x3 = fam("A3").copy(); x3["Dq"] = x3.D.astype(str) + "/" + x3.q.astype(str)
sc3 = pick(x3, [], "Dq")
b3 = sc3.index[0]
bD, bq = b3.split("/"); bD, bq = int(bD), float(bq)
row = {"ось": "A3", "семья": "S10", "порог": "D/q=" + b3, "min_lo_янв-июнь": round(sc3.iloc[0], 3)}
for m in TEST + TRAIN:
    r = x3[(x3.мес == m) & (x3.Dq == b3)].iloc[0]
    row[MON[m]] = f"{r.подъём:.2f} [{r.подъём_lo:.2f};{r.подъём_hi:.2f}] т={r.точность:.4f} n={int(r.срабат)}"
out_rows.append(row)
lines(ax[0, 2], x3[x3.q == bq], "D", title=f"A3 S10: подъём по D мин при q{bq}", xl="D, мин")
lines(ax[1, 0], x3[x3.D == bD], "q", title=f"A3 S10: подъём по квантилю хода при D={bD}", xl="q, %")
ax[1, 0].set_xscale("linear")
# A4: горизонт
a4 = d[(d.ось == "A4") & d.сигнал.str.startswith("S10:r60")].copy()
for sgl, grp in a4.groupby("сигнал"):
    P = grp.pivot_table(index="порог", columns="мес", values="точность")
    LO = grp.pivot_table(index="порог", columns="мес", values="подъём_lo")
    HI = grp.pivot_table(index="порог", columns="мес", values="подъём_hi")
    hc = LO[TRAIN].dropna().min(axis=1).idxmax()          # продолжение
    hr = (-HI[TRAIN]).dropna().min(axis=1).idxmax()        # откат
    for nm, h in (("продолжение", hc), ("откат", hr)):
        row = {"ось": "A4", "семья": sgl, "порог": f"h={h:.0f}мин ({nm})",
               "min_lo_янв-июнь": round(float(LO[TRAIN].loc[h].min() if nm == "продолжение" else (-HI[TRAIN].loc[h]).min()), 3)}
        for m in TEST + TRAIN:
            r = grp[(grp.мес == m) & (grp.порог == h)].iloc[0]
            row[MON[m]] = f"ср.{r.точность:+.1f}б.п. [{r.подъём_lo:+.1f};{r.подъём_hi:+.1f}] n={int(r.срабат)}"
        out_rows.append(row)
    a_ = ax[1, 1] if sgl.endswith(">0") else ax[1, 2]
    lines(a_, grp, "порог", col="точность", title=f"A4 {sgl}: ср. направл. возврат б.п. за горизонт", xl="горизонт, мин")
    a_.axhline(0, c="k", lw=.5)
plt.tight_layout(); plt.savefig(outp + ".png", dpi=110)
pd.DataFrame(out_rows).to_csv(outp + "-selected.csv", index=False)
d.to_csv(outp + "-all.csv", index=False)
print(f"испытаний (строк на месяц) {ntr}; месяцев {len(MON)}")
print(pd.DataFrame(out_rows).to_string(max_colwidth=70))
