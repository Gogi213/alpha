"""TK-461 шаг 0b (часть 1): n_events строк S1-S5 из воронок TK-459 (calc:/data/tk459/s*-funnel.csv).
n_events строки S = число событий-носителей С выполненным фактором (столбец «с фактором» / «премиум/дискаунт=1» /
«из них A8» / «со свипом в час» / «фактор=1»). Порядок строк = порядок S_ROWS в tk461_enum.py (S1 8, S2 12, S3 24, S4 10, S5 8).
Запуск: python tools/tk461_0b_s.py <каталог с *-funnel.csv>  -> docs/research/P-TK461-s-counts-2026-10-30.csv"""
import csv
import sys

COL = {"s1": 2, "s2": 3, "s3": 2, "s4": 3, "s5": 2}  # индекс столбца числа событий с фактором
EXPECT = {"s1": 8, "s2": 12, "s3": 24, "s4": 10, "s5": 8}
OUT = "docs/research/P-TK461-s-counts-2026-10-30.csv"


def main(d):
    out = []
    for s, c in COL.items():
        with open(f"{d}/{s}-funnel.csv", encoding="utf-8") as f:
            rows = list(csv.reader(f))[1:]
        if s == "s5":  # S5: 4 значения d дают одинаковые события-носители -> 8 строк (Азия/…) как в S_ROWS
            pass
        assert len(rows) == EXPECT[s], (s, len(rows))
        for r in rows:
            out.append((s.upper(), r[0], int(r[1]), int(r[c])))
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["S", "row", "carrier_events", "n_events_factor"])
        w.writerows(out)
    print(len(out), "rows; min n:", min(r[3] for r in out))


if __name__ == "__main__":
    main(sys.argv[1])
