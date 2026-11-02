#!/usr/bin/env python3
"""TK-483 (П-32 Д-1), копия tk476-bybit-hist.py: category=spot; retCode 10001 («символ не поддержан») = пусто, не провал.
Полная история минутных свечей Bybit spot (REST /v5/market/kline, 1000 свечей/запрос) для пула > 1 млн/сут.
Идём от END назад до листинга (пустой ответ); START — 2020-01-01. Выход: <out>/bybit-spot/СИМВОЛ.csv.gz (формат как у лежащих:
t_open_ms,o,h,l,c,v,quote_v,n_trades; n_trades пуст — API не отдаёт) и <out>/cov/СИМВОЛ.tsv. Готовое пропускается.
usage: tk476-bybit-hist.py <out> <symbols.txt> [threads=16] [rps=80]   (END = 00:00 UTC сегодняшних суток, env K_END=мс)"""
import gzip, os, sys, threading, time, json, urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor

START = int(os.environ.get("K_START", 1577836800000))  # 2020-01-01 00:00 UTC
END = int(os.environ.get("K_END", int(time.time() // 86400 * 86400 * 1000)))
BASE = "https://api.bybit.com/v5/market/"
lock = threading.Lock()
next_slot = pause_until = 0.0
reqs = 0


def throttle(rps):
    global next_slot
    while True:
        with lock:
            now = time.monotonic()
            wait = max(next_slot - now, pause_until - now)
            if wait <= 0:
                next_slot = now + 1.0 / rps
                return
        time.sleep(min(wait, 1.0))


def get(path, rps):
    global pause_until, reqs
    for attempt in range(12):
        throttle(rps)
        try:
            with lock:
                reqs += 1
            with urllib.request.urlopen(BASE + path, timeout=30) as r:
                d = json.load(r)
            if d.get("retCode") == 0:
                return d["result"]
            if d.get("retCode") == 10001:
                return {"list": []}
            if d.get("retCode") == 10006:
                with lock:
                    pause_until = max(pause_until, time.monotonic() + 5 * (attempt + 1))
                continue
            if d.get("retCode") == 10016:
                time.sleep(3 * (attempt + 1))
                continue
            raise RuntimeError("bybit retCode %s %s" % (d.get("retCode"), d.get("retMsg")))
        except urllib.error.HTTPError as e:
            if e.code in (403, 429):
                with lock:
                    pause_until = max(pause_until, time.monotonic() + 30 * (attempt + 1))
                print("HTTP %d, пауза %ds" % (e.code, 30 * (attempt + 1)), file=sys.stderr, flush=True)
                continue
            time.sleep(2 * (attempt + 1))
        except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError):
            time.sleep(2 * (attempt + 1))
    raise RuntimeError("не вышло: " + path)


def one(sym, out, rps):
    dst = "%s/bybit-spot/%s.csv.gz" % (out, sym)
    if os.path.exists(dst):
        return
    pages, end, n, nrows, first, last = [], END - 1, 0, 0, None, None
    while end >= START:
        r = get("kline?category=spot&symbol=%s&interval=1&start=%d&end=%d&limit=1000" % (sym, START, end), rps)
        n += 1
        lst = [k for k in r["list"] if START <= int(k[0]) <= end]
        if not lst:
            break
        lst.sort(key=lambda k: int(k[0]))
        oldest = int(lst[0][0])
        if last is None:
            last = int(lst[-1][0])
        pages.append("".join("%s,%s,%s,%s,%s,%s,%s,\n" % tuple(k[:7]) for k in lst))
        nrows += len(lst); first = oldest
        end = oldest - 1
    with gzip.open(dst + ".part", "wt", encoding="ascii", compresslevel=6, newline="") as g:
        g.write("t_open_ms,o,h,l,c,v,quote_v,n_trades\n")
        for p in reversed(pages):
            g.write(p)
    os.replace(dst + ".part", dst)
    with open("%s/cov/%s.tsv" % (out, sym), "w") as c:
        c.write("%s\t%d\t%s\t%s\t%d\n" % (sym, nrows, first or "", last or "", n))


def main():
    out, syms = sys.argv[1], [s.strip() for s in open(sys.argv[2]) if s.strip()]
    threads = int(sys.argv[3]) if len(sys.argv) > 3 else 16
    rps = float(sys.argv[4]) if len(sys.argv) > 4 else 80.0
    os.makedirs(out + "/bybit-spot", exist_ok=True); os.makedirs(out + "/cov", exist_ok=True)
    print("END", END, time.strftime("%Y-%m-%d %H:%M", time.gmtime(END / 1000)), "UTC; символов", len(syms), flush=True)
    t0, fails = time.time(), []
    with ThreadPoolExecutor(threads) as ex:
        futs = {ex.submit(one, s, out, rps): s for s in syms}
        for i, fu in enumerate(futs, 1):
            try:
                fu.result()
            except Exception as e:  # noqa: BLE001
                fails.append((futs[fu], str(e)))
            if i % 10 == 0:
                print("%d/%d, %.0f с, запросов %d" % (i, len(syms), time.time() - t0, reqs), flush=True)
    print("готово: %d/%d, провалов %d, запросов %d, %.0f с" % (len(syms) - len(fails), len(syms), len(fails), reqs, time.time() - t0), flush=True)
    for f_ in fails:
        print("FAIL", f_, flush=True)
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
