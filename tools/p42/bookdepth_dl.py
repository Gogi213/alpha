#!/usr/bin/env python3
"""П-42 §13.21 (TK-595): докачка Binance USD-M `bookDepth` (дневные архивы data.binance.vision) на srv.

  python3 bookdepth_dl.py <symbols.txt> <день_с YYYY-MM-DD> <день_по YYYY-MM-DD> [--out /data/p42-book] [--workers 6] [--min-free-gb 45]
        [--extra-days YYYY-MM-DD,YYYY-MM-DD,...]

symbols.txt — через запятую или по строке. Файл: <out>/zip/<SYM>/<SYM>-bookDepth-<день>.zip (уже скачанные пропускаются).
Охват: <out>/coverage-<тег>.csv (sym,day,status,bytes; status = ok | 404 | err:<код>) — по монете и суткам.
Сторож места: пока свободного на томе `out` меньше --min-free-gb — останавливается (код 3), ничего не удаляет.
Запуск — `systemd-run` (nice 19, idle IO), не висящая ssh-сессия.
"""
import argparse
import concurrent.futures as cf
import datetime as dt
import os
import shutil
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://data.binance.vision/data/futures/um/daily/bookDepth"


def days(a, b):
    d0, d1 = dt.date.fromisoformat(a), dt.date.fromisoformat(b)
    return [(d0 + dt.timedelta(n)).isoformat() for n in range((d1 - d0).days + 1)]


def fetch(out, sym, day, retries=3):
    name = "%s-bookDepth-%s.zip" % (sym, day)
    path = os.path.join(out, "zip", sym, name)
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return sym, day, "ok", os.path.getsize(path)
    url = "%s/%s/%s" % (BASE, urllib.parse.quote(sym), urllib.parse.quote(name))
    err = "err"
    for k in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                data = r.read()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".part"
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, path)
            return sym, day, "ok", len(data)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return sym, day, "404", 0
            err = "err:%d" % e.code
        except Exception as e:  # сеть, таймаут
            err = "err:%s" % type(e).__name__
    return sym, day, err, 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("symbols")
    ap.add_argument("d0")
    ap.add_argument("d1")
    ap.add_argument("--out", default="/data/p42-book")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--min-free-gb", type=float, default=45)
    ap.add_argument("--extra-days", default="")
    a = ap.parse_args()
    syms = [s.strip() for s in open(a.symbols, encoding="utf-8").read().replace("\n", ",").split(",") if s.strip()]
    ds = days(a.d0, a.d1) + [d for d in a.extra_days.split(",") if d]
    os.makedirs(os.path.join(a.out, "zip"), exist_ok=True)
    tag = "%s_%s" % (a.d0, a.d1)
    cov_path = os.path.join(a.out, "coverage-%s.csv" % tag)
    jobs = [(s, d) for d in ds for s in syms]
    lock = threading.Lock()
    stop = threading.Event()
    n_ok = n_404 = n_err = 0
    with open(cov_path, "w", encoding="utf-8") as cov, cf.ThreadPoolExecutor(a.workers) as ex:
        cov.write("sym,day,status,bytes\n")
        futs = []
        for s, d in jobs:
            def run(s=s, d=d):
                if stop.is_set():
                    return None
                if shutil.disk_usage(a.out).free < a.min_free_gb * 1e9:
                    stop.set()
                    return None
                return fetch(a.out, s, d)
            futs.append(ex.submit(run))
        for i, f in enumerate(cf.as_completed(futs), 1):
            r = f.result()
            if r is None:
                continue
            with lock:
                cov.write("%s,%s,%s,%d\n" % r)
                if r[2] == "ok":
                    n_ok += 1
                elif r[2] == "404":
                    n_404 += 1
                else:
                    n_err += 1
                if i % 500 == 0:
                    cov.flush()
                    print("%d/%d ok=%d 404=%d err=%d" % (i, len(jobs), n_ok, n_404, n_err), flush=True)
    print("DONE jobs=%d ok=%d 404=%d err=%d stopped_by_disk=%s" % (len(jobs), n_ok, n_404, n_err, stop.is_set()), flush=True)
    return 3 if stop.is_set() else 0


if __name__ == "__main__":
    sys.exit(main())
