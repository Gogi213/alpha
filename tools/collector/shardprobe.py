"""shardprobe POOL OUT SEC [ШАРДЫ] — TK-532: паузы прод-шардов trade02/mark02 привязаны к набору подписок (символы шарда) или к соединению/узлу? Свежие соединения (новые узлы биржи) с ТЕМ ЖЕ набором топиков,
что у шардов trade02/mark02 боевого коллектора, и контрольные trade01/mark01: по одному соединению IPv6 и IPv4 на шард. Пауза кадров > STALL_MS пишется строкой jsonl сразу:
время конца, длина, лаг (приём − ts биржи) первых кадров после паузы, состояние TCP внутри паузы. Раз в минуту строка hb. Наблюдение, не проверочный прогон (паузы редки: раз в десятки минут).
POOL — pool-<день>.json боевого коллектора (тот же набор символов, что у прода). Боевые соединения не трогает."""
import asyncio
import json
import os
import socket
import sys
import time

import orjson

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bybit_ws import TOPICS, WS_URL  # noqa: E402
from wscore.dial import peer_ip, tcp_state  # noqa: E402

HOST = "stream.bybit.com"
STALL_MS = 1500
TRACK = 20  # кадров после паузы, у которых меряется лаг


def now_utc():
    t = time.time()
    return time.strftime("%H:%M:%S", time.gmtime(t)) + f".{int(t % 1 * 1000):03d}"


async def one(label, fam, topics, end, log):
    import websockets

    loop = asyncio.get_running_loop()
    while time.monotonic() < end:
        sock = None
        try:
            f, typ, proto, _, addr = (await loop.getaddrinfo(HOST, 443, family=fam, type=socket.SOCK_STREAM))[0]
            sock = socket.socket(f, typ, proto)
            sock.setblocking(False)
            await loop.sock_connect(sock, addr)
            async with websockets.connect(WS_URL, sock=sock, server_hostname=HOST, max_size=None, ping_interval=None, compression=None, close_timeout=1) as ws:
                log({"conn": label, "ev": "connected", "ip": peer_ip(sock), "t": now_utc()})
                await ws.send(json.dumps({"op": "subscribe", "args": topics}))
                last, n, track, mid, lags = time.monotonic(), 0, 0, False, []
                stop = asyncio.Event()

                async def pinger():
                    while not stop.is_set():
                        t = time.monotonic()
                        try:
                            await asyncio.wait_for(await ws.ping(), 10)
                        except asyncio.TimeoutError:
                            log({"conn": label, "ev": "ping_timeout", "t": now_utc()})
                        rtt = (time.monotonic() - t) * 1000
                        if rtt > 700:
                            log({"conn": label, "ev": "slow_ping", "t": now_utc(), "rtt_ms": round(rtt)})
                        await asyncio.sleep(max(0, 1 - (time.monotonic() - t)))

                async def sampler():
                    nonlocal mid
                    while not stop.is_set():
                        await asyncio.sleep(0.25)
                        if time.monotonic() - last > 1.0:
                            if not mid:
                                mid = True
                                log({"conn": label, "ev": "inside", "t": now_utc(), "since_last_ms": round((time.monotonic() - last) * 1000), "tcp": tcp_state(sock)})
                        else:
                            mid = False

                tasks = [asyncio.create_task(pinger()), asyncio.create_task(sampler())]
                hb = time.monotonic() + 60
                try:
                    while time.monotonic() < end:
                        try:
                            m = await asyncio.wait_for(ws.recv(), 10)
                        except asyncio.TimeoutError:
                            log({"conn": label, "ev": "silent10s", "t": now_utc()})
                            continue
                        mono = time.monotonic()
                        gap = (mono - last) * 1000
                        last = mono
                        n += 1
                        if n > 3 and gap > STALL_MS:
                            track, lags = TRACK, []
                            stall = {"conn": label, "ev": "stall", "end": now_utc(), "gap_ms": round(gap), "tcp_after": tcp_state(sock)}
                        if track:
                            try:
                                d = orjson.loads(m)
                                if "ts" in d:
                                    lags.append(round(time.time() * 1000 - d["ts"]))
                                    track -= 1
                                    if not track:
                                        stall.update(lag_first=lags[0], lag_max=max(lags), lag_med=sorted(lags)[len(lags) // 2])
                                        log(stall)
                            except Exception:
                                pass
                        if mono > hb:
                            hb = mono + 60
                            log({"conn": label, "ev": "hb", "t": now_utc(), "frames": n, "tcp": tcp_state(sock)})
                finally:
                    stop.set()
                    await asyncio.gather(*tasks, return_exceptions=True)
        except Exception as e:  # noqa: BLE001 — диагностика: любое закрытие пишем и открываем заново
            log({"conn": label, "ev": "closed", "t": now_utc(), "err": repr(e)[:160]})
            await asyncio.sleep(5)
        finally:
            if sock is not None:
                sock.close()


async def main():
    pool_file, out, sec = sys.argv[1], sys.argv[2], float(sys.argv[3])
    pool = json.load(open(pool_file))["pool"]
    end = time.monotonic() + sec
    f = open(out, "a", buffering=1)

    def log(d):
        f.write(json.dumps(d, separators=(",", ":")) + "\n")

    shards = {}
    for kind in ("trade", "mark"):
        tp = [TOPICS[kind].format(x) for x in pool]  # раскладка боевого коллектора 12.11 до 03:10 UTC: подряд по 100 (после — равные куски, wscore.pool.balanced_chunks)
        for i in range(0, len(tp), 100):
            shards[f"{kind}{i // 100:02d}"] = tp[i : i + 100]
    jobs = []
    names = sys.argv[4].split(",") if len(sys.argv) > 4 else ("trade02", "mark02", "trade01", "mark01")
    for name in names:
        for lab, fam in (("v6", socket.AF_INET6), ("v4", socket.AF_INET)):
            jobs.append(one(f"{name}-{lab}", fam, shards[name], end, log))
            await asyncio.sleep(0.5)
    log({"ev": "start", "t": now_utc(), "pool": len(pool), "streams": {k: len(v) for k, v in shards.items()}})
    await asyncio.gather(*jobs)


asyncio.run(main())
