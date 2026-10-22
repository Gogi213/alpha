#!/usr/bin/env python3
"""TK-345 п.3 Судьи: σ240 σ-лестницы (В-131) без заглядывания и без расхождения с независимым пересчётом.
Использование: tk345-sigma-check.py <sigma-table.py> <klines-dir> <sigma-dir> <выход.txt> [символ…]
Проверки по каждому символу (по умолчанию — все `ref-*-1m.csv`):
  (1) таблица на диске = свежий `sigma_rows(closes)` (повтор дал те же байты);
  (2) наивный пересчёт σ(E) = √Σ ln²(C_m/C_{m-1}) по минутам открытия E−240…E−1 (только пары подряд идущих свечей, ≥200) на
      выборке концов окон равен таблице до 1e-5 бп (относит.);
  (3) заглядывание: все закрытия минут с открытием ≥ E0 заменены другими числами — строки с E ≤ E0 не меняются, строки с
      E > E0 меняются (тест чувствителен).
Печатает «символ | строк | (1) | (2) выборка/расх. | (3) E≤E0 неизм./изм. | E>E0 изм.»; нарушение — итог «НАРУШЕНИЙ n»."""
import csv, glob, importlib.util, math, os, random, sys

tbl_py, kdir, sdir, out = sys.argv[1:5]
spec = importlib.util.spec_from_file_location("sigma_table", tbl_py)
st = importlib.util.module_from_spec(spec)
spec.loader.exec_module(st)
M = st.MINUTE_MS
syms = sys.argv[5:] or sorted(os.path.basename(p)[4:-7] for p in glob.glob(os.path.join(kdir, "ref-*-1m.csv")))
rng = random.Random(345)


def naive(closes, e):
    """σ по определению: минуты открытия e−240…e−1, доходность — только если есть и свеча m, и m−1."""
    s, n = 0.0, 0
    for k in range(e - st.WINDOW_MIN * M, e, M):
        a, b = closes.get(k - M), closes.get(k)
        if a is None or b is None:
            continue
        s += math.log(float(b) / float(a)) ** 2
        n += 1
    return (math.sqrt(s) * 10_000, n)


lines, bad = [], 0
for sym in syms:
    closes = st.read_closes([kdir], sym)
    rows = st.sigma_rows(closes)
    if not rows:
        lines.append(f"{sym} | 0 строк")
        continue
    tab = {}
    path = os.path.join(sdir, f"sigma-{sym}.csv")
    ex = os.path.exists(path)
    if ex:
        with open(path, encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                tab[int(r["window_end_ms"])] = float(r["sigma_bps"])
    c1 = "нет файла" if not ex else ("ок" if tab == {e: float(f"{v:.6f}") for e, v in rows} else "РАЗЛ")
    if c1 == "РАЗЛ":
        bad += 1
    pick = rng.sample(rows, min(40, len(rows)))
    d2 = 0
    for e, v in pick:
        nv, n = naive(closes, e)
        if n < st.MIN_RETURNS or abs(nv - v) > 1e-5 * max(1.0, v):
            d2 += 1
    bad += d2
    e0 = rows[len(rows) // 2][0]
    fut = {m: (str(float(c) * (1.0 + rng.uniform(0.01, 0.2))) if m >= e0 else c) for m, c in closes.items()}
    base = dict(rows)
    alt = dict(st.sigma_rows(fut))
    same_before = sum(1 for e in base if e <= e0 and alt.get(e) == base[e])
    n_before = sum(1 for e in base if e <= e0)
    chg_after = sum(1 for e in base if e > e0 + st.WINDOW_MIN * M and alt.get(e) != base[e])
    n_after = sum(1 for e in base if e > e0 + st.WINDOW_MIN * M)
    bad += n_before - same_before
    lines.append(f"{sym} | {len(rows)} | (1) {c1} | (2) {len(pick)}/{d2} | (3) E≤E0 неизм. {same_before}/{n_before} | E>E0+240м изм. {chg_after}/{n_after}")
lines.append(f"НАРУШЕНИЙ {bad}; символов {len(syms)}")
with open(out, "w", encoding="utf-8") as f:
    f.write("\n".join(lines) + "\n")
print("\n".join(lines[-3:]))
