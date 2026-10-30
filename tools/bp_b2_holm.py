"""TK-458 B2 шаг 14: Холм по всему семейству B2 вместо Холма по шагам (возврат Судьи п.2). Без нового прохода: p берутся из *-total.
p = min(1, 2·min(p_gt0, p_lt0) + 1/B) (P-TK457 §2); B файла: 2000 (шаги 1–12), 4000 (g61). Холм по m=max(70, число строк с p) и по m=70.
Вердикт по строке: прежний 'подтверждено'/'оспорено' сохраняется, только если скорр. p < 0,05 и знак Δ тот же; иначе 'не различимо'; прежнее 'не различимо' не меняется.
python -I tools/bp_b2_holm.py <docs/findings> <выход.csv>"""
import sys, os
import numpy as np, pandas as pd

F = sys.argv[1]
FILES = [("G1.1", "bp-g1-total-2026-10-30.csv"), ("G2", "bp-g2-total-2026-10-30.csv"), ("G3.1", "bp-g3-total-2026-10-30.csv"), ("G3.2/3.3", "bp-g32-total-2026-10-30.csv"),
         ("G4.3", "bp-g4-total-2026-10-30.csv"), ("G4.1", "bp-g41-total-2026-10-30.csv"), ("G4.2", "bp-g42-2026-10-30-total.csv"), ("G5", "bp-g5-total-2026-10-30.csv"),
         ("G6.4", "bp-g6-total-2026-10-30.csv"), ("G6.3", "bp-g63-total-2026-10-30.csv"), ("G7", "bp-g7-total-2026-10-30.csv"), ("G8.1", "bp-g8-total-2026-10-30.csv"),
         ("G6.1/6.2", "bp-g61-total-2026-10-30.csv")]
rows = []
for g, f in FILES:
    p = os.path.join(F, f)
    if not os.path.exists(p): print("нет файла", f); continue
    d = pd.read_csv(p); lab = [c for c in d.columns[:2] if d[c].dtype == object]
    B = 4000 if g.startswith("G6.1") else 2000
    for _, r in d.iterrows():
        if "срез" in d.columns and r["срез"] != "итог": continue
        rows.append((g, " | ".join(str(r[c]) for c in lab), r.dlt, B, min(1.0, 2 * min(r.p_gt0, r.p_lt0) + 1 / B), r["вердикт"], r.holm_gt0, r.holm_lt0))
t = pd.DataFrame(rows, columns=["группа", "строка", "dlt", "B", "p", "вердикт_шага", "holm_gt0_шага", "holm_lt0_шага"])


def holm(p, m):
    o = np.argsort(p); out = np.empty(len(p)); run = 0
    for k, i in enumerate(o):
        run = max(run, min(1, p[i] * (m - k))); out[i] = run
    return out


n = len(t); m = max(70, n)
t["holm_m"] = holm(t.p.values, m); t["holm_70"] = holm(t.p.values, 70)
def new(r, col):
    v = r.вердикт_шага
    if v.startswith("не различимо"): return v
    side_gt = r.holm_gt0_шага < .05
    return v if (r[col] < .05 and (r.dlt > 0) == side_gt) else "не различимо (было " + v + ")"
t["вердикт_K"] = t.apply(lambda r: new(r, "holm_m"), axis=1); t["вердикт_70"] = t.apply(lambda r: new(r, "holm_70"), axis=1)
t.to_csv(sys.argv[2], index=False)
cnt = lambda c: t[c].str.extract(r"^(подтверждено|оспорено|не различимо)")[0].value_counts().to_dict()
print("строк с p:", n, "| m =", m); print("шаги:", cnt("вердикт_шага")); print("K=m:", cnt("вердикт_K")); print("K=70:", cnt("вердикт_70"))
ch = t[t.вердикт_K.str.contains("было") | t.вердикт_70.str.contains("было")]
print(ch[["группа", "строка", "p", "holm_m", "holm_70", "вердикт_шага"]].round(4).to_string())
bd = t[(t.holm_m > .02) & (t.holm_m < .1)]; print("у порога (holm_m 0,02…0,1):", len(bd)); print(bd[["группа", "строка", "p", "holm_m", "вердикт_шага"]].round(4).to_string())
