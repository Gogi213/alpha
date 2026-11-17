"""П-42 §13.17 (TK-588): описательные добавки ПОСЛЕ чтения результатов (не проверки, в счётчик не входят; в отчёте помечены «добавлено после чтения»):
(1) сделки `ow_e` в разреженной группе по `tpm60` (нижний квинтиль U1, граница из results.json) — потому что группа Z по `emp6h` пуста;
(2) корзины `turn24h`, `turn1h`, `age_h` (разведка) по эпизодам U1 — квантили половины A; (3) разрез `tpm60` по оборотному квинтилю.
Использование: python3 sigfeat_post.py <рабочий каталог> > post.md"""
import json
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")
from sigfeat_lib import O3, wilson

W = sys.argv[1]
res = json.load(open(f"{W}/results.json", encoding="utf-8"))
tw = pd.read_parquet(f"{W}/trades_ow_e.parquet")
ep = pd.read_parquet(f"{W}/episodes.parquet")
u1 = ep[(ep.univ == "U1")]
SIDES = (("long", "лонг"), ("short", "шорт"))


def row(df):
    n = len(df)
    return [n, f"{df['net'].sum():+.0f}", f"{df['net'].mean():+.2f}" if n else "—", f"{100 * (df['kind'] == 'stop').mean():.1f} %" if n else "—",
            f"{100 * (df['net'] < 0).mean():.1f} %" if n else "—", f"{df.loc[df['net'] < 0, 'net'].sum():.0f}"]


def table(head, rows):
    return "\n".join(["| " + " | ".join(head) + " |", "|" + "|".join("---" for _ in head) + "|"] + ["| " + " | ".join(str(c) for c in r) + " |" for r in rows])


print("### Сделки `ow_e` по разреженности `tpm60` (нижний квинтиль U1; добавлено после чтения)\n")
rows = []
for s, nm in SIDES:
    q = res[s]["sparse"]["U1_tpm60_bottom20"]["threshold"]
    t = tw[tw.side == s]
    z = t["tpm60"] <= q
    for lab, d in (("все", t), (f"tpm60 ≤ {q:.1f} (Z)", t[z & t.tpm60.notna()]), ("вне Z", t[~z & t.tpm60.notna()])):
        rows.append([nm, lab] + row(d))
    rows.append([nm, "без значения", int(t.tpm60.isna().sum()), "", "", "", "", ""])
print(table(["сторона", "группа", "сделок", "сумма $", "среднее $ на сделку", "доля стопов", "доля сделок с отрицательным итогом", "сумма отрицательных $"], rows))
print("\n### Корзины признаков разведки (U1, граница — квантили половины A; добавлено после чтения; «найдено на этих данных»)\n")
for f in ("turn24h", "turn1h", "age_h"):
    for s, nm in SIDES:
        d = u1[(u1.side == s) & u1.ok_t60]
        y = (d["o_x1_t60"] == O3).to_numpy().astype(int)
        x = d[f].to_numpy().astype(float)
        e = np.unique(np.quantile(x[(d.half == "A").to_numpy()], [0.2, 0.4, 0.6, 0.8]))
        b = np.searchsorted(e, x, side="left")
        rows = []
        for bi in range(len(e) + 1):
            m = b == bi
            k, n = int(y[m].sum()), int(m.sum())
            lo, hi = wilson(k, n)
            ha, hb = m & (d.half == "A").to_numpy(), m & (d.half == "B").to_numpy()
            lab = f"≤{e[0]:.4g}" if bi == 0 else (f">{e[-1]:.4g}" if bi == len(e) else f"({e[bi - 1]:.4g}; {e[bi]:.4g}]")
            rows.append([lab, n, k, f"{100 * k / n:.1f} %", f"[{100 * lo:.1f} %; {100 * hi:.1f} %]", f"{ha.sum()} / {100 * y[ha].mean():.1f} %" if ha.sum() else "—", f"{hb.sum()} / {100 * y[hb].mean():.1f} %" if hb.sum() else "—"])
        print(f"\n{nm}, `{f}`:\n")
        print(table(["корзина", "эпизодов", "O3", "доля O3", "Уилсон 95 %", "A: n / доля", "B: n / доля"], rows))
print("\n### `tpm60` и оборот: низкий `tpm60` внутри оборотных квинтилей (U1, лонг и шорт вместе по сторонам отдельно; добавлено после чтения)\n")
rows = []
for s, nm in SIDES:
    d = u1[(u1.side == s) & u1.ok_t60]
    y = (d["o_x1_t60"] == O3).to_numpy().astype(int)
    tq = np.quantile(d["turn24h"], [0.2, 0.4, 0.6, 0.8])
    tb = np.searchsorted(tq, d["turn24h"].to_numpy(), side="left")
    lo_t = (d["tpm60"] <= d["tpm60"].quantile(0.2)).to_numpy()
    for bi in range(5):
        m = tb == bi
        rows.append([nm, bi + 1, int(m.sum()), f"{100 * y[m].mean():.1f} %", int((m & lo_t).sum()), f"{100 * y[m & lo_t].mean():.1f} %" if (m & lo_t).sum() else "—", int((m & ~lo_t).sum()), f"{100 * y[m & ~lo_t].mean():.1f} %" if (m & ~lo_t).sum() else "—"])
print(table(["сторона", "квинтиль оборота за сутки (1 = меньше)", "эпизодов", "доля O3", "из них с низким tpm60", "доля O3", "остальные", "доля O3"], rows))
print("\n### Резкий подход без проторговки: `mv5` (верхний квинтиль по границе половины A) × `cons60` (0 против ≥ 1 минуты) — добавлено после чтения; описательно\n")
rows = []
for s, nm in SIDES:
    d = u1[(u1.side == s) & u1.ok_t60]
    y = (d["o_x1_t60"] == O3).to_numpy().astype(int)
    q80 = np.quantile(d.loc[d.half == "A", "mv5"], 0.8)
    hi = (d["mv5"] > q80).to_numpy()
    c0 = (d["cons60"] == 0).to_numpy()
    for a_, la in ((True, f"резкий (`mv5` > {q80:.2f})"), (False, "остальные")):
        for b_, lb in ((True, "без проторговки (`cons60` = 0)"), (False, "с проторговкой (`cons60` ≥ 1)")):
            m = (hi == a_) & (c0 == b_)
            k, n = int(y[m].sum()), int(m.sum())
            lo, up = wilson(k, n)
            ha, hb = m & (d.half == "A").to_numpy(), m & (d.half == "B").to_numpy()
            rows.append([nm, la, lb, n, k, f"{100 * k / n:.1f} %" if n else "—", f"[{100 * lo:.1f} %; {100 * up:.1f} %]" if n else "—", f"{ha.sum()} / {100 * y[ha].mean():.1f} %" if ha.sum() else "—", f"{hb.sum()} / {100 * y[hb].mean():.1f} %" if hb.sum() else "—"])
print(table(["сторона", "подход", "проторговка", "эпизодов", "O3", "доля O3", "Уилсон 95 %", "A: n / доля", "B: n / доля"], rows))
print("\n### Сосредоточение пробоев (O3, основной набор U1; добавлено после чтения)\n")
rows = []
for s, nm in SIDES:
    d = u1[(u1.side == s) & u1.ok_t60 & (u1.o_x1_t60 == O3)]
    c = d.sym.value_counts()
    dd = d.day.value_counts()
    rows.append([nm, len(d), c.size, int(c.iloc[:5].sum()), f"{c.index[0]} {int(c.iloc[0])}", dd.size, int(dd.iloc[:5].sum()), int(dd.iloc[0])])
print(table(["сторона", "пробоев", "монет с пробоем", "из них на 5 монетах с наибольшим числом", "наибольшее у монеты", "суток с пробоем", "на 5 сутках с наибольшим числом", "наибольшее за сутки"], rows))
print("\n### Оборот и волатильность: доля пробоев по квинтилям `turn24h` для меток в процентах и в NATR (добавлено после чтения; описательно)\n")
rows = []
for s, nm in SIDES:
    d = u1[(u1.side == s) & u1.ok_t60]
    tq = np.quantile(d.loc[d.half == "A", "turn24h"], [0.2, 0.4, 0.6, 0.8])
    tb = np.searchsorted(tq, d["turn24h"].to_numpy(), side="left")
    for bi in range(5):
        m = tb == bi
        g = d[m]
        rows.append([nm, bi + 1, int(m.sum()), f"{g['natr_bp'].mean():.0f}", f"{100 * (g['o_x1_t60'] == O3).mean():.1f} %", f"{100 * (g['o_n1_t60'] == O3).mean():.1f} %", f"{100 * (g['o_n2_t60'] == O3).mean():.1f} %",
                     f"{100 * (g['o_n4_t60'] == O3).mean():.1f} %", f"{100 * (g['o_x1_t60_f01'] == O3).mean():.1f} %"])
print(table(["сторона", "квинтиль оборота за сутки (1 = меньше)", "эпизодов", "средний NATR, бп", "O3: X = 1 %", "X = 1 NATR", "X = 2 NATR", "X = 4 NATR", "X = 1 %, возврат глубже 0,1 %"], rows))
