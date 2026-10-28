#!/usr/bin/env python3
"""TK-448 v2: сводка cond-<мес>.csv -> csv+md. python3 tk448-agg.py <каталог cond> <префикс вых.>
Пороги как в первой сдаче: корзины >= 2000 ячеек; подъём = CI_lo лучшей > 1,2; спад = CI_hi худшей < 0,83;
держится: разброс усл. >= 2 и подъём >= 70 % мес.; механика: безусл. >= 1,5, усл. < 1,5; нет эффекта: оба < 1,5; < 3 мес. — «мало месяцев»."""
import csv, glob, sys, statistics as st
from collections import defaultdict
d, outp = sys.argv[1:3]
R = defaultdict(lambda: defaultdict(list))  # (признак,вид) -> месяц -> строки
for fn in sorted(glob.glob(f"{d}/cond-*.csv")):
    for r in csv.DictReader(open(fn, encoding="utf-8")):
        R[(r["признак"], r["вид"])][r["месяц"]].append(r)
def stat(rows):
    b = [r for r in rows if int(r["ячеек"]) >= 2000]
    if len(b) < 2:
        return None
    hi = max(b, key=lambda r: float(r["подъём"])); lo = min(b, key=lambda r: float(r["подъём"]))
    lf = float(lo["подъём"])
    return (float(hi["подъём"]) / lf if lf > 0 else float("inf"), float(hi["подъём_lo"]) > 1.2, float(lo["подъём_hi"]) < 0.83)
out = []
for nm in sorted({k[0] for k in R}):
    res = {}
    for kind in ("безусл", "усл"):
        ms = {m: stat(rs) for m, rs in R[(nm, kind)].items()}
        ms = {m: v for m, v in ms.items() if v}
        res[kind] = ms
    nmo = len(res["усл"])
    if nmo == 0:
        continue
    def agg(kind):
        v = list(res[kind].values())
        if not v: return None
        return st.median(x[0] for x in v), sum(x[1] for x in v), sum(x[2] for x in v)
    a, c = agg("безусл"), agg("усл")
    if c is None or a is None: continue
    sb, su = a[0], c[0]
    keep = (su - 1) / (sb - 1) if sb > 1.0001 and su != float("inf") else float("nan")
    if su == float("inf") or sb == float("inf"): v = "не оценить: корзина с 0 попаданий"
    elif nmo < 3: v = f"мало месяцев ({nmo}), вне счёта"
    elif su >= 2 and c[1] >= 0.7 * nmo: v = "держится"
    elif sb >= 1.5 and su < 1.5: v = "механика"
    elif sb < 1.5 and su < 1.5: v = "нет эффекта"
    else: v = "слабо/частично"
    out.append((nm, sb, su, a[1], c[1], a[2], c[2], nmo, keep, v))
out.sort(key=lambda r: -(r[2] if r[2] != float("inf") else 1e9))
with open(outp + ".csv", "w", encoding="utf-8", newline="") as fo:
    w = csv.writer(fo); w.writerow("признак,разброс_безусл,разброс_усл,мес_подъём_безусл,мес_подъём_усл,мес_спад_безусл,мес_спад_усл,мес,доля_сохр,вердикт".split(","))
    for r in out: w.writerow([r[0]] + [f"{x:.3f}" if isinstance(x, float) else x for x in r[1:]])
with open(outp + "-table.md", "w", encoding="utf-8", newline="\n") as fo:
    fo.write("| признак | разброс безусл. | разброс усл. | подъём усл. (мес.) | спад усл. (мес.) | вердикт |\n|---|---|---|---|---|---|\n")
    for r in out: fo.write(f"| {r[0]} | {r[1]:.1f} | {r[2]:.1f} | {r[4]}/{r[7]} | {r[6]}/{r[7]} | {r[9]} |\n")
from collections import Counter
print(Counter(r[9] for r in out))
for r in out:
    if r[0] in ("ret24h_bps", "ctl_abs_ret24h", "ctl_vol24h"): print("САМОПРОВЕРКА", r[0], "усл.", round(r[2], 2), "безусл.", round(r[1], 2))
