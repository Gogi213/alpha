"""Процесс коллектора: пул биржи, раскладка по шардам, запуск шардов процессами, health.json раз в STATS_PERIOD_S.
Делает: collector_main(adapter) — точка входа адаптера `<биржа>_ws.py`. Не знает биржу (всё через адаптер) и внутренности шарда.
"""
import argparse
import json
import multiprocessing as mp
import os
import signal
import time

from wscore import stats
from wscore.health import write_health
from wscore.shard import shard_main
from wscore.writer import day_of


def parse_args(adapter):
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--health", default=adapter.health_default)
    ap.add_argument("--streams-per-conn", type=int, default=adapter.streams_per_conn)
    ap.add_argument("--min-quote-volume", type=float, default=adapter.min_quote_volume)
    ap.add_argument("--top-exclude", type=int, default=adapter.top_exclude)
    ap.add_argument("--kinds", default=",".join(adapter.kinds))
    ap.add_argument("--pool-only", action="store_true")
    return ap.parse_args()


def collector_main(adapter):
    a = parse_args(adapter)
    os.makedirs(a.root, exist_ok=True)
    pool, top = adapter.load_pool(a.min_quote_volume, a.top_exclude)
    with open(os.path.join(a.root, f"pool-{day_of(time.time())}.json"), "w") as f:
        json.dump({"ts_ms": int(time.time() * 1000), "min_quote_volume": a.min_quote_volume, "top_excluded": top, "pool": pool}, f)
    print(f"pool {len(pool)} top_excluded {top}", flush=True)
    if a.pool_only:
        return
    shards = adapter.plan(pool, a.kinds.split(","), a.streams_per_conn)
    procs = [mp.Process(target=shard_main, args=(adapter, a.root, n, e, s), name=n) for n, e, s in shards]
    for p in procs:
        p.start()
    print(f"shards {len(procs)}: " + ", ".join(f"{n}={len(s)}" for n, _, s in shards), flush=True)
    signal.signal(signal.SIGTERM, lambda *_: [p.terminate() for p in procs])  # дети закрывают файлы по SIGTERM
    while any(p.is_alive() for p in procs):
        time.sleep(stats.STATS_PERIOD_S)
        write_health(a.root, a.health, pool, adapter.name)
    for p in procs:
        p.join()
