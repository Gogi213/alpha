#!/usr/bin/env python3
"""G-022 (возврат TK-532 v2, п. 1): сверка `orderbook.1` Bybit через разные входы — одни и те же подписки на всём пуле, сравнение по ключу (символ, u): ts, cts, цены и размеры.
Запуск (долгое — только systemd-run):
  bookcmp.py --root /data/bbws-bookcmp --pool-file /data/bbws/pool-2026-11-12.json --max-s 360 --ref com4=stream.bybit.com@v4 \\
      --entrance kz=stream.bybit.kz --entrance com6=stream.bybit.com@v6 --entrance ge=stream.bybitgeorgia.ge
Вход = ИМЯ=хост[@ip|@v4|@v6] (как у боевых линий, `wscore.dial`). Пассивно, в боевой формат не пишет: итог — `<root>/bookcmp.json` (по каждому входу против опорного).
Сравниваются записи, разобранные боевым адаптером (`Adapter.parse`: b = (символ, ts, cts, u, bid, bsz, ask, asz)), и текст кадра целиком. Окно сравнения — от SKIP_START_S после того, как все входы на связи, до SKIP_END_S до конца:
в начале и конце окна у одной стороны кадр мог начаться раньше другой — такие ключи не считаются «потерянными».
"""
import argparse
import asyncio
import importlib
import json
import os
import signal
import sys
import time

from wscore.dial import dial, hostname, parse_entrance, peer_ip, with_host

SKIP_START_S = 15.0
SKIP_END_S = 5.0
STAGGER_S = 0.4  # пауза между открытиями соединений входа (лимит Bybit — 500 подключений / 5 мин)
FIELDS = ("ts", "cts", "bid", "bsz", "ask", "asz")


class Side:
    """Приёмник одного входа: ключ (символ, u) -> [первый приём мс, [различные записи в порядке прихода], текст первого кадра]."""

    def __init__(self, name):
        self.name = name
        self.keys = {}
        self.frames = self.unparsed = 0
        self.first_ms = None
        self.conns = 0
        self.errors = 0
        self.ips = set()

    def add(self, rx_ms, frame, rec):
        self.frames += 1
        if self.first_ms is None:
            self.first_ms = rx_ms
        s, ts, cts, u, bid, bsz, ask, asz = rec[1], rec[2], rec[3], rec[4], rec[5], rec[6], rec[7], rec[8]
        payload = (ts, cts, bid, bsz, ask, asz)
        slot = self.keys.get((s, u))
        if slot is None:
            self.keys[(s, u)] = [rx_ms, [payload], frame]
        elif payload not in slot[1]:
            slot[1].append(payload)


async def conn(adapter, side, url, ip, shard, streams, t_end):
    import websockets

    backoff = 1.0
    while time.time() < t_end:
        t0 = time.monotonic()
        try:
            sock, rtt = await dial(url, None, ip)
            async with websockets.connect(url, sock=sock, server_hostname=hostname(url), max_size=None, ping_interval=None, compression=None, close_timeout=1) as ws:
                side.conns += 1
                side.ips.add(peer_ip(sock))
                try:
                    for msg in adapter.subscribe_messages(streams):
                        await ws.send(msg)
                        await asyncio.sleep(adapter.sub_period_s)
                    async for frame in ws:
                        rx_ms = time.time_ns() // 1_000_000
                        recs = adapter.parse(frame)
                        if recs and recs[0][0] == "b":
                            side.add(rx_ms, frame, recs[0])
                        elif recs is None and '"topic"' in frame[:20]:
                            side.unparsed += 1
                finally:
                    side.conns -= 1
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — сбой входа считается, соединение открывается заново
            side.errors += 1
        backoff = 1.0 if time.monotonic() - t0 > 60 else min(backoff * 2, 30.0)
        await asyncio.sleep(backoff)


def compare(ref, other, lo_ms, hi_ms):
    """Ключи с первым приёмом хотя бы на одной стороне в [lo, hi]: общие (совпали / различаются по полям), только у опорного, только у другого."""
    out = {"keys_common": 0, "first_payload_equal": 0, "all_payloads_equal": 0, "frame_text_equal": 0, "only_ref": 0, "only_other": 0,
           "mismatch_by_field": dict.fromkeys(FIELDS, 0), "examples": []}
    for k in set(ref.keys) | set(other.keys):
        a, b = ref.keys.get(k), other.keys.get(k)
        t = min(x[0] for x in (a, b) if x is not None)
        if not lo_ms <= t <= hi_ms:
            continue
        if a is None:
            out["only_other"] += 1
            continue
        if b is None:
            out["only_ref"] += 1
            continue
        out["keys_common"] += 1
        out["frame_text_equal"] += a[2] == b[2]
        out["first_payload_equal"] += a[1][0] == b[1][0]
        eq = set(a[1]) == set(b[1])
        out["all_payloads_equal"] += eq
        if not eq or a[1][0] != b[1][0]:
            for i, f in enumerate(FIELDS):
                if {p[i] for p in a[1]} != {p[i] for p in b[1]}:
                    out["mismatch_by_field"][f] += 1
            if len(out["examples"]) < 8:
                out["examples"].append({"sym": k[0], "u": k[1], "ref": a[1], "other": b[1]})
    return out


async def main_async(a):
    adapter = importlib.import_module("bybit_ws").Adapter()
    pool = a.pool.split(",") if a.pool else json.load(open(a.pool_file))["pool"]
    shards = adapter.plan(pool, ["book"], a.per_conn or adapter.streams_per_conn)
    specs = [a.ref] + a.entrance
    sides, tasks = {}, []
    t_start = time.time()
    t_end = t_start + a.max_s
    for spec in specs:
        name, _, rest = spec.partition("=")
        host, ip = parse_entrance(rest)
        url = with_host(adapter.url(None), host)
        sides[name] = side = Side(name)
        for shard, _endpoint, streams in shards:
            tasks.append(asyncio.create_task(conn(adapter, side, url, ip, shard, streams, t_end)))
            await asyncio.sleep(STAGGER_S / len(specs))
    ref_name = a.ref.partition("=")[0]
    try:
        await asyncio.sleep(max(0.0, t_end - time.time()))
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    ready_ms = max(s.first_ms or 0 for s in sides.values())
    lo, hi = ready_ms + int(SKIP_START_S * 1000), int(t_end * 1000) - int(SKIP_END_S * 1000)
    res = {"pool": len(pool), "shards_per_entrance": len(shards), "ref": ref_name, "window_ms": [lo, hi], "window_s": round((hi - lo) / 1000), "pool_symbols_seen": {n: len({k[0] for k in s.keys}) for n, s in sides.items()},
           "entrances": {n: {"frames": s.frames, "keys": len(s.keys), "unparsed_book_frames": s.unparsed, "errors": s.errors, "ips": sorted(i for i in s.ips if i)} for n, s in sides.items()},
           "vs_ref": {n: compare(sides[ref_name], s, lo, hi) for n, s in sides.items() if n != ref_name}}
    os.makedirs(a.root, exist_ok=True)
    with open(os.path.join(a.root, "bookcmp.json"), "w") as f:
        json.dump(res, f, indent=1)
    print(json.dumps({n: {k: v for k, v in c.items() if k != "examples"} for n, c in res["vs_ref"].items()}, indent=1), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--pool-file")
    ap.add_argument("--pool", help="символы через запятую (вместо --pool-file)")
    ap.add_argument("--per-conn", type=int)
    ap.add_argument("--max-s", type=int, default=360)
    ap.add_argument("--ref", required=True, help="опорный вход ИМЯ=хост[@ip|@v4|@v6]")
    ap.add_argument("--entrance", action="append", required=True, help="сверяемый вход ИМЯ=хост[@ip|@v4|@v6]")
    a = ap.parse_args()

    async def amain():
        task = asyncio.current_task()
        asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, task.cancel)
        await main_async(a)

    asyncio.run(amain())


if __name__ == "__main__":
    sys.exit(main())
