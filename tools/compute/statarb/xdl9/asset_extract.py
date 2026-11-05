#!/usr/bin/env python3
"""TK-493 F5, стадия 1 (Steam Deck, только stdlib): компактные ряды закрытий ФОРМИРОВАНИЯ 01.10-14.10 UTC для таблицы «один актив».

Читает `<root>/<биржа>/candles/<SYM>.csv.gz` (формат xdl: t_open_ms,o,h,l,c,v,quote_v) только до конца окна формирования
[2026-10-01 00:00, 2026-10-15 00:00) UTC = 20 160 минут; торговое окно (с 15.10) не читается и не попадает в выход.
Выход на биржу: `<out>/<биржа>.f32` (N x 20160 float32 little-endian, NaN = нет бара ИЛИ v=0 - правило G3) и
`<out>/<биржа>.idx.csv` (строка массива, символ, статистики формирования).

Статистики по контракту: n_bars (строки в окне), n_valid (бар есть и v>0 и c>0), qv_rows (строк с непустым quote_v), turnover_qv
(Σ quote_v по строкам с v>0), turnover_cv (Σ c·v по валидным), turnover (Σ quote_v, если у биржи quote_v есть в этом файле, иначе Σ c·v),
price_med (медиана закрытий валидных минут), first_valid/last_valid (индекс минуты формирования).
Ничего не выдумывается; торговое окно не открывается.

  nice -n 19 python3 asset_extract.py --root ~/xdl/out --out ~/xdl9/asset --exchanges binance,bybit,okx,... --procs 3
"""
import argparse, array, csv, gzip, math, multiprocessing, os, sys, time

MIN_MS = 60_000
START_MS = 1790812800000                 # 2026-10-01 00:00:00 UTC
N_MIN = 14 * 1440                        # 20 160 минут формирования
END_MS = START_MS + N_MIN * MIN_MS       # 2026-10-15 00:00:00 UTC (исключительно)
NAN = float("nan")
EXCH = ["binance", "bybit", "okx", "bingx", "aster", "kraken", "lighter", "bitget"]


def read_one(args):
    root, ex, fname = args
    path = os.path.join(root, ex, "candles", fname)
    arr = array.array("f", [NAN]) * N_MIN
    n_bars = n_valid = qv_rows = 0
    t_qv = t_cv = 0.0
    closes = []
    first_valid = last_valid = -1
    err = ""
    try:
        with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
            f.readline()
            for line in f:
                p = line.rstrip("\n").split(",")
                if len(p) < 6:
                    continue
                try:
                    t = int(p[0])
                except ValueError:
                    continue
                if t >= END_MS:
                    break
                if t < START_MS or (t - START_MS) % MIN_MS:
                    continue
                i = (t - START_MS) // MIN_MS
                n_bars += 1
                try:
                    c = float(p[4]); v = float(p[5])
                except ValueError:
                    continue
                qv = None
                if len(p) > 6 and p[6] != "":
                    try:
                        qv = float(p[6])
                    except ValueError:
                        qv = None
                if v > 0 and c > 0 and c == c:
                    arr[i] = c
                    n_valid += 1
                    t_cv += c * v
                    closes.append(c)
                    if qv is not None:
                        qv_rows += 1
                        t_qv += qv
                    if first_valid < 0:
                        first_valid = i
                    last_valid = i
    except Exception as e:  # noqa
        err = repr(e)[:120]
    closes.sort()
    med = closes[len(closes) // 2] if closes else NAN
    turnover = t_qv if qv_rows > 0 else t_cv
    return (fname, arr.tobytes(), dict(n_bars=n_bars, n_valid=n_valid, qv_rows=qv_rows, turnover_qv=t_qv, turnover_cv=t_cv,
                                       turnover=turnover, price_med=med, first_valid=first_valid, last_valid=last_valid, err=err))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--exchanges", default=",".join(EXCH))
    ap.add_argument("--procs", type=int, default=3)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    for ex in a.exchanges.split(","):
        t0 = time.time()
        d = os.path.join(a.root, ex, "candles")
        names = sorted(x for x in os.listdir(d) if x.endswith(".csv.gz"))
        if a.limit:
            names = names[:a.limit]
        with multiprocessing.Pool(a.procs) as pool:
            res = list(pool.imap(read_one, [(a.root, ex, n) for n in names], chunksize=4))
        with open(os.path.join(a.out, ex + ".f32.tmp"), "wb") as fb, \
                open(os.path.join(a.out, ex + ".idx.csv.tmp"), "w", encoding="utf-8", newline="") as fi:
            w = csv.writer(fi)
            w.writerow(["row", "file", "n_bars", "n_valid", "qv_rows", "turnover_qv", "turnover_cv", "turnover", "price_med",
                        "first_valid", "last_valid", "err"])
            for r, (fname, raw, st) in enumerate(res):
                fb.write(raw)
                w.writerow([r, fname, st["n_bars"], st["n_valid"], st["qv_rows"], repr(st["turnover_qv"]), repr(st["turnover_cv"]),
                            repr(st["turnover"]), repr(st["price_med"]), st["first_valid"], st["last_valid"], st["err"]])
        os.replace(os.path.join(a.out, ex + ".f32.tmp"), os.path.join(a.out, ex + ".f32"))
        os.replace(os.path.join(a.out, ex + ".idx.csv.tmp"), os.path.join(a.out, ex + ".idx.csv"))
        print(ex, len(names), "files", "%.0f s" % (time.time() - t0), flush=True)


if __name__ == "__main__":
    main()
