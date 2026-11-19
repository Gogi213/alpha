#!/usr/bin/env python3
"""TK-621: download the published data.binance.vision daily zips listed in missing.tsv (as published, sha256 vs .CHECKSUM, size vs S3 list).
  python3 tk621_dl.py <list_dir> <out_dir> [threads]
aggTrades -> <out>/aggtrades/<SYM>/<SYM>-aggTrades-<day>.zip (+ .sha256); klines -> <out>/klines/<SYM>/<SYM>-1m-<day>.zip (+ .sha256)
and the unzipped CSV (byte-identical to `unzip -p`) -> <out>/klines-csv/<SYM>/<day>.csv. Resumable: a file with its .sha256 is skipped.
Failures -> <out>/log/miss.tsv (kind, symbol, day, reason); counters -> <out>/log/progress.json."""
import hashlib
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor

BASE = "https://data.binance.vision/data/futures/um/daily"
L, OUT = sys.argv[1], sys.argv[2]
THREADS = int(sys.argv[3]) if len(sys.argv) > 3 else 16
os.makedirs(OUT + "/log", exist_ok=True)
lock = threading.Lock()
cnt = {"done": 0, "skipped": 0, "bytes": 0, "miss": 0, "total": 0}


def get(url, tries=6):
    for a in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=120) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return 404, b""
            err = e.code
        except OSError as e:
            err = str(e)
        time.sleep(1 + 2 * a)
    return -1, str(err).encode()


def one(row):
    kind, sym, day, size, _etag = row
    if kind == "aggTrades":
        rel = f"aggTrades/{sym}/{sym}-aggTrades-{day}.zip"
        dst = f"{OUT}/aggtrades/{sym}/{sym}-aggTrades-{day}.zip"
    else:
        rel = f"klines/{sym}/1m/{sym}-1m-{day}.zip"
        dst = f"{OUT}/klines/{sym}/{sym}-1m-{day}.zip"
    csv_dst = f"{OUT}/klines-csv/{sym}/{day}.csv"
    try:
        if os.path.exists(dst + ".sha256") and os.path.getsize(dst) == int(size) and (kind != "klines" or os.path.exists(csv_dst)):
            with lock:
                cnt["skipped"] += 1
            return
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        c, body = get(f"{BASE}/{rel}")
        if c != 200:
            raise RuntimeError(f"http {c}")
        if len(body) != int(size):
            raise RuntimeError(f"size {len(body)} != listed {size}")
        c, ck = get(f"{BASE}/{rel}.CHECKSUM")
        want = ck.split()[0].decode() if c == 200 and ck else ""
        got = hashlib.sha256(body).hexdigest()
        if want != got:
            raise RuntimeError(f"sha256 {got} != {want or 'no checksum'}")
        with open(dst + ".part", "wb") as f:
            f.write(body)
        os.replace(dst + ".part", dst)
        if kind == "klines":
            with zipfile.ZipFile(dst) as z:
                names = z.namelist()
                if len(names) != 1:
                    raise RuntimeError(f"zip members {names}")
                data = z.read(names[0])
            os.makedirs(os.path.dirname(csv_dst), exist_ok=True)
            with open(csv_dst + ".part", "wb") as f:
                f.write(data)
            os.replace(csv_dst + ".part", csv_dst)
        with open(dst + ".sha256", "w") as f:
            f.write(f"{got}  {os.path.basename(dst)}\n")
        with lock:
            cnt["done"] += 1
            cnt["bytes"] += len(body)
    except Exception as e:  # noqa: BLE001
        with lock:
            cnt["miss"] += 1
            with open(OUT + "/log/miss.tsv", "a") as f:
                f.write(f"{kind}\t{sym}\t{day}\t{e}\n")


def progress():
    while True:
        with lock:
            cnt["ts"] = time.strftime("%FT%TZ", time.gmtime())
            json.dump(cnt, open(OUT + "/log/progress.json", "w"))
        time.sleep(15)


rows = [l.rstrip("\n").split("\t") for l in open(L + "/missing.tsv")]
cnt["total"] = len(rows)
threading.Thread(target=progress, daemon=True).start()
t0 = time.time()
with ThreadPoolExecutor(THREADS) as pool:
    list(pool.map(one, rows))
cnt["ts"] = time.strftime("%FT%TZ", time.gmtime())
json.dump(cnt, open(OUT + "/log/progress.json", "w"))
open(OUT + "/log/done", "w").write(json.dumps(cnt))
print("DONE", cnt, "sec", round(time.time() - t0))
