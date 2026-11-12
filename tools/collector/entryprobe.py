#!/usr/bin/env python3
"""TK-532: проба независимых входов биржи (пассивная, замер, не приёмочное окно).
Запуск (долгое — только systemd-run):
  entryprobe.py --adapter bybit_ws --root /data/bbws-entry --pool-file /data/bbws/pool-2026-11-12.json --kinds trade,mark --max-s 21600 \\
      --entrance com=stream.bybit.com --entrance fra=stream.bybit.com@13.226.244.52 --entrance kz=stream.bybit.kz
Вход = ИМЯ=хост[@ip]: хост — имя и SNI, ip — подключиться к этому адресу (другая точка присутствия CDN при том же имени). По процессу на вход.
Разбор — entrycmp.py.
"""
import argparse
import asyncio
import importlib
import json
import multiprocessing as mp
import signal
import sys
from urllib.parse import urlsplit

from wscore.entry import run_entrance


def child(adapter_mod, name, host, ip, pool, kinds, per_conn, root, max_s):
    adapter = importlib.import_module(adapter_mod).Adapter()
    url = urlsplit(adapter.url(None))._replace(netloc=host).geturl()
    shards = adapter.plan(pool, kinds, per_conn)

    async def amain():
        task = asyncio.current_task()
        asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, task.cancel)
        await run_entrance(adapter, name, url, ip, shards, root, max_s)

    asyncio.run(amain())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--adapter", default="bybit_ws")
    ap.add_argument("--root", required=True)
    ap.add_argument("--pool-file")
    ap.add_argument("--pool", help="символы через запятую (вместо --pool-file)")
    ap.add_argument("--kinds", default="trade,mark")
    ap.add_argument("--per-conn", type=int)
    ap.add_argument("--max-s", type=int, default=21600)
    ap.add_argument("--entrance", action="append", required=True)
    a = ap.parse_args()
    mod = importlib.import_module(a.adapter)
    pool = a.pool.split(",") if a.pool else json.load(open(a.pool_file))["pool"]
    per_conn = a.per_conn or mod.Adapter.streams_per_conn
    procs = []
    for spec in a.entrance:
        name, _, rest = spec.partition("=")
        host, _, ip = rest.partition("@")
        procs.append(mp.Process(target=child, args=(a.adapter, name, host, ip or None, pool, a.kinds.split(","), per_conn, a.root, a.max_s), name=name))
    for p in procs:
        p.start()
    signal.signal(signal.SIGTERM, lambda *_: [p.terminate() for p in procs])
    print(f"entrances {len(procs)} pool {len(pool)} kinds {a.kinds} per_conn {per_conn} max_s {a.max_s}", flush=True)
    for p in procs:
        p.join()


if __name__ == "__main__":
    sys.exit(main())
