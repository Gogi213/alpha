#!/usr/bin/env python3
"""TK-415: mark / index / premium-index цена за 2026-09 по публичному API (как отдаёт биржа).
  bybit : /v5/market/{mark-price-kline,index-price-kline,premium-index-price-kline}, linear, интервал 1 мин (лимит 1000).
  gate  : /futures/usdt/candlesticks contract=mark_<С> / index_<С>, интервал 15m (1m и 5m за сентябрь Gate не отдаёт: HTTP 400,
          глубина 10 000 точек; 15m = 104 сут.); premium у Gate отдельным рядом нет.
Выход <root>/<биржа>/<mark|index|premium>/СИМВОЛ.csv.gz (Bybit t_ms,o,h,l,c; Gate t_ms,o,h,l,c), охват cov/<биржа>-<вид>-СИМВОЛ.tsv.
Список — охват свечей TK-411. Использование: tk415-mark.py <bybit|gate> <root> <охват свечей csv> [потоков]
"""
import importlib.util
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

spec = importlib.util.spec_from_file_location("of", os.path.join(os.path.dirname(os.path.abspath(__file__)), "tk415-oi-funding.py"))
of = importlib.util.module_from_spec(spec)
spec.loader.exec_module(of)
START, END = of.START, of.END
BYB = {"mark": "mark-price-kline", "index": "index-price-kline", "premium": "premium-index-price-kline"}


def bybit_kline(kind, sym, root, rps):
    if of.done(root, "bybit", kind, sym):
        return
    rows, end, n, note = {}, END - 1, 0, ""
    while end >= START:
        u = "https://api.bybit.com/v5/market/%s?category=linear&symbol=%s&interval=1&start=%d&end=%d&limit=1000" % (BYB[kind], sym, START, end)
        d = of.fetch(u, "bybit", rps, of.bybit_ok)
        n += 1
        if d.get("retCode") != 0:
            note = "retCode %s %s" % (d.get("retCode"), d.get("retMsg") or d.get("_http"))
            break
        lst = d["result"]["list"]
        if not lst:
            break
        for k in lst:
            t = int(k[0])
            if START <= t < END:
                rows[t] = (t, k[1], k[2], k[3], k[4])
        oldest = min(int(k[0]) for k in lst)
        if oldest >= end:
            break
        end = oldest - 1
    of.save(root, "bybit", kind, sym, ["t_ms", "o", "h", "l", "c"], [rows[t] for t in sorted(rows)], n, note)


def gate_kline(kind, sym, root, rps):
    if of.done(root, "gate", kind, sym):
        return
    rows, n, note = {}, 0, ""
    for a, b in ((START // 1000, START // 1000 + 15 * 86400 - 1), (START // 1000 + 15 * 86400, END // 1000 - 1)):
        u = "https://api.gateio.ws/api/v4/futures/usdt/candlesticks?contract=%s_%s&interval=15m&from=%d&to=%d" % (kind, sym, a, b)
        d = of.fetch(u, "gate-mark", rps, of.gate_ok)
        n += 1
        if not isinstance(d, list):
            note = "http %s %s" % (d.get("_http"), d.get("_body", ""))
            break
        for k in d:
            t = int(k["t"]) * 1000
            if START <= t < END:
                rows[t] = (t, k["o"], k["h"], k["l"], k["c"])
    of.save(root, "gate", kind, sym, ["t_ms", "o", "h", "l", "c"], [rows[t] for t in sorted(rows)], n, note)


def main():
    ex, root, cov = sys.argv[1], sys.argv[2], sys.argv[3]
    threads = int(sys.argv[4]) if len(sys.argv) > 4 else 16
    if ex == "bybit":
        syms, rps, jobs = of.symbols(cov, "bybit", "linear"), 100, [(bybit_kline, k) for k in BYB]
    else:
        syms, rps, jobs = of.symbols(cov, "gate", "futures_usdt"), 15, [(gate_kline, k) for k in ("mark", "index")]
    print("%s: %d инструментов, потоков %d, rps %d" % (ex, len(syms), threads, rps), flush=True)
    tasks = [(fn, k, s) for fn, k in jobs for s in syms]
    left, t0 = [len(tasks)], time.time()

    def run(t):
        t[0](t[1], t[2], root, rps)
        with of.lock:
            left[0] -= 1
            if left[0] % 100 == 0:
                print("осталось %d/%d, запросов %d, %.0f с" % (left[0], len(tasks), of.reqs, time.time() - t0), flush=True)

    with ThreadPoolExecutor(threads) as pool:
        list(pool.map(run, tasks))
    print("готово: запросов %d, %.0f с" % (of.reqs, time.time() - t0), flush=True)


if __name__ == "__main__":
    main()
