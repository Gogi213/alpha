#!/usr/bin/env python3
"""TK-415: открытый интерес и фандинг за 2026-09 по публичному API (как отдаёт биржа, без агрегатов).
  bybit : /v5/market/open-interest (intervalTime=5min, самый мелкий шаг истории) + /v5/market/funding/history, linear.
  gate  : /futures/usdt/contract_stats (interval=GATE_INTERVAL, по умолчанию 5m: OI, long/short, ликвидации liq_*) + /futures/usdt/funding_rate.
Список — охват свечей TK-411 (строки с минут_с_данными>0). Выход: <root>/<биржа>/{oi,funding}/СИМВОЛ.csv.gz, охват
<root>/cov/<биржа>-<вид>-СИМВОЛ.tsv «вид символ строк первая_мс последняя_мс запросов примечание». Готовое пропускается.
Параллельность — по лимитам источника: Bybit ≤100 запр/с (лимит IP 600/5 с), Gate ≤15 запр/с на эндпоинт (лимит 200/10 с).
Использование: tk415-oi-funding.py <bybit|gate> <root> <охват свечей csv> [потоков]
"""
import csv
import gzip
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

START = int(os.environ.get("K_START", 1788220800000))  # 2026-09-01 00:00 UTC, мс; август (TK-425): K_START=1785542400000
END = int(os.environ.get("K_END", 1790812800000))  # 2026-10-01 00:00 UTC (исключая); август: K_END=1788220800000
GATE_INTERVAL = os.environ.get("GATE_INTERVAL", "5m")
GATE_STEP = {"1m": 60, "5m": 300}[GATE_INTERVAL]

lock = threading.Lock()
slots = {}
pause_until = {}
reqs = 0


def throttle(key, rps):
    while True:
        with lock:
            now = time.monotonic()
            wait = max(slots.get(key, 0.0) - now, pause_until.get(key, 0.0) - now)
            if wait <= 0:
                slots[key] = now + 1.0 / rps
                return
        time.sleep(min(wait, 1.0))


def fetch(url, key, rps, ok):
    """ok(json) -> (годен, повторять?). Возвращает json; сетевые сбои/429/403 — бэкофф."""
    global reqs
    for attempt in range(12):
        throttle(key, rps)
        try:
            with lock:
                reqs += 1
            with urllib.request.urlopen(url, timeout=30) as r:
                d = json.load(r)
            good, retry = ok(d)
            if good:
                return d
            if retry:
                with lock:
                    pause_until[key] = max(pause_until.get(key, 0.0), time.monotonic() + 3 * (attempt + 1))
                continue
            return d
        except urllib.error.HTTPError as e:
            if e.code in (403, 429):
                with lock:
                    pause_until[key] = max(pause_until.get(key, 0.0), time.monotonic() + 15 * (attempt + 1))
                print("HTTP %d %s, пауза %ds" % (e.code, key, 15 * (attempt + 1)), file=sys.stderr, flush=True)
                continue
            if 400 <= e.code < 500:
                try:
                    return {"_http": e.code, "_body": e.read().decode("utf-8", "replace")[:200]}
                except Exception:
                    return {"_http": e.code}
            time.sleep(2 * (attempt + 1))
        except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError):
            time.sleep(2 * (attempt + 1))
    raise RuntimeError("не вышло: " + url)


def bybit_ok(d):
    rc = d.get("retCode")
    if rc == 0:
        return True, False
    if rc in (10006, 10016):  # лимит / сбой сервиса — повторяем
        return False, True
    return False, False


def save(root, ex, kind, sym, header, rows, nreq, note):
    d = "%s/%s/%s" % (root, ex, kind)
    os.makedirs(d, exist_ok=True)
    dst = "%s/%s.csv.gz" % (d, sym)
    with gzip.open(dst + ".part%d" % os.getpid(), "wt", encoding="ascii", compresslevel=6, newline="") as g:
        g.write(",".join(header) + "\n")
        for r in rows:
            g.write(",".join(str(x) for x in r) + "\n")
    os.replace(dst + ".part%d" % os.getpid(), dst)
    os.makedirs(root + "/cov", exist_ok=True)
    with open("%s/cov/%s-%s-%s.tsv" % (root, ex, kind, sym), "w", encoding="utf-8") as c:
        c.write("%s\t%s\t%d\t%s\t%s\t%d\t%s\n" % (kind, sym, len(rows), rows[0][0] if rows else "", rows[-1][0] if rows else "", nreq, note))


def done(root, ex, kind, sym):
    return os.path.exists("%s/%s/%s/%s.csv.gz" % (root, ex, kind, sym))


def bybit_oi(sym, root, rps):
    if done(root, "bybit", "oi", sym):
        return
    rows, end, n, note = {}, END - 1, 0, ""
    while end >= START:
        u = "https://api.bybit.com/v5/market/open-interest?category=linear&symbol=%s&intervalTime=5min&startTime=%d&endTime=%d&limit=200" % (sym, START, end)
        d = fetch(u, "bybit", rps, bybit_ok)
        n += 1
        if d.get("retCode") != 0:
            note = "retCode %s %s" % (d.get("retCode"), d.get("retMsg") or d.get("_http"))
            break
        lst = d["result"]["list"]
        if not lst:
            break
        for k in lst:
            t = int(k["timestamp"])
            if START <= t < END:
                rows[t] = (t, k["openInterest"], k.get("singleOpenInterest", ""))
        oldest = min(int(k["timestamp"]) for k in lst)
        if oldest >= end:
            break
        end = oldest - 1
    save(root, "bybit", "oi", sym, ["t_ms", "open_interest", "single_open_interest"], [rows[t] for t in sorted(rows)], n, note)


def bybit_funding(sym, root, rps):
    if done(root, "bybit", "funding", sym):
        return
    rows, end, n, note = {}, END - 1, 0, ""
    while end >= START:
        u = "https://api.bybit.com/v5/market/funding/history?category=linear&symbol=%s&startTime=%d&endTime=%d&limit=200" % (sym, START, end)
        d = fetch(u, "bybit", rps, bybit_ok)
        n += 1
        if d.get("retCode") != 0:
            note = "retCode %s %s" % (d.get("retCode"), d.get("retMsg") or d.get("_http"))
            break
        lst = d["result"]["list"]
        if not lst:
            break
        for k in lst:
            t = int(k["fundingRateTimestamp"])
            if START <= t < END:
                rows[t] = (t, k["fundingRate"])
        oldest = min(int(k["fundingRateTimestamp"]) for k in lst)
        if oldest >= end:
            break
        end = oldest - 1
    save(root, "bybit", "funding", sym, ["t_ms", "funding_rate"], [rows[t] for t in sorted(rows)], n, note)


def gate_ok(d):
    return (True, False) if isinstance(d, list) else (False, False)


def gate_stats(sym, root, rps):
    if done(root, "gate", "oi", sym):
        return
    # только from + limit (с to Gate игнорирует from при диапазоне > limit, проба TK-415): страницы вперёд от from
    rows, frm, n, note, header = {}, START // 1000, 0, "", None
    while frm < END // 1000:
        u = "https://api.gateio.ws/api/v4/futures/usdt/contract_stats?contract=%s&from=%d&interval=%s&limit=1000" % (urllib.parse.quote(sym), frm, GATE_INTERVAL)
        d = fetch(u, "gate-stats", rps, gate_ok)
        n += 1
        if not isinstance(d, list):
            note = "http %s %s" % (d.get("_http"), d.get("_body", ""))
            break
        if not d:  # from раньше первой записи контракта (листинг позже начала окна) — пусто; шагаем окном limit
            frm += 1000 * GATE_STEP
            continue
        if header is None:
            header = list(d[0].keys())
        for k in d:
            if START // 1000 <= k["time"] < END // 1000:
                rows[k["time"]] = k
        newest = max(k["time"] for k in d)
        if newest < frm:
            break
        frm = newest + GATE_STEP
    header = header or ["time"]
    out = [[("%d" % (r["time"] * 1000)) if h == "time" else r.get(h, "") for h in header] for r in (rows[t] for t in sorted(rows))]
    header = ["t_ms" if h == "time" else h for h in header]
    save(root, "gate", "oi", sym, header, out, n, note)


def gate_funding(sym, root, rps):
    if done(root, "gate", "funding", sym):
        return
    u = "https://api.gateio.ws/api/v4/futures/usdt/funding_rate?contract=%s&from=%d&to=%d&limit=1000" % (urllib.parse.quote(sym), START // 1000, END // 1000 - 1)
    d = fetch(u, "gate-fund", rps, gate_ok)
    note = ""
    if not isinstance(d, list):
        note = "http %s %s" % (d.get("_http"), d.get("_body", ""))
        d = []
    rows = {}
    for k in d:
        t = int(k["t"]) * 1000
        if START <= t < END:
            rows[t] = (t, k["r"])
    save(root, "gate", "funding", sym, ["t_ms", "funding_rate"], [rows[t] for t in sorted(rows)], 1, note)


def symbols(cov, ex, market):
    out = []
    with open(cov, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["биржа"] == ex and r["рынок"] == market and int(r["минут_с_данными"]) > 0:
                out.append(r["инструмент"])
    return sorted(out)


def main():
    ex, root, cov = sys.argv[1], sys.argv[2], sys.argv[3]
    threads = int(sys.argv[4]) if len(sys.argv) > 4 else 16
    if ex == "bybit":
        syms, jobs, rps = symbols(cov, "bybit", "linear"), (bybit_oi, bybit_funding), 100
    else:
        syms, jobs, rps = symbols(cov, "gate", "futures_usdt"), (gate_stats, gate_funding), 15
    if os.environ.get("REVERSE"):  # второй процесс навстречу первому (быстрее при задержке API)
        syms = syms[::-1]
    print("%s: %d инструментов, потоков %d, rps %d" % (ex, len(syms), threads, rps), flush=True)
    tasks = [(fn, s) for fn in jobs for s in syms]
    left = [len(tasks)]
    t0 = time.time()

    def run(t):
        t[0](t[1], root, rps)
        with lock:
            left[0] -= 1
            if left[0] % 100 == 0:
                print("осталось %d/%d, запросов %d, %.0f с" % (left[0], len(tasks), reqs, time.time() - t0), flush=True)

    with ThreadPoolExecutor(threads) as pool:
        list(pool.map(run, tasks))
    print("готово: запросов %d, %.0f с" % (reqs, time.time() - t0), flush=True)


if __name__ == "__main__":
    main()
