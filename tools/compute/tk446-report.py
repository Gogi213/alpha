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


fig, ax = plt.subplots(2, 2, figsize=(14, 9))
def curve(axis, x, keys, thr, title, a_):
    sel = []
    for k, grp in x.groupby(keys):
        k = k if isinstance(k, tuple) else (k,)
        sc = pick(grp, [], thr)
        if sc.empty:
            continue
        best = sc.index[0]
        sel.append((k, best, sc.iloc[0]))
        row = {"ось": axis, "семья": "|".join(map(str, k)), "порог": best, "min_lo_янв-июнь": round(sc.iloc[0], 3)}
        for m in TEST + TRAIN:
            r = grp[(grp.мес == m) & (np.isclose(grp[thr], best))]
            if len(r):
                r = r.iloc[0]
                row[MON[m]] = f"{r.подъём:.2f} [{r.подъём_lo:.2f};{r.подъём_hi:.2f}] т={r.точность:.4f} n={int(r.срабат)} ({r.срабат_в_сутки_на_монету:.3g}/сут/мон)"
        out_rows.append(row)
    for k, grp in x.groupby(keys):
        k = k if isinstance(k, tuple) else (k,)
        med = grp[grp.мес.isin(TRAIN)].groupby(thr).подъём.median()
        a_.plot(med.index, med.values, lw=1, alpha=.5, label="/".join(map(str, k)) + " янв–июнь мед.")
        for m, c in (("m07", "C1"), ("aug", "C2"), ("sep", "C3")):
            g = grp[grp.мес == m].sort_values(thr)
            a_.plot(g[thr], g.подъём, c, lw=.7, alpha=.6)
    a_.set_xscale("log"); a_.set_title(title); a_.set_xlabel("порог"); a_.set_ylabel("подъём к базе")


curve("A1", fam("A1"), ["сигнал", "D"], "порог", "A1 оборот к норме (цвета: июль/авг/сент.)", ax[0, 0])
curve("A2", fam("A2"), ["D"], "порог", "A2 волатильность к норме", ax[0, 1])
# A3: порог = (D,q)
x3 = fam("A3").copy(); x3["Dq"] = x3.D.astype(str) + "/" + x3.q.astype(str)
sc3 = pick(x3, [], "Dq")
b3 = sc3.index[0]
row = {"ось": "A3", "семья": "S10", "порог": "D/q=" + b3, "min_lo_янв-июнь": round(sc3.iloc[0], 3)}
for m in TEST + TRAIN:
    r = x3[(x3.мес == m) & (x3.Dq == b3)].iloc[0]
    row[MON[m]] = f"{r.подъём:.2f} [{r.подъём_lo:.2f};{r.подъём_hi:.2f}] т={r.точность:.4f} n={int(r.срабат)}"
out_rows.append(row)
for q, grp in x3.groupby("q"):
    med = grp[grp.мес.isin(TRAIN)].groupby("D").подъём.median()
    ax[1, 0].plot(med.index, med.values, label=f"q{q} янв–июнь мед.")
    s = grp[grp.мес == "sep"].sort_values("D"); ax[1, 0].plot(s.D, s.подъём, ":", alpha=.6)
ax[1, 0].set_xscale("log"); ax[1, 0].set_title("A3 длительность хода D × квантиль (пунктир — сент.)"); ax[1, 0].legend(fontsize=6)
# A4: горизонт
a4 = d[(d.ось == "A4") & d.сигнал.str.startswith("S10:r60")].copy()
for sgl, grp in a4.groupby("сигнал"):
    P = grp.pivot_table(index="порог", columns="мес", values="точность")
    LO = grp.pivot_table(index="порог", columns="мес", values="подъём_lo")
    HI = grp.pivot_table(index="порог", columns="мес", values="подъём_hi")
    pr, hi_ = P[TRAIN], HI[TRAIN]
    hc = LO[TRAIN].dropna().min(axis=1).idxmax()          # продолжение
    hr = (-HI[TRAIN]).dropna().min(axis=1).idxmax()        # откат
    for nm, h in (("продолжение", hc), ("откат", hr)):
        row = {"ось": "A4", "семья": sgl, "порог": f"h={h:.0f}мин ({nm})",
               "min_lo_янв-июнь": round(float(LO[TRAIN].loc[h].min() if nm == "продолжение" else (-HI[TRAIN].loc[h]).min()), 3)}
        for m in TEST + TRAIN:
            r = grp[(grp.мес == m) & (grp.порог == h)].iloc[0]
            row[MON[m]] = f"ср.{r.точность:+.1f}б.п. [{r.подъём_lo:+.1f};{r.подъём_hi:+.1f}] n={int(r.срабат)}"
        out_rows.append(row)
    ax[1, 1].plot(P.index, P[TRAIN].median(axis=1), label=sgl + " янв–июнь мед.")
    ax[1, 1].plot(P.index, P["sep"], ":", label=sgl + " сент.")
ax[1, 1].axhline(0, c="k", lw=.5); ax[1, 1].set_xscale("log"); ax[1, 1].set_title("A4 ср. направленный возврат (б.п.) за горизонт, мин"); ax[1, 1].legend(fontsize=6)
ax[0, 0].legend(fontsize=5)
plt.tight_layout(); plt.savefig(outp + ".png", dpi=110)
pd.DataFrame(out_rows).to_csv(outp + "-selected.csv", index=False)
d.to_csv(outp + "-all.csv", index=False)
print(f"испытаний (строк на месяц) {ntr}; месяцев {len(MON)}")
print(pd.DataFrame(out_rows).to_string(max_colwidth=70))
