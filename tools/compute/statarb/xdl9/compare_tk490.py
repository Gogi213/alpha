#!/usr/bin/env python3
"""TK-493 G1 (сверка): свечи xdl против прежней закачки пампов TK-490 по минутам (open, high, low, close, volume).

Только стандартная библиотека. Читает два каталога с файлами `<SYMBOL>.csv.gz` (колонки t_open_ms,o,h,l,c,v,quote_v[,n_trades]):
  --xdl DIR   каталог свечей xdl (…/<биржа>/candles), окно [start, end) уже вырезано закачкой;
  --prev DIR  каталог TK-490 (…/candles/<биржа>), за 2025-01…2026-11-03 — читается только окно.
Символы: пересечение имён файлов; выбор --n символов детерминированный (правило записано здесь, не подбирается):
  мажоры из --majors, если они есть в пересечении и содержат строки в окне, плюс остальные поровну по алфавиту (шаг (m-1)/(k-1)).
Сравнение числовое (float ==), не по тексту: «0.1» и «0.10» равны. Метрики по символу: строк в окне в каждом наборе, общих минут,
минуты только в xdl / только в TK-490, число расхождений open/high/low/close/volume на общих минутах, наибольшее относительное
расхождение объёма, первая расходящаяся минута. Выход: CSV (по символу) и сводная строка в stdout.

usage: compare_tk490.py --xdl DIR --prev DIR --out CSV [--start 2026-10-01] [--end 2026-11-05] [--n 30] [--majors BTCUSDT,ETHUSDT,SOLUSDT]
"""
import argparse, csv, datetime, gzip, os, sys, time


def parse_day(s):
    return int(datetime.datetime.strptime(s[:10], "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)


def load(path, start, end):
    d = {}
    with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
        f.readline()
        for line in f:
            p = line.rstrip("\r\n").split(",")
            if len(p) < 6:
                continue
            try:
                t = int(p[0])
            except ValueError:
                continue
            if t < start or t >= end:
                continue
            try:
                d[t] = (float(p[1]), float(p[2]), float(p[3]), float(p[4]), float(p[5]) if p[5] else None)
            except ValueError:
                d[t] = None
    return d


def pick(names, majors, k, has_rows):
    cand = [n for n in sorted(names) if has_rows(n)]
    chosen = [m for m in majors if m in cand]
    rest = [n for n in cand if n not in chosen]
    need = max(0, k - len(chosen))
    if need and rest:
        step = (len(rest) - 1) / (need - 1) if need > 1 else 0
        idx = sorted({round(i * step) for i in range(need)}) if need > 1 else [0]
        chosen += [rest[i] for i in idx]
        # если округление схлопнуло индексы — добираем следующими по алфавиту
        j = 0
        while len(chosen) < k and j < len(rest):
            if rest[j] not in chosen:
                chosen.append(rest[j])
            j += 1
    return chosen[:k]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--xdl", required=True)
    ap.add_argument("--prev", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--start", default="2026-10-01")
    ap.add_argument("--end", default="2026-11-05")
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--majors", default="BTCUSDT,ETHUSDT,SOLUSDT")
    a = ap.parse_args()
    start, end = parse_day(a.start), parse_day(a.end)
    x_names = {f[:-7] for f in os.listdir(a.xdl) if f.endswith(".csv.gz")}
    p_names = {f[:-7] for f in os.listdir(a.prev) if f.endswith(".csv.gz")}
    both = x_names & p_names
    print("xdl файлов %d, TK-490 файлов %d, общих имён %d" % (len(x_names), len(p_names), len(both)), flush=True)
    # «в окне есть строки» проверяем по размеру файла xdl (пустое окно = только заголовок, ≈ 100 байт)
    chosen = pick(both, [m for m in a.majors.split(",") if m], a.n, lambda n: os.path.getsize(os.path.join(a.xdl, n + ".csv.gz")) > 200)
    cols = ["symbol", "rows_xdl", "rows_prev", "common", "only_xdl", "only_prev", "diff_o", "diff_h", "diff_l", "diff_c", "diff_v", "max_rel_diff_v",
            "first_diff_t", "first_diff_what"]
    tot = dict.fromkeys(cols[1:11], 0)
    t0 = time.time()
    with open(a.out, "w", encoding="utf-8", newline="") as fo:
        w = csv.writer(fo)
        w.writerow(cols)
        for n in chosen:
            X = load(os.path.join(a.xdl, n + ".csv.gz"), start, end)
            P = load(os.path.join(a.prev, n + ".csv.gz"), start, end)
            common = sorted(set(X) & set(P))
            diff = [0] * 5
            maxrel = 0.0
            first = ("", "")
            for t in common:
                x, p = X[t], P[t]
                if x is None or p is None:
                    diff[4] += 1
                    continue
                for k in range(5):
                    if x[k] != p[k]:
                        diff[k] += 1
                        if not first[0]:
                            first = (t, "%s x=%r p=%r" % ("ohlcv"[k], x[k], p[k]))
                        if k == 4 and x[4] is not None and p[4] is not None:
                            m = max(abs(x[4]), abs(p[4]), 1e-300)
                            maxrel = max(maxrel, abs(x[4] - p[4]) / m)
            row = [n, len(X), len(P), len(common), len(set(X) - set(P)), len(set(P) - set(X))] + diff + ["%.3e" % maxrel, first[0], first[1]]
            w.writerow(row)
            for i, c in enumerate(cols[1:11]):
                tot[c] += row[1 + i]
            print("%-22s xdl=%d prev=%d common=%d only_xdl=%d only_prev=%d diff(o,h,l,c,v)=%s" % (n, len(X), len(P), len(common), row[4], row[5], diff), flush=True)
    print("ИТОГО символов %d: %s (%.0f с)" % (len(chosen), tot, time.time() - t0))


if __name__ == "__main__":
    main()
