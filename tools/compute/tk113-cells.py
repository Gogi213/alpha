#!/usr/bin/env python3
"""TK-113/В-210: клетки П-12 R2 (40 + базы) по пулу v171c (без TRUMP/TRX/BCH, portfolio-sim --drop) против v171b: $ было/стало, Шарп (сутки, янв–сен),
месяцы с Шарпом > 0 (из определённых), доля KPI (наблюдение). Без пересчёта бэктестов; вход — closes из data/p12r2-v171c (+ CSV p12-r2-analyze).
Запуск: python tools/compute/tk113-cells.py -> docs/findings/cells-v171c-2026-10-09.{csv,md}. Описание, не вердикт (отбор — Судья, В-208)."""
import csv, os, subprocess, sys
import numpy as np
from p12lib import load_head

POOL = {f"2026-{i:02d}" for i in range(1, 10)}
OUT = "docs/findings/cells-v171c-2026-10-09"


def head(mode, d):
    os.environ["P12_DIR"] = d
    return load_head("tools/compute/p12-r2-analyze.py", "rng = np.random.default_rng(63)", [mode])


def sr(x):
    return float(x.mean() / x.std(ddof=1)) if len(x) > 2 and x.std(ddof=1) > 0 else 0.0


def rd(path):
    return {r["cell"]: r for r in csv.DictReader(open(path, encoding="utf-8"))}


rows = []
for mode in ("free", "B2"):
    old, new = rd(f"docs/findings/p12-r2-kpi-{mode}-2026-10-08.csv"), rd(f"docs/findings/p12-r2-kpi-{mode}-2026-10-08-v171c.csv")
    res = {}
    for tag, d in (("b", "data/p12r2/"), ("c", "data/p12r2-v171c/")):
        ns = head(mode, d)
        cal = ns["cal"]
        for c, v in ns["S"].items():   # S собран головой анализатора: ключи форм g87 (vg) и e117 (vt) — те же, что в p12-r2-analyze
            res[tag, c] = v[0]
    keep = np.array([x[:7] in POOL for x in cal])
    mon = np.array([x[:7] for x in cal])
    for c in list(old) + ["B1", "B3", "B1g"]:
        if c in ("B1", "B3", "B1g"):
            fam = c
            usd_b = float(res["b", c][keep].sum()); usd_c = float(res["c", c][keep].sum())
            kp = ""
        else:
            fam = old[c]["family"]; kp = new[c]["kpi_share_mean"]
            usd_b = float(res["b", c][keep].sum()) if ("b", c) in res else float("nan")
            usd_c = float(res["c", c][keep].sum()) if ("c", c) in res else float("nan")
        if ("c", c) not in res or not res["c", c].any():
            rows.append((mode, c, fam, round(usd_b, 1), round(usd_c, 1), None, None, 9, kp)); continue
        a = res["c", c]
        s_all = sr(a[keep])
        msr = [sr(a[mon == m]) for m in sorted(POOL)]
        rows.append((mode, c, fam, round(usd_b, 1), round(usd_c, 1), round(s_all, 3), sum(x > 0 for x in msr), len(msr), kp))
with open(OUT + ".csv", "w", encoding="utf-8", newline="") as fh:
    w = csv.writer(fh, lineterminator="\n")
    w.writerow(["mode", "cell", "family", "usd_v171b", "usd_v171c", "sharpe_day_jan_sep", "months_sr_pos", "months", "kpi_share_mean"])
    w.writerows(rows)
L = ["# Клетки П-12 R2 по пулу v171c (без TRUMP/TRX/BCH): $ было/стало, Шарп, месяцы с Шарпом > 0", "",
     "TK-113/В-210, **описание, не вердикт**. Точный пересчёт portfolio-sim --drop на тех же деревьях (без нового бэктеста), равная экспозиция §6(2) (m — по v171c (expo-v171c.json), "
     "посчитана с тремя монетами), потолок B2 применён заново к оставшимся сделкам. ВСЕ числа таблицы — в одном окне янв–сен (В-211; октябрь не входит; январь — калибровка П-12 §4: оговорка, окно не менять). Шарп — суточный ряд янв–сен (без годовой нормировки), мес. с Шарпом > 0 — из 9. "
     "Доля KPI — наблюдение (среднее по месяцам, v171c). Покрыты R2 (40) + базы; R1 (154) и ext (7) — следующим заходом.", ""]
for mode in ("free", "B2"):
    L += [f"## {mode}", "", "| клетка | семья | $ v171b (янв–сен) | $ v171c (янв–сен) | Шарп | мес. SR>0 | доля KPI |", "|---|---|---|---|---|---|---|"]
    for r in sorted([r for r in rows if r[0] == mode], key=lambda r: -(r[5] if r[5] is not None else -9)):
        L.append(f"| {r[1]} | {r[2]} | {r[3]:+.0f} | {r[4]:+.0f} | " + (f"{r[5]:+.3f} | {r[6]}/{r[7]}" if r[5] is not None else "— | —") + f" | {r[8]} |")
    L.append("")
open(OUT + ".md", "w", encoding="utf-8", newline="\n").write("\n".join(L))
print("\n".join(L[:22]))
