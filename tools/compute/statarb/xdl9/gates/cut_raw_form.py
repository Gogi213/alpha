#!/usr/bin/env python3
"""TK-493 w5: копии СЫРЬЯ (`candles/<SYM>.csv.gz`, `funding/<SYM>.csv.gz`) только за сутки формирования (строки со временем < cut_day·86400 с).

Для независимого пересчёта (check_trades.py) без чтения торгового окна: проверка читает эти копии, а не полное сырьё `~/xdl/out`.
  cut_raw_form.py <корень сырья> <выход> <cut_day=2479> <биржа:SYMBOL> [<биржа:SYMBOL> ...]
Выход: <выход>/<биржа>/{candles,funding}/<SYMBOL>.csv.gz. Заголовок (первая строка) сохраняется; строки с time_ms >= cut отбрасываются;
сырьё не меняется. Только стандартная библиотека.
"""
import gzip
import os
import sys

G0_MS = 1_577_836_800_000


def cut(src, dst, cut_ms):
    if not os.path.exists(src):
        return None
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    kept = 0
    with gzip.open(src, "rt", encoding="utf-8") as r, gzip.open(dst, "wt", encoding="utf-8", newline="") as w:
        for i, line in enumerate(r):
            head = line.split(",", 1)[0].strip()
            if i == 0 and not head.lstrip("-").isdigit():
                w.write(line)  # заголовок
                continue
            if head and int(head) < cut_ms:
                w.write(line)
                kept += 1
    return kept


def main():
    if len(sys.argv) < 5:
        raise SystemExit(__doc__)
    root, out, cut_day = sys.argv[1], sys.argv[2], int(sys.argv[3])
    cut_ms = G0_MS + cut_day * 1440 * 60_000
    for spec in sys.argv[4:]:
        ex, sym = spec.split(":", 1)
        for kind in ("candles", "funding"):
            n = cut(os.path.join(root, ex, kind, sym + ".csv.gz"), os.path.join(out, ex, kind, sym + ".csv.gz"), cut_ms)
            print(f"{ex}/{kind}/{sym}: {'нет файла' if n is None else str(n) + ' строк до суток ' + str(cut_day)}")


if __name__ == "__main__":
    main()
