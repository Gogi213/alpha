#!/usr/bin/env python3
"""TK-522 (CEO 10.11 17:02, Судья 16:58): предвыборка L1 ног с бокса на диск srv, пока бой считает. Бой (orch.py с P40_L1_CACHE=<кэш>) берёт сутки ноги с диска, если она там.
Запуск: prefetch_l1.py <pairs.csv> <out боя> <кэш> --days d1,d2,.. [--threads 16] [--skip-first 14] [--order asset|day] [--min-free-gb 35]
Берёт активы без out/<актив>/<cid>/DONE в порядке pairs.csv (= порядок очереди боя), ноги×сутки без записи в кэше. Сутки качаются в <кэш>/<rel>.part и переименовываются в <кэш>/<rel> целиком
(есть `.ok` -> каталог полный); нет суток на боксе (rsync rc 23) — не кэшируется, бой возьмёт свой путь и сам решит (L1Missing). rc 12/255 (бокс закрыл соединение) — повтор с паузой.
Кэш не чистится: после D1 L1 нужны D2 и K-прогонам; стоп по месту — свободно < --min-free-gb на разделе кэша."""
import argparse, concurrent.futures as cf, csv, os, shutil, subprocess, sys, threading, time

SSH = "ssh -c aes128-gcm@openssh.com -o Compression=no"
SRC = os.environ.get("P40_L1_SRC", "box:alpha/stakan")
EXDIR = {ex: f"{ex}_futures" for ex in ("binance", "okx", "bitget", "aster", "hyperliquid", "bybit", "gate")}
RETRY_RC = (12, 255)


def rel(ex, sym, day):
    return f"p40l1/{EXDIR[ex]}/{sym}/{day}"


def queue(pairs, out, cid, skip_first, days):
    by_asset = {}
    for r in csv.DictReader(open(pairs)):
        by_asset.setdefault(r["asset"], []).append(r)
    todo = [a for a in by_asset if not os.path.exists(f"{out}/{a}/{cid}/DONE")][skip_first:]
    legs = []
    for a in todo:
        seen = set()
        for r in by_asset[a]:
            for s in "AB":
                k = (r["ex" + s], r["sym" + s])
                if k not in seen:
                    seen.add(k)
                    legs.append((a,) + k)
    return [(a, ex, sym, d) for (a, ex, sym) in legs for d in days]


def fetch_one(cache, ex, sym, day, stat):
    r = rel(ex, sym, day)
    fin, part = f"{cache}/{r}", f"{cache}/{r}.part"
    if os.path.exists(f"{fin}/.ok"):
        return 0
    shutil.rmtree(part, ignore_errors=True)
    os.makedirs(part, exist_ok=True)
    rc = 255
    for k in range(4):
        rc = subprocess.call(["rsync", "-q", "-a", "-e", SSH, f"{SRC}/{r}/", part + "/"])
        if rc not in RETRY_RC:
            break
        time.sleep(5 * (k + 1))
    if rc == 0 and os.path.exists(f"{part}/.ok"):
        shutil.rmtree(fin, ignore_errors=True)
        os.rename(part, fin)
        stat["ok"] += 1
        stat["bytes"] += sum(os.path.getsize(f"{fin}/{f}") for f in os.listdir(fin))
        return 0
    shutil.rmtree(part, ignore_errors=True)
    stat["miss" if rc == 23 else "fail"] += 1
    return rc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pairs"), ap.add_argument("out"), ap.add_argument("cache")
    ap.add_argument("--days", required=True)
    ap.add_argument("--threads", type=int, default=16)
    ap.add_argument("--skip-first", type=int, default=14, help="первые N невыполненных активов бой берёт сам (в работе на старте)")
    ap.add_argument("--order", choices=("asset", "day"), default="asset")
    ap.add_argument("--min-free-gb", type=float, default=35.0)
    ap.add_argument("--limit", type=int, default=0, help="не больше N суток ноги (для замера)")
    a = ap.parse_args()
    days = a.days.split(",")
    cid = f"{days[0]}+{len(days)}"
    os.makedirs(a.cache, exist_ok=True)
    q = queue(a.pairs, a.out, cid, a.skip_first, days)
    if a.order == "day":
        q = sorted(q, key=lambda t: t[3])   # устойчивая: внутри суток порядок очереди боя
    if a.limit:
        q = q[:a.limit]
    stat = {"ok": 0, "miss": 0, "fail": 0, "bytes": 0}
    t0, lock = time.time(), threading.Lock()
    print(f"задач {len(q)}, потоков {a.threads}, порядок {a.order}", flush=True)

    def work(t):
        _, ex, sym, day = t
        while shutil.disk_usage(a.cache).free < a.min_free_gb * 2**30:
            time.sleep(30)
        fetch_one(a.cache, ex, sym, day, stat)
        with lock:
            n = stat["ok"] + stat["miss"] + stat["fail"]
            if n % 50 == 0:
                el = time.time() - t0
                print(f"{time.strftime('%H:%M:%S')} {n}/{len(q)} ok {stat['ok']} miss {stat['miss']} fail {stat['fail']} {stat['bytes'] / 2**20:.0f} МиБ, {stat['bytes'] / 2**20 / el:.1f} МиБ/с, {n / el * 3600:.0f} нога-суток/ч", flush=True)

    with cf.ThreadPoolExecutor(a.threads) as ex:
        list(ex.map(work, q))
    el = time.time() - t0
    print(f"ГОТОВО {len(q)} задач за {el:.0f} с: ok {stat['ok']} miss {stat['miss']} fail {stat['fail']} {stat['bytes'] / 2**20:.0f} МиБ", flush=True)
    open(f"{a.cache}/PREFETCH_DONE", "w").write(time.strftime("%F %T") + chr(10))


if __name__ == "__main__":
    main()
