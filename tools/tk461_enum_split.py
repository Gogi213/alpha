"""TK-461 поправка 12.1 (P-TK461): раскладка строк входов по ТФ × сторона, K D2b = Σ раскладки × X.

Вход: docs/research/P-TK461-entries-2026-10-30.csv (шаг 0). Выход: P-TK461-entries-split-2026-10-30.csv, K в stdout.
Правила (верхняя граница, пустые строки шаг 0b отбрасывает, K не уменьшается):
  sancho/M3/S — строка уже одна (ТФ, сторона) -> x1; bp — сторона берётся из def (high/low, bull/bear, long/short);
  g5/g61/g6 (сторона события не в ключе) -> x2 (long, short); sm M2 — сторона по event_label (два токена -> x2),
  ТФ — из row_label ("ТФ N мин"), нет -> число ТФ носителя (H1/H4/D1 -> x3, g8 5m / g5,g61 1m / g6 1h*L -> x1).
"""
import csv
import re
import sys

F = "docs/findings/"
IN = "docs/research/P-TK461-entries-2026-10-30.csv"
OUT = "docs/research/P-TK461-entries-split-2026-10-30.csv"
X = 449  # P-TK461 §4
ONE_TF = ("def.setup_g8", "def.asia_range", "def.session_window")


def sm_rows():
    d = {}
    with open(F + "smartmoney-survivors-2026-10-30.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            d[r["sm_id"]] = r
    return d


def main():
    sm = sm_rows()
    out = []  # source, tf, side, mode, how
    for r in csv.DictReader(open(IN, encoding="utf-8")):
        src, tf, d, mode = r["source"], r["tf"], r["side_or_def"], r["mode"]
        if src.startswith("sm:"):
            s = sm[src[3:]]
            sides = d.split()
            m = re.search(r"ТФ (\d+) мин", s["row_label"])
            if m:
                tfs = [m.group(1)]
            elif any(k in d for k in ONE_TF):
                tfs = ["1"]
            elif d.startswith("sm_s"):
                tfs = ["60"]
            else:
                tfs = ["60", "240", "1440"]
            for t in tfs:
                for sd in sides:
                    out.append((src, t, sd, mode, "sm"))
        elif src in ("bp:g5", "bp:g61", "bp:g6"):
            for sd in ("long", "short"):
                out.append((src, tf, d + "|" + sd, mode, "bp-side-x2"))
        else:
            out.append((src, tf, d, mode, "as-is"))
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["source", "tf", "side_or_def", "mode", "how"])
        w.writerows(out)
    e = len(out)
    print(f"E_split={e} X={X} K_D2b_upper={e * X} K_TK461_total={e * X + 1008} Holm_family={377 + e * X + 1008}")


if __name__ == "__main__":
    sys.exit(main())
