"""TK-461 шаг 0 (P-TK461 §9): список входов E и число испытаний K = E * X до симулятора.

Строка входа = (источник, ТФ, сторона/определение, режим M0..M3). Определимо локально:
  Санчо — тройки (ev, tf, dir) из rows TK-455; bp — пары (tf, def_id) из файлов событий TK-458;
  M2 — по одной строке на каждого из 47 выживших (smartmoney-survivors).
Не определимо без файлов calc (/data/tk0451/l1, sm_s*): M3 (пересечение >= 2 выживших по ключам) и
M0/M1 для S1-S6 — строки помечаются `calc`, счёт на calc `--calc` дописывает их (шаг 0b).
Запуск: python tools/tk461_enum.py  ->  docs/research/P-TK461-entries-2026-10-30.csv, K в stdout.
"""
import collections
import csv
import gzip
import sys

F = "docs/findings/"
OUT = "docs/research/P-TK461-entries-2026-10-30.csv"
X_BASE, X_BE, X_OT, X_OTBE = 41, 120, 144, 144
X = X_BASE + X_BE + X_OT + X_OTBE  # 449 (P-TK461 §4)
BP = "g1 g2 g3 g32 g4 g41 g42 g5 g6 g61 g7 g8".split()


def sancho():
    seen = collections.OrderedDict()
    with open(F + "sancho-strategy-2026-10-30-rows.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            seen[(r["ev"], r["tf"], r["dir"])] = 1
    return list(seen)


def bp():
    out = []
    for g in BP:
        c = collections.Counter()
        with gzip.open(F + f"bp-{g}-events-2026-10-30.csv.gz", "rt", encoding="utf-8") as f:
            rd = csv.reader(f)
            next(rd)
            for l in rd:
                c[(l[2], l[3])] += 1
        for (tf, d), n in sorted(c.items()):
            out.append((g, tf, d, n))
    return out


# S1-S6 (P-TK459 §3) — строки, которые считались: S1 только 1H (4H/1D «не проверяется»), S6 — цепочки канала без бара/ТФ
# события входа -> вне оси входов. S = фактор-подмножество A5 (S3 — A8), M0 у них совпадает с M0 носителя -> одна строка M1.
S_ROWS = [("S1", "60", "H/L x asia,frankfurt,london,ny", 8), ("S2", "60|240|1440", "H/L x (ТФ свипа, старший ТФ)", 12),
          ("S3", "60|240|1440", "H/L x (fulfill, инвалидация) x (k=2, k=1)", 24), ("S4", "5 пар ТФ", "H/L", 10),
          ("S5", "60", "H/L x d(0,25;0,5;1;без предела)", 8)]
# M3: >= 2 РАЗНЫХ признака (разные event_label; исходы одного признака — один признак). Из survivors: A5 1H = S2 ∩ S5 (+L2 для L);
# + A8 ∩ S-сочетание по 3 ТФ x 2 стороны. Непустоту проверяет шаг 0b на calc; K объявлен как ВЕРХНЯЯ граница (пустые строки
# отбрасываются, K не уменьшается).
M3_ROWS = [("A5", "60", "H"), ("A5", "60", "L")] + [("A8", tf, d) for tf in ("60", "240", "1440") for d in ("H", "L")]


def main():
    rows = []  # source, tf, side, mode, n_events, where
    for ev, tf, d in sancho():
        for m in ("M0", "M1"):
            rows.append(("sancho:" + ev, tf, d, m, "", "calc"))
    for g, tf, d, n in bp():
        # файл событий TK-458 уже содержит определение-якорь: M0 = M1 (одна строка M1, M0 не дублируется)
        rows.append(("bp:" + g, tf, d, "M1", n, "local"))
    with open(F + "smartmoney-survivors-2026-10-30.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append(("sm:" + r["sm_id"], "", r["event_label"], "M2", "", "local"))
    for sid, tf, side, k in S_ROWS:
        for i in range(k):
            rows.append((f"S:{sid}", tf, f"{side} #{i + 1}", "M1", "", "calc-count"))
    for src, tf, d in M3_ROWS:
        rows.append((src, tf, d, "M3", "", "calc-count"))
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["source", "tf", "side_or_def", "mode", "n_events", "where"])
        w.writerows(rows)
    e = len(rows)
    by = collections.Counter(r[3] for r in rows)
    print(f"E_local={e} {dict(by)} X={X} K_upper={e * X}")
    print("K — верхняя граница; шаг 0b (calc) считает n_events для calc/calc-count и отбрасывает пустые строки, K не уменьшается")


if __name__ == "__main__":
    sys.exit(main())
