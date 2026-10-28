#!/usr/bin/env python3
"""TK-411: свечи 1 м Bybit (REST /v5/market/kline) за 2026-09: все инструменты linear (Trading+Closed+PreLaunch) и spot.
Выход: <root>/candles/bybit-{linear,spot}/СИМВОЛ.csv.gz (t_open_ms,o,h,l,c,v,quote_v,n_trades; n_trades пуст — API его не отдаёт),
охват: <root>/cov/bybit-{linear,spot}-СИМВОЛ.tsv «рынок символ минут первая последняя окно_мин запросов», список — cov/bybit-instruments.tsv.
Параллельность — по лимитам источника (CEO 26.10): THREADS=16, общий ограничитель ≤RPS запр/с (лимит IP 600/5 с), бэкофф на 403/10006/сеть.
Готовое пропускается. Использование: tk411-bybit-kline.py <root> [threads] [rps]
"""
import gzip
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

START = int(os.environ.get("K_START", 1788220800000))  # 2026-09-01 00:00 UTC; август (TK-423): K_START=1785542400000
END = int(os.environ.get("K_END", 1790812800000))  # 2026-10-01 00:00 UTC (исключая); август: K_END=1788220800000
BASE = "https://api.bybit.com/v5/market/"

lock = threading.Lock()
next_slot = 0.0
pause_until = 0.0
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
            if d.get("retCode") == 10006:
                with lock:
                    pause_until = max(pause_until, time.monotonic() + 5 * (attempt + 1))
                continue
            if d.get("retCode") == 10016:  # «svc error: Get kline failed» — сбой сервиса Bybit, повторяем (TK-411: 253 символа linear упали)
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


def instruments(cat, rps):
    out = {}
    for status in ("Trading", "Closed", "PreLaunch") if cat == "linear" else (None,):
        cursor = ""
        while True:
            q = "instruments-info?category=%s&limit=1000" % cat
            if status:
                q += "&status=" + status
            if cursor:
                q += "&cursor=" + cursor
            r = get(q, rps)
            for it in r["list"]:
                out[it["symbol"]] = (int(it.get("launchTime") or 0), int(it.get("deliveryTime") or 0), it.get("status", ""))
            cursor = r.get("nextPageCursor") or ""
            if not cursor:
                break
    return out


def one(cat, sym, root, rps):
    dst = "%s/candles/bybit-%s/%s.csv.gz" % (root, cat, sym)
    if os.path.exists(dst):
        return
    rows = {}
    end = END - 1
    n = 0
    while end >= START:
        r = get("kline?category=%s&symbol=%s&interval=1&start=%d&end=%d&limit=1000" % (cat, sym, START, end), rps)
        n += 1
        lst = r["list"]
        if not lst:
            break
        for k in lst:
            t = int(k[0])
            if START <= t < END:
                rows[t] = k
        oldest = min(int(k[0]) for k in lst)
        if oldest >= end:
            break
        end = oldest - 1
    ts = sorted(rows)
    with gzip.open(dst + ".part", "wt", encoding="ascii", compresslevel=6, newline="") as g:
        g.write("t_open_ms,o,h,l,c,v,quote_v,n_trades\n")
        for t in ts:
            k = rows[t]
            g.write("%d,%s,%s,%s,%s,%s,%s,\n" % (t, k[1], k[2], k[3], k[4], k[5], k[6]))
    os.replace(dst + ".part", dst)
    with open("%s/cov/bybit-%s-%s.tsv" % (root, cat, sym), "w") as c:
        c.write("%s\t%s\t%d\t%s\t%s\t%d\n" % (cat, sym, len(ts), ts[0] if ts else "", ts[-1] if ts else "", n))


def main():
    root = sys.argv[1]
    threads = int(sys.argv[2]) if len(sys.argv) > 2 else 16
    rps = float(sys.argv[3]) if len(sys.argv) > 3 else 80.0
    for cat in ("linear", "spot"):
        os.makedirs("%s/candles/bybit-%s" % (root, cat), exist_ok=True)
    os.makedirs(root + "/cov", exist_ok=True)
    jobs = []
    only = set(os.environ.get("TK411_ONLY", "").split(",")) - {""}  # проба на нескольких символах
    klist = os.environ.get("K_LIST")  # TK-428: свой список «рынок<TAB>символ» (linear|spot|inverse), без instruments-info (снятые с торгов)
    if klist:
        with open(klist, encoding="utf-8") as f:
            jobs = [tuple(ln.split()[:2]) for ln in f if len(ln.split()) >= 2 and ln.split()[0] in ("linear", "spot", "inverse")]
        for cat in {c for c, _ in jobs}:
            os.makedirs("%s/candles/bybit-%s" % (root, cat), exist_ok=True)
    with open(root + "/cov/bybit-instruments.tsv", "w") if not klist else open(os.devnull, "w") as f:
        f.write("рынок\tсимвол\tlaunch_ms\tdelivery_ms\tstatus\tв_окне\n")
        for cat in ("linear", "spot"):
            for sym, (la, de, st) in sorted(instruments(cat, rps).items() if not klist else ()):
                inwin = la < END and (de == 0 or de >= START)
                f.write("%s\t%s\t%d\t%d\t%s\t%d\n" % (cat, sym, la, de, st, inwin))
                if inwin and (not only or sym in only):
                    jobs.append((cat, sym))
    print("инструментов к запросу: linear=%d spot=%d" % (sum(1 for j in jobs if j[0] == "linear"), sum(1 for j in jobs if j[0] == "spot")), flush=True)
    t0 = time.time()
    done = 0
    fails = []
    with ThreadPoolExecutor(threads) as ex:
        futs = {ex.submit(one, c, s, root, rps): (c, s) for c, s in jobs}
        for fu in futs:
            try:
                fu.result()
            except Exception as e:  # noqa: BLE001
                fails.append((futs[fu], str(e)))
            done += 1
            if done % 100 == 0:
                print("%d/%d, %.0f с, запросов %d" % (done, len(jobs), time.time() - t0, reqs), flush=True)
    print("готово: %d/%d, провалов %d, запросов %d, %.0f с" % (done - len(fails), len(jobs), len(fails), reqs, time.time() - t0), flush=True)
    for f_ in fails:
        print("FAIL", f_, flush=True)
    sys.exit(1 if fails else 0)


main()
